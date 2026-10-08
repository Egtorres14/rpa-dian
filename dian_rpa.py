#!/usr/bin/env python3
"""RPA DIAN: consulta cada CUFE, resuelve el CAPTCHA (Cloudflare Turnstile), descarga el PDF de
la representación gráfica, extrae los datos por OCR (un DataFrame/CSV por factura) y reporta
tiempos, métricas y restricciones del proceso.

Ejemplos:
  python dian_rpa.py dian --limite 1     prueba con la primera factura de cufes.json
  python dian_rpa.py dian                todas las facturas de cufes.json
  python dian_rpa.py extraer             sólo la parte 2, sobre los PDF ya descargados
"""
from __future__ import annotations

import argparse
import getpass
import hashlib
import json
import os
import re
import statistics
import sys
import time
import tracemalloc
import urllib.request
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from urllib.parse import urljoin

import pandas as pd
import pymupdf
from playwright.sync_api import Error as PlaywrightError, sync_playwright

from extraccion import COLUMNS, OCR, extraer_factura

BASE = Path(__file__).resolve().parent
DIAN_URL = "https://catalogo-vpfe.dian.gov.co/User/SearchDocument"
CAPTCHA_API = os.environ.get("CAPTCHA_API_URL", "https://api.2captcha.com/")
WIDGETS = ".cf-turnstile[data-sitekey], .g-recaptcha[data-sitekey]"
CAMPOS_TOKEN = "input[name='cf-turnstile-response'], textarea[name='g-recaptcha-response']"
SELECTORES = ("document_key", "search_nit", "search_button", "errors", "download",
              "dialog", "download_nit", "download_confirm")
BLOQUEO = re.compile(r"solicitud bloqueada|access denied|checking your browser|just a moment|"
                     r"attention required|you have been blocked", re.I)


class DetenerLote(RuntimeError):
    """Error que se repetiría en todas las facturas: se detiene el lote para no gastar CAPTCHA."""


class CaptchaError(DetenerLote):
    """Falta configuración, se agotó el proveedor o el sitio bloqueó la solicitud."""


class ControlNoEncontrado(DetenerLote):
    """Abrió la página del documento pero no se reconoce el control de descarga."""


class VerificacionPendiente(DetenerLote):
    """La verificación del documento no produjo un token utilizable para descargar."""


class CaptchaRechazado(RuntimeError):
    """El portal o el proveedor rechazaron el token; la factura se reintenta."""


class DocumentoNoDisponible(ValueError):
    """El portal respondió con un error del documento (CUFE o NIT); no se reintenta."""


class DescargaInvalida(RuntimeError):
    """Respuesta de descarga incompleta o distinta de PDF; admite reintento acotado."""


def clave_del_archivo(path: Path) -> str:
    """Lee sólo TWOCAPTCHA_API_KEY; no interpreta código ni expande variables."""
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except (OSError, UnicodeError):
        raise CaptchaError("No se pudo leer el archivo de clave en UTF-8.") from None
    values = []
    for line in lines:
        name, separator, value = line.partition("=")
        if not separator or name.strip() != "TWOCAPTCHA_API_KEY":
            continue
        value = value.strip()
        if value.startswith(("'", '"')):
            if len(value) < 2 or value[-1] != value[0]:
                raise CaptchaError("Comillas incompletas en el archivo de clave.")
            value = value[1:-1].strip()
        values.append(value)
    if len(values) != 1 or not values[0]:
        raise CaptchaError("El archivo debe contener una única TWOCAPTCHA_API_KEY con valor.")
    return values[0]


def obtener_clave(pedir: bool = False, env_file: Path | None = None) -> str:
    """Prioridad: entrada oculta, variable de entorno y archivo local .env."""
    if pedir:
        if not sys.stdin.isatty():
            raise CaptchaError("--pedir-clave necesita una terminal interactiva; en servidores usa TWOCAPTCHA_API_KEY.")
        return getpass.getpass("Clave de 2Captcha (entrada oculta): ").strip()
    if key := os.environ.get("TWOCAPTCHA_API_KEY", "").strip():
        return key
    path = env_file if env_file is not None else BASE / ".env"
    if path.is_file():
        return clave_del_archivo(path)
    if env_file is not None:
        raise CaptchaError("No existe el archivo indicado con --env-file.")
    return ""


def leer_cufes(path: Path, nit: str = "", sin_nit: bool = False) -> list[dict[str, str]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    output = []
    seen = set()
    for index, value in enumerate(data, 1):
        value = {"cufe": value} if isinstance(value, str) else value
        cufe = re.sub(r"\s+", "", str(value.get("cufe", ""))).lower()
        identifier = "" if sin_nit else str(value.get("nit") or nit).strip()
        if not re.fullmatch(r"[0-9a-f]{96}", cufe):
            raise ValueError(f"CUFE {index}: se esperaban 96 caracteres hexadecimales.")
        if cufe in seen:
            raise ValueError(f"CUFE {index}: duplicado.")
        if identifier and not re.fullmatch(r"\d{5,15}", identifier):
            raise ValueError(f"NIT {index}: usa sólo dígitos, sin puntos ni DV.")
        seen.add(cufe)
        output.append({"cufe": cufe, "nit": identifier})
    if not output:
        raise ValueError("El archivo CUFE está vacío.")
    return output


class TwoCaptcha:
    """API v2 de 2Captcha (createTask/getTaskResult). Sólo resuelve el widget; no evade bloqueos."""
    FATALES = {"ERROR_KEY_DOES_NOT_EXIST", "ERROR_WRONG_USER_KEY", "ERROR_ZERO_BALANCE",
               "ERROR_IP_NOT_ALLOWED", "ERROR_IP_BANNED", "ERROR_ACCOUNT_SUSPENDED"}
    INTERVALO_S = 5

    def __init__(self, key: str, timeout: int = 180, max_tasks: int = 25):
        self.key = key
        self.timeout = timeout
        self.max_tasks = max_tasks
        self.tasks = 0
        self.reported_cost = Decimal("0")
        self.details: list[dict] = []

    def _post(self, endpoint: str, payload: dict) -> dict:
        request = urllib.request.Request(CAPTCHA_API.rstrip("/") + "/" + endpoint,
                                         data=json.dumps(payload).encode(),
                                         headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=30) as response:
            data = json.load(response)
        if data.get("errorId"):
            # No se incluyen la clave, el token ni el cuerpo del request.
            code = str(data.get("errorCode", "ERROR"))
            error = CaptchaError if code in self.FATALES else CaptchaRechazado
            message = f"Proveedor CAPTCHA: {code} {data.get('errorDescription') or ''}".strip()
            if self.key:
                message = message.replace(self.key, "[clave oculta]")
            raise error(message)
        return data

    def resolver(self, task: dict) -> str:
        if not self.key:
            raise CaptchaError("Falta TWOCAPTCHA_API_KEY: configúrala o usa --captcha manual.")
        if self.tasks >= self.max_tasks:
            raise CaptchaError("Se alcanzó --max-captcha-tasks.")
        start = time.monotonic()
        answer = self._post("createTask", {"clientKey": self.key, "task": task})
        self.tasks += 1
        detail = {"tipo":task["type"], "estado":"pendiente", "consultas_resultado":0,
                  "coste_reportado_usd":"0", "tiempo_proveedor_s":None}
        self.details.append(detail)
        deadline = time.monotonic() + self.timeout
        try:
            while time.monotonic() < deadline:
                time.sleep(min(self.INTERVALO_S, max(0, deadline - time.monotonic())))
                detail["consultas_resultado"] += 1
                result = self._post("getTaskResult", {"clientKey": self.key, "taskId": answer["taskId"]})
                if result.get("status") == "ready":
                    cost = str(result.get("cost") or "0")
                    self.reported_cost += Decimal(cost)
                    detail["coste_reportado_usd"] = cost
                    if result.get("createTime") is not None and result.get("endTime") is not None:
                        detail["tiempo_proveedor_s"] = max(0, result["endTime"] - result["createTime"])
                    solution = result.get("solution", {})
                    token = solution.get("token") or solution.get("gRecaptchaResponse")
                    if not token:
                        raise CaptchaRechazado("El proveedor devolvió una solución sin token.")
                    detail["estado"] = "resuelto"
                    return token
            detail["estado"] = "timeout"
            raise CaptchaRechazado("Tiempo máximo del proveedor CAPTCHA agotado; puede existir cargo pendiente.")
        except Exception:
            if detail["estado"] == "pendiente":
                detail["estado"] = "error"
            raise
        finally:
            detail["tiempo_cliente_s"] = round(time.monotonic() - start, 6)


def visible(scope, selector: str):
    for locator in scope.locator(selector).all():
        if locator.is_visible():
            return locator
    return None


def textos_visibles(scope, selector: str) -> list[str]:
    textos = []
    for locator in scope.locator(selector).all():
        if locator.is_visible():
            texto = " ".join(locator.inner_text().split())
            if texto and texto not in textos:
                textos.append(texto)
    return textos


def clic(locator) -> None:
    """Clic normal; si otro elemento lo tapa (el widget fijo del CAPTCHA), clic por JavaScript."""
    try:
        locator.click(timeout=5000)
    except PlaywrightError:
        locator.evaluate("element => element.click()")


def abrir_navegador(pw, args):
    """Devuelve (página, cerrar) con Chromium de Playwright o el canal indicado (Chrome/Edge)."""
    options = {"headless": args.headless}
    if args.canal:
        options["channel"] = args.canal
    if args.browser_path:
        options["executable_path"] = args.browser_path
    browser = pw.chromium.launch(**options)
    context = browser.new_context(accept_downloads=True, locale="es-CO", viewport={"width": 1366, "height": 900})

    def cerrar():
        context.close()
        browser.close()
    return context.new_page(), cerrar


class CaptchaManager:
    """Token de Turnstile/reCAPTCHA: del propio widget, de 2Captcha o de una persona (diagnóstico)."""

    def __init__(self, mode: str, remote: TwoCaptcha, wait_s: float, manual_wait_s: float = 180):
        self.mode, self.remote, self.wait_s = mode, remote, wait_s
        self.manual_wait_s = manual_wait_s
        self.seconds = 0.0
        self.calls = 0
        self.providers: list[str] = []
        self.sitekey = ""
        self.intervenciones_solicitadas = 0

    def resolver(self, page, scope=None) -> None:
        scope = scope if scope is not None else page
        start = time.perf_counter()
        try:
            widgets, fields = scope.locator(WIDGETS), scope.locator(CAMPOS_TOKEN)
            if not widgets.count() and not fields.count():
                return
            widget = widgets.first if widgets.count() else None
            css = (widget.get_attribute("class") or "") if widget is not None else ""
            is_turnstile = "g-recaptcha" not in css and not scope.locator("textarea[name='g-recaptcha-response']").count()
            self.sitekey = (widget.get_attribute("data-sitekey") if widget is not None else None) or self.sitekey
            if self.mode != "2captcha":
                if self.mode == "manual":
                    self.intervenciones_solicitadas += 1
                    print("    CAPTCHA: resuélvelo en la ventana del navegador...", flush=True)
                token = self._token_del_widget(page, fields)
                if token:
                    self.calls += 1
                    self.providers.append("manual" if self.mode == "manual" else "widget")
                    return
                if self.mode == "gratis":
                    self.intervenciones_solicitadas += 1
                    print("    CAPTCHA gratuito: marca la casilla en el navegador; el robot continuará al recibir el token.", flush=True)
                    if self._token_del_widget(page, fields, self.manual_wait_s):
                        self.calls += 1
                        self.providers.append("widget_tras_aviso")
                        return
                    raise VerificacionPendiente("El widget no entregó un token en el tiempo de espera asistida. "
                                                "El modo gratis no utiliza 2Captcha.")
                if self.mode == "widget":
                    raise VerificacionPendiente("Turnstile no entregó el token automáticamente. "
                                                "Usa --captcha gratis para permitir intervención o --captcha 2captcha.")
                if self.mode == "manual":
                    raise CaptchaRechazado(f"No se resolvió el CAPTCHA en {self.wait_s:.0f} s (modo manual).")
                if not self.remote.key:
                    raise CaptchaError("Turnstile no entregó el token por sí solo y no hay TWOCAPTCHA_API_KEY. "
                                       "Configura la clave para usar 2Captcha.")
            if not self.sitekey:
                raise CaptchaError("No se encontró el data-sitekey del CAPTCHA; ajusta selectores.json.")
            task = {"type": "TurnstileTaskProxyless" if is_turnstile else "RecaptchaV2TaskProxyless",
                    "websiteURL": page.url, "websiteKey": self.sitekey}
            if widget is not None:
                if is_turnstile:
                    for source, dest in (("data-action", "action"), ("data-cdata", "data")):
                        if value := widget.get_attribute(source):
                            task[dest] = value
                elif widget.get_attribute("data-size") == "invisible":
                    task["isInvisible"] = True
            token = self.remote.resolver(task)
            self._inyectar(page, scope, widget, fields, token, is_turnstile)
            self.calls += 1
            self.providers.append("2captcha_turnstile" if is_turnstile else "2captcha_recaptcha_v2")
        finally:
            self.seconds += time.perf_counter() - start

    def _token_del_widget(self, page, fields, wait_s=None) -> str:
        deadline = time.monotonic() + (self.wait_s if wait_s is None else wait_s)
        while True:
            for index in range(fields.count()):
                value = fields.nth(index).input_value()
                if len(value) > 20:
                    return value
            if time.monotonic() >= deadline:
                return ""
            page.wait_for_timeout(250)

    @staticmethod
    def _inyectar(page, scope, widget, fields, token: str, is_turnstile: bool) -> None:
        """Pone el token en el campo oculto del widget, sin desactivar validaciones ni CSRF."""
        deadline = time.monotonic() + 10
        while not fields.count() and time.monotonic() < deadline:
            page.wait_for_timeout(250)  # El widget crea su campo al terminar de cargar.
        if not fields.count():
            container = widget if widget is not None else scope.locator("form").first
            container.evaluate("""(element, name) => {
                const field = document.createElement(name.startsWith('g-') ? 'textarea' : 'input');
                if (field.tagName === 'INPUT') field.type = 'hidden'; else field.style.display = 'none';
                field.name = name;
                element.appendChild(field);
            }""", "cf-turnstile-response" if is_turnstile else "g-recaptcha-response")
        fields.first.evaluate("""(field, token) => {
            field.value = token;
            field.dispatchEvent(new Event('input', {bubbles: true}));
            field.dispatchEvent(new Event('change', {bubbles: true}));
        }""", token)
        for index in range(1, fields.count()):
            fields.nth(index).evaluate("field => { field.disabled = true; }")  # Widget renderizado dos veces.
        callback = widget.get_attribute("data-callback") if widget is not None else None
        if callback:
            page.evaluate("""({token, callback}) => {
                const fn = callback.split('.').reduce((v, k) => v && v[k], window);
                if (typeof fn === 'function') fn(token);
            }""", {"token": token, "callback": callback})


def es_pdf(path: Path) -> bool:
    """Cabecera %PDF- (también la tienen los PDF cifrados). No prueba contraseñas."""
    try:
        with path.open("rb") as stream:
            return stream.read(1024).lstrip().startswith(b"%PDF-")
    except OSError:
        return False


def token_presente(fields) -> bool:
    """¿Algún campo de respuesta del widget ya tiene su token?"""
    return any(len(fields.nth(i).input_value()) > 20 for i in range(fields.count()))


def pdf_cifrado(path: Path) -> bool:
    try:
        with pymupdf.open(path) as doc:
            return bool(doc.needs_pass)
    except (OSError, RuntimeError, ValueError):
        return False


def pdf_valido(path: Path, passwords=()) -> bool:
    """Un PDF con cabecera %PDF-, al menos una página y descifrable (con alguna de `passwords`)."""
    try:
        with path.open("rb") as stream:
            if not stream.read(1024).lstrip().startswith(b"%PDF-"):
                return False
        with pymupdf.open(path) as doc:
            if len(doc) == 0:
                return False
            if not doc.needs_pass:
                return True
            return any(password and doc.authenticate(str(password)) for password in passwords)
    except (OSError, RuntimeError, ValueError):
        return False


def guardar_diagnostico(page, carpeta: Path, nombre: str) -> None:
    """Captura y HTML de la página para ajustar selectores sin repetir la consulta."""
    if page is None or page.is_closed():
        return
    try:
        carpeta.mkdir(parents=True, exist_ok=True)
        (carpeta / f"{nombre}.html").write_text(f"<!-- {page.url} -->\n" + page.content(), encoding="utf-8")
        page.screenshot(path=str(carpeta / f"{nombre}.png"), full_page=True)
    except Exception:
        pass


AVISO_RECHAZO = re.compile(r"campo de seguridad|captcha|verifique|no se pudo generar|error al generar", re.I)


class DianRPA:
    def __init__(self, page, url: str, selectors: dict, captcha: CaptchaManager, timeout_ms: int,
                 verificacion_s: float = 120, headless: bool = False):
        self.page, self.url, self.selectors, self.captcha = page, url, selectors, captcha
        self.timeout = timeout_ms
        self.verificacion_s, self.headless = verificacion_s, headless

    def esperar_verificacion(self) -> None:
        """Resuelve el CAPTCHA propio del documento antes de pulsar «Descargar PDF».

        El token de la búsqueda ya se consumió: la descarga necesita otro token, con la
        sitekey de esta página. El JavaScript del portal lo copia al campo `captcha` del POST.
        """
        if not self.page.locator(CAMPOS_TOKEN).count() and not self.page.locator(WIDGETS).count():
            return
        print(f"    CAPTCHA de descarga: {self.captcha.mode}...", flush=True)
        wait_anterior = self.captcha.wait_s
        try:
            if self.captcha.mode == "manual":
                self.captcha.wait_s = self.verificacion_s
            self.captcha.resolver(self.page)
        finally:
            self.captcha.wait_s = wait_anterior
        if not token_presente(self.page.locator(CAMPOS_TOKEN)):
            raise VerificacionPendiente("El CAPTCHA del documento no entregó un token para descargar el PDF.")

    def _errores(self) -> list[str]:
        """Mensajes visibles del portal: validaciones ASP.NET, modal de error o alertas."""
        if BLOQUEO.search(self.page.locator("body").inner_text(timeout=5000)):
            raise CaptchaError("El sitio bloqueó la solicitud (Cloudflare/WAF); no se evade. Se detiene el lote.")
        return textos_visibles(self.page, self.selectors["errors"])

    def consultar(self, task: dict) -> None:
        self.page.goto(self.url, wait_until="domcontentloaded", timeout=self.timeout)
        self._errores()
        self.page.locator(self.selectors["document_key"]).fill(task["cufe"])
        # Al escribir el CUFE el portal muestra "NIT del Emisor o Receptor"; sólo se llena si hay NIT.
        nit = visible(self.page, self.selectors["search_nit"])
        if nit is not None and task["nit"]:
            nit.fill(task["nit"])
        self.captcha.resolver(self.page)
        clic(self.page.locator(self.selectors["search_button"]))
        deadline = time.monotonic() + self.timeout / 1000
        outside_since = None
        last_error = None
        while time.monotonic() < deadline:
            try:
                if visible(self.page, self.selectors["download"]) is not None:
                    return
                if errores := self._errores():
                    message = " | ".join(errores)
                    if re.search(r"captcha|token|robot|turnstile|verificaci[oó]n", message, re.I):
                        raise CaptchaRechazado("El portal rechazó la verificación: " + message)
                    raise DocumentoNoDisponible("Respuesta del portal: " + message)
                # Sin formulario ya es la página del documento: si no aparece la descarga, falta un selector.
                if self.page.locator(self.selectors["document_key"]).count():
                    outside_since = None
                else:
                    outside_since = outside_since or time.monotonic()
                    if time.monotonic() - outside_since > 15:
                        raise ControlNoEncontrado("Abrió la página del documento pero no se reconoce el botón de "
                                                  "descarga. Ajusta 'download' en selectores.json con el HTML de diagnostico/.")
            except PlaywrightError as exc:
                last_error = exc  # La página está navegando; se revisa en el siguiente ciclo.
            self.page.wait_for_timeout(250)
        detail = f" Último error: {last_error}" if last_error else ""
        raise TimeoutError("El portal no mostró el documento a tiempo (revisa diagnostico/)." + detail)

    def descargar(self, task: dict, path: Path) -> None:
        control = visible(self.page, self.selectors["download"])
        if control is None:
            raise ControlNoEncontrado("No se encontró el control de descarga.")
        self.esperar_verificacion()
        href = control.get_attribute("href") or ""
        if href and not href.startswith(("#", "javascript")):
            # Descarga directa con las cookies de la sesión del navegador.
            response = self.page.context.request.get(urljoin(self.page.url, href), timeout=self.timeout,
                                                     headers={"Referer": self.page.url})
            body = response.body()
            if response.ok and body[:1024].lstrip().startswith(b"%PDF-"):
                path.write_bytes(body)
                return
        self._descargar_con_clic(task, control, path)

    def _descargar_con_clic(self, task: dict, control, path: Path) -> None:
        downloads, responses, popup_pages = [], [], []

        def on_download(download):
            downloads.append(download)

        def on_response(response):
            if "application/pdf" in response.headers.get("content-type", "").lower():
                responses.append(response)

        def on_page(new_page):
            popup_pages.append(new_page)
            new_page.on("download", on_download)
            new_page.on("response", on_response)

        self.page.on("download", on_download)
        self.page.on("response", on_response)
        self.page.context.on("page", on_page)
        try:
            clic(control)
            deadline = time.monotonic() + self.timeout / 1000
            confirmed = False
            while time.monotonic() < deadline:
                if downloads:
                    downloads[0].save_as(path)
                    break
                if responses:
                    response = responses.pop(0)
                    try:
                        body = response.body()
                        if response.ok and body[:1024].lstrip().startswith(b"%PDF-"):
                            path.write_bytes(body)
                            break
                        # Chromium puede notificar los encabezados antes de que el
                        # body esté disponible en Playwright. El blob del portal
                        # todavía puede producir una descarga completa y válida.
                    except PlaywrightError:
                        pass  # Se espera la descarga generada por el navegador.
                if errores := self._errores():
                    raise DocumentoNoDisponible("Respuesta del portal al descargar: " + " | ".join(errores))
                dialog = visible(self.page, self.selectors["dialog"])
                if dialog is not None:
                    titulo = visible(dialog, ".sub-title")  # Sólo el mensaje, sin el texto del botón.
                    aviso = " ".join((titulo or dialog).inner_text().split())
                    if AVISO_RECHAZO.search(aviso):
                        # Aviso de error (verificación incompleta o PDF no generado): no es la confirmación.
                        raise CaptchaRechazado("La página rechazó la descarga: " + aviso)
                    if not confirmed:
                        # Aviso informativo: el PDF viene con contraseña (el NIT digitado). Se acepta.
                        field = visible(dialog, self.selectors["download_nit"])
                        if field is not None:
                            if not task["nit"]:
                                raise DocumentoNoDisponible("La descarga exige el NIT del emisor/receptor y no fue suministrado.")
                            field.fill(task["nit"])
                        button = visible(dialog, self.selectors["download_confirm"])
                        if button is None:
                            raise ControlNoEncontrado("Aviso de descarga sin botón «Aceptar» reconocible. Ajusta selectores.json.")
                        clic(button)
                        confirmed = True
                self.page.wait_for_timeout(150)
            else:
                raise TimeoutError("No se recibió el PDF tras el clic de descarga.")
            if not es_pdf(path):
                longitud = path.stat().st_size if path.exists() else 0
                path.unlink(missing_ok=True)
                raise DescargaInvalida(f"La descarga no contiene un PDF válido ({longitud} bytes).")
        finally:
            self.page.remove_listener("download", on_download)
            self.page.remove_listener("response", on_response)
            self.page.context.remove_listener("page", on_page)
            for popup in popup_pages:
                if not popup.is_closed():
                    popup.close()


def guardar_csv(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # BOM UTF-8 para Excel. Coma delimitadora; números con punto decimal.
    frame.to_csv(path, index=False, encoding="utf-8-sig", lineterminator="\n")


def descargar_con_reintentos(rpa: DianRPA, task: dict, pdf_path: Path, record: dict, args, stem: str) -> None:
    for attempt in range(1, args.attempts + 1):
        record["intentos"] = attempt
        stage, t = "consulta_s", time.perf_counter()
        try:
            rpa.consultar(task)
            record[stage] += time.perf_counter() - t
            stage, t = "descarga_s", time.perf_counter()
            rpa.descargar(task, pdf_path)
            record[stage] += time.perf_counter() - t
            return
        except Exception as exc:
            record[stage] += time.perf_counter() - t
            guardar_diagnostico(rpa.page, args.out / "diagnostico", f"{stem}_intento{attempt}")
            print(f"    intento {attempt}: {type(exc).__name__}: {exc}", flush=True)
            if rpa.page.is_closed():
                raise DetenerLote("Se cerró el navegador.") from exc
            if not isinstance(exc, (PlaywrightError, OSError, CaptchaRechazado, DescargaInvalida)) or attempt == args.attempts:
                raise
            time.sleep(min(2 ** attempt, 8))


ORDEN_METRICAS = ["archivo", "cufe", "nit_consulta", "origen", "estado", "intentos", "consulta_s",
                  "captcha_s", "descarga_s", "render_png_s", "lectura_ocr_s", "parseo_s", "extraccion_s",
                  "csv_s", "total_s", "cpu_python_s", "captcha_resueltos", "captcha_proveedores",
                  "captcha_tareas_remotas", "captcha_coste_reportado_usd", "numero_factura", "fecha_emision",
                  "nit_emisor", "paginas", "paginas_ocr", "fuente_texto", "separador_decimal", "productos",
                  "campos_presentes", "campos_esperados", "cobertura_campos_pct", "confianza_ocr_media",
                  "campos_comparados_ocr_pdf", "concordancia_ocr_pdf_pct", "sha256_pdf", "advertencias", "error"]


def tabla_metricas(records: list[dict]) -> pd.DataFrame:
    frame = pd.DataFrame(records)
    return frame[[c for c in ORDEN_METRICAS if c in frame] + [c for c in frame if c not in ORDEN_METRICAS]]


def ejecutar_lote(args, tasks: list[dict], ocr: OCR, page=None) -> tuple[dict, list[dict]]:
    """Procesa las facturas en orden. Devuelve {nombre_pdf: DataFrame} y las métricas por factura."""
    frames_por_archivo: dict[str, pd.DataFrame] = {}
    records = []
    for folder in ("pdf", "csv", "diagnostico"):
        (args.out / folder).mkdir(parents=True, exist_ok=True)
    remote = captcha = rpa = None
    if page is not None:
        remote = TwoCaptcha(args.captcha_key, args.captcha_timeout, args.max_captcha_tasks)
        captcha = CaptchaManager(args.captcha, remote, args.captcha_wait, args.verificacion_espera)
        rpa = DianRPA(page, args.url, args.selectores, captcha, args.timeout * 1000,
                      verificacion_s=args.verificacion_espera, headless=args.headless)
    stopped = ""
    for index, task in enumerate(tasks, 1):
        stem = Path(task["pdf"]).stem if "pdf" in task else f"factura_{index:02d}_{task['cufe'][:12]}"
        pdf_path = Path(task["pdf"]) if "pdf" in task else args.out / "pdf" / f"{stem}.pdf"
        start, cpu_start = time.perf_counter(), time.process_time()
        captcha_start = (captcha.seconds, captcha.calls, len(captcha.providers), captcha.intervenciones_solicitadas) if captcha else (0.0, 0, 0, 0)
        remote_start = (remote.tasks, remote.reported_cost, len(remote.details)) if remote else (0, Decimal("0"), 0)
        record = {"archivo": pdf_path.name, "cufe": task.get("cufe", ""), "nit_consulta": task.get("nit", ""),
                  "origen": args.command, "estado": "ERROR", "intentos": 0, "consulta_s": 0.0, "descarga_s": 0.0}
        frame = pd.DataFrame(columns=COLUMNS)
        print(f"[{index}/{len(tasks)}] {pdf_path.name}", flush=True)
        # Contraseñas candidatas para un PDF cifrado: NIT de la factura y NIT global (--nit/--password).
        passwords = [p for p in (task.get("nit"), args.password) if p]
        try:
            if stopped:
                record["estado"] = "OMITIDA"
                raise DetenerLote(f"No se consultó: el lote se detuvo antes ({stopped}).")
            if rpa and args.resume and pdf_valido(pdf_path, passwords):
                record["origen"] = "pdf_existente"
            elif rpa:
                descargar_con_reintentos(rpa, task, pdf_path, record, args, stem)
            if not pdf_valido(pdf_path, passwords):
                if pdf_cifrado(pdf_path):
                    raise RuntimeError(f"PDF cifrado y la contraseña no coincide (probadas: {', '.join(passwords) or 'ninguna'}). "
                                       "En la DIAN la contraseña es el NIT: usa --nit o --password.")
                raise RuntimeError("No existe un PDF válido para extraer.")
            frame, stats = extraer_factura(pdf_path, args.out, ocr, cufe=task.get("cufe", ""),
                                           usar_ocr=not args.sin_ocr, dpi=args.dpi,
                                           decimal_separator=args.decimal_separator, passwords=passwords)
            warnings = stats.pop("advertencias")
            record.update(stats, advertencias="; ".join(warnings),
                          sha256_pdf=hashlib.sha256(pdf_path.read_bytes()).hexdigest())
            record["estado"] = "REVISAR" if warnings or not len(frame) or stats["cobertura_campos_pct"] < 100 else "OK"
        except Exception as exc:
            record["error"] = f"{type(exc).__name__}: {exc}"
            if isinstance(exc, DetenerLote) and not stopped:
                stopped = f"{type(exc).__name__} en {pdf_path.name}"
        finally:
            ocr.limpiar_imagenes()
        t = time.perf_counter()
        guardar_csv(frame, args.out / "csv" / f"{stem}.csv")
        frames_por_archivo[pdf_path.name] = frame
        record.update(captcha_s=round(captcha.seconds - captcha_start[0], 6) if captcha else 0.0,
                      captcha_resueltos=captcha.calls - captcha_start[1] if captcha else 0,
                      captcha_intervenciones_solicitadas=captcha.intervenciones_solicitadas - captcha_start[3] if captcha else 0,
                      captcha_proveedores=", ".join(captcha.providers[captcha_start[2]:]) if captcha else "",
                      captcha_tareas=remote.details[remote_start[2]:] if remote else [],
                      captcha_tareas_remotas=remote.tasks - remote_start[0] if remote else 0,
                      captcha_coste_reportado_usd=str(remote.reported_cost - remote_start[1]) if remote else "0",
                      consulta_s=round(record["consulta_s"], 6), descarga_s=round(record["descarga_s"], 6),
                      csv_s=round(time.perf_counter() - t, 6), total_s=round(time.perf_counter() - start, 6),
                      cpu_python_s=round(time.process_time() - cpu_start, 6))
        records.append(record)
        detail = record.get("error") or record.get("advertencias") or ""
        print(f"    {record['estado']} ({record['total_s']:.1f} s){' - ' + detail if detail else ''}", flush=True)
        guardar_csv(tabla_metricas(records), args.out / "metricas.csv")  # Parcial, por si se interrumpe.
        if rpa and not stopped and index < len(tasks) and args.delay:
            time.sleep(args.delay)
    with_rows = [f for f in frames_por_archivo.values() if len(f)]
    combined = pd.concat(with_rows, ignore_index=True) if with_rows else pd.DataFrame(columns=COLUMNS)
    guardar_csv(combined, args.out / "facturas_consolidadas.csv")
    (args.out / "detalle_proceso.json").write_text(json.dumps(records, ensure_ascii=False, indent=2, default=str),
                                                    encoding="utf-8")
    return frames_por_archivo, records


def resumir(args, records: list[dict], frames: dict, started: datetime, elapsed: float,
            python_peak_mib: float, ocr: OCR) -> dict:
    processed = [r["total_s"] for r in records if r["estado"] != "OMITIDA"]

    def mean_of(key):
        values = [r[key] for r in records if r.get(key) not in (None, "")]
        return round(statistics.mean(values), 2) if values else None

    solved = sum(r["captcha_resueltos"] for r in records)
    captcha_s = sum(r["captcha_s"] for r in records)
    return {"modo": args.command, "inicio_utc": started.isoformat(timespec="seconds"),
            "fin_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "facturas_solicitadas": len(records),
            "ok": sum(r["estado"] == "OK" for r in records),
            "revisar": sum(r["estado"] == "REVISAR" for r in records),
            "errores": sum(r["estado"] == "ERROR" for r in records),
            "omitidas": sum(r["estado"] == "OMITIDA" for r in records),
            "tiempo_total_s": round(elapsed, 3),
            "tiempo_factura_medio_s": round(statistics.mean(processed), 3) if processed else 0,
            "tiempo_factura_p95_s": round(float(pd.Series(processed).quantile(.95)), 3) if processed else 0,
            "tiempo_consulta_total_s": round(sum(r["consulta_s"] for r in records), 3),
            "tiempo_captcha_total_s": round(captcha_s, 3),
            "tiempo_captcha_medio_por_token_s": round(captcha_s / solved, 3) if solved else None,
            "tiempo_descarga_total_s": round(sum(r["descarga_s"] for r in records), 3),
            "tiempo_ocr_total_s": round(sum(r.get("lectura_ocr_s", 0) for r in records), 3),
            "tiempo_parseo_total_s": round(sum(r.get("parseo_s", 0) for r in records), 3),
            "facturas_ok_por_minuto": round(sum(r["estado"] == "OK" for r in records) * 60 / elapsed, 3) if elapsed else 0,
            "paginas": sum(r.get("paginas", 0) for r in records),
            "paginas_ocr": sum(r.get("paginas_ocr", 0) for r in records),
            "filas_productos": sum(len(f) for f in frames.values()),
            "cobertura_campos_media_pct": mean_of("cobertura_campos_pct"),
            "confianza_ocr_media": mean_of("confianza_ocr_media"),
            "concordancia_ocr_pdf_media_pct": mean_of("concordancia_ocr_pdf_pct"),
            "memoria_pico_python_mib": round(python_peak_mib, 3),
            "ocr_workers": ocr.workers,
            "tesseract_hilos_por_proceso": 1,
            "dpi": args.dpi,
            "pausa_entre_facturas_s": args.delay if args.command == "dian" else 0,
            "captcha_resueltos": solved,
            "captcha_modo": args.captcha if args.command == "dian" else "sin_captcha",
            "captcha_intervenciones_solicitadas": sum(r["captcha_intervenciones_solicitadas"] for r in records),
            "captcha_tareas_remotas": sum(r["captcha_tareas_remotas"] for r in records),
            "captcha_coste_reportado_usd": str(sum(Decimal(r["captcha_coste_reportado_usd"]) for r in records)),
            "advertencias_ocr": ocr.warnings,
            "nota_tiempos": "consulta_s y descarga_s incluyen la espera del CAPTCHA (captcha_s). Excluye instalación.",
            "nota_memoria": "Sólo asignaciones Python (tracemalloc); excluye navegador y Tesseract.",
            "nota_precision": "Concordancia = campos OCR iguales a la capa de texto del PDF (cuando existe)."}


def escribir_reporte(path: Path, args, summary: dict, records: list[dict]) -> None:
    def num(value, digits=2):
        return "" if value in (None, "") else f"{float(value):.{digits}f}"

    def nd(value, suffix=""):
        return "n/d" if value is None else f"{value}{suffix}"

    lines = ["# Reporte de ejecución - RPA DIAN + OCR", "",
             f"- Modo `{summary['modo']}`, inicio {summary['inicio_utc']}, fin {summary['fin_utc']} (UTC).",
             f"- Facturas: {summary['facturas_solicitadas']} solicitadas, {summary['ok']} OK, "
             f"{summary['revisar']} REVISAR, {summary['errores']} ERROR, {summary['omitidas']} OMITIDAS.",
             "", "## Tiempos por factura (segundos)", "",
             "| # | Archivo | Estado | Consulta | CAPTCHA | Descarga | PNG | OCR | Parseo | CSV | Total |",
             "|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for index, r in enumerate(records, 1):
        lines.append(f"| {index} | {r['archivo']} | {r['estado']} | {num(r['consulta_s'])} | {num(r['captcha_s'])} | "
                     f"{num(r['descarga_s'])} | {num(r.get('render_png_s'))} | {num(r.get('lectura_ocr_s'))} | "
                     f"{num(r.get('parseo_s'))} | {num(r['csv_s'], 3)} | {num(r['total_s'])} |")
    lines += ["", "## Métricas del proceso", "",
              f"- Tiempo total {summary['tiempo_total_s']:.1f} s; por factura: media {summary['tiempo_factura_medio_s']:.1f} s, "
              f"p95 {summary['tiempo_factura_p95_s']:.1f} s; {summary['facturas_ok_por_minuto']} facturas OK por minuto.",
              f"- Por etapa (total): consulta {summary['tiempo_consulta_total_s']:.1f} s, CAPTCHA de búsqueda y descarga "
              f"{summary['tiempo_captcha_total_s']:.1f} s (incluido en consulta y descarga), "
              f"descarga {summary['tiempo_descarga_total_s']:.1f} s, OCR {summary['tiempo_ocr_total_s']:.1f} s, "
              f"parseo {summary['tiempo_parseo_total_s']:.1f} s.",
              f"- Páginas {summary['paginas']} ({summary['paginas_ocr']} con OCR); filas de producto {summary['filas_productos']}.",
              f"- Calidad: cobertura media de campos {nd(summary['cobertura_campos_media_pct'], '%')}, confianza OCR media "
              f"{nd(summary['confianza_ocr_media'])}, concordancia OCR vs capa de texto "
              f"{nd(summary['concordancia_ocr_pdf_media_pct'], '%')}.",
              f"- Memoria pico (sólo Python): {summary['memoria_pico_python_mib']} MiB."]
    if summary["modo"] == "dian":
        lines.append(f"- CAPTCHA: {summary['captcha_resueltos']} tokens, {summary['captcha_tareas_remotas']} tareas en 2Captcha, "
                     f"media {nd(summary['tiempo_captcha_medio_por_token_s'], ' s')} por token, coste reportado USD "
                     f"{summary['captcha_coste_reportado_usd']}.")
        lines.append(f"- Modalidad: {args.captcha}; avisos de intervención: {summary['captcha_intervenciones_solicitadas']}. "
                     "La espera asistida está incluida en CAPTCHA y en el total; no es tiempo automático del proveedor.")
    lines += ["", "## Restricciones del proceso", ""]
    if summary["modo"] == "dian":
        lines += ["- CAPTCHA: el portal usa Cloudflare Turnstile. No es una imagen de texto, así que no se resuelve con OCR: "
                  "el modo 2captcha usa un servicio externo; gratis permite intervención y widget sólo espera al navegador. "
                  f"Los tokens caducan a los 300 s. Esta ejecución usó: {args.captcha}.",
                  "- NIT: el formulario pide «NIT del Emisor o Receptor» al escribir el CUFE. El enunciado sólo entrega CUFE; "
                  "si el portal lo exige, hace falta el NIT correcto de cada factura (no se deduce del CUFE, que es un hash SHA-384).",
                  "- Segunda verificación: la página del documento tiene su propio CAPTCHA Turnstile. Se resuelve con "
                  "el mismo modo configurado, usando su propia sitekey y un token nuevo; se acepta el aviso de contraseña "
                  "y el portal envía su formulario POST con CAPTCHA, cookies y protección CSRF.",
                  f"- Ritmo: ejecución secuencial, pausa de {args.delay:g} s entre facturas, {args.attempts} intentos por factura y "
                  f"tope de {args.max_captcha_tasks} tareas CAPTCHA, para no saturar el portal ni gastar de más.",
                  "- Dependencia del sitio: si la DIAN cambia su HTML, hay que ajustar selectores.json (el proceso guarda "
                  "HTML y captura en diagnostico/ cuando falla)."]
    lines += [f"- OCR: Tesseract ({args.ocr_lang}) a {args.dpi} DPI. Depende de la calidad de la imagen; los campos con baja "
              "confianza o distintos de la capa de texto del PDF quedan en estado REVISAR.",
              "- Formato: la extracción se apoya en los títulos de sección y rótulos de columna de la representación gráfica; "
              "un formato distinto requiere ajustar las reglas de extraccion.py.",
              "- Memoria: tracemalloc sólo mide Python (no el navegador ni Tesseract)."]
    lines += [f"- Paralelismo OCR: hasta {args.ocr_workers} lecturas de páginas o celdas, un hilo interno por proceso "
              "Tesseract. Los tiempos OCR son de pared; no suman trabajos simultáneos. PyMuPDF y las consultas DIAN "
              "se mantienen secuenciales. La caché conserva hasta dos imágenes decodificadas por factura."]
    incidents = [r for r in records if r["estado"] != "OK"]
    if incidents:
        lines += ["", "## Incidencias", ""]
        lines += [f"- {r['archivo']} ({r['estado']}): {r.get('error') or r.get('advertencias')}" for r in incidents]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    check = sub.add_parser("verificar-captcha", help="comprueba clave y saldo positivo sin crear tareas ni gastar CAPTCHA")
    check.add_argument("--pedir-clave", action="store_true", help="introduce la clave con entrada oculta")
    check.add_argument("--env-file", type=Path, help="archivo de clave; por defecto .env junto al programa")
    for name, help_text in (("dian", "consulta la DIAN, descarga los PDF y extrae los datos"),
                            ("extraer", "sólo la parte 2: extrae datos de PDF ya descargados")):
        p = sub.add_parser(name, help=help_text)
        p.add_argument("--out", type=Path, default=BASE / ("resultados_" + name), help="carpeta de resultados")
        p.add_argument("--input", type=Path, default=BASE / "cufes.json", help="lista de CUFE (y NIT opcional)")
        p.add_argument("--sin-ocr", action="store_true",
                       help="usa la capa de texto del PDF en vez del OCR de las imágenes")
        p.add_argument("--dpi", type=int, default=400)
        p.add_argument("--tesseract", default="tesseract", help="nombre o ruta del ejecutable de Tesseract")
        p.add_argument("--ocr-lang", default="spa+eng")
        p.add_argument("--ocr-timeout", type=int, default=120)
        p.add_argument("--ocr-workers", type=int, default=4,
                       help="lecturas de celdas simultáneas (1 a 16; predeterminado 4)")
        p.add_argument("--decimal-separator", choices=["auto", ",", "."], default="auto",
                       help="separador decimal de los PDF; auto lo detecta en cada factura")
        p.add_argument("--password", default="", help="contraseña del PDF cifrado (en la DIAN, el NIT); "
                       "se prueba junto al NIT de cada factura")
        if name == "extraer":
            p.add_argument("--pdf-dir", type=Path, default=BASE / "resultados_dian" / "pdf")
            p.add_argument("--demo", action="store_true", help="usa los diez PDF incluidos en muestras/pdf, sin Internet ni CAPTCHA")
            p.add_argument("--nit", default="", help="NIT para descifrar los PDF si no está en cufes.json")
            continue
        p.add_argument("--nit", default="", help="NIT común (sin puntos ni DV); el nit de cada registro tiene prioridad")
        p.add_argument("--sin-nit", action="store_true", help="no envía NIT aunque cufes.json lo traiga")
        p.add_argument("--limite", type=int, default=0, help="procesa sólo las primeras N facturas (0 = todas)")
        p.add_argument("--resume", action="store_true", help="no vuelve a descargar los PDF válidos ya guardados")
        p.add_argument("--url", default=DIAN_URL)
        p.add_argument("--selectors", type=Path, default=BASE / "selectores.json")
        p.add_argument("--captcha", choices=["auto", "2captcha", "gratis", "widget", "manual"], default="auto",
                       help="2captcha: servicio de pago. auto: usa el token si el widget lo entrega solo y, si no, "
                            "2Captcha. gratis: widget con intervención si hace falta, nunca paga. "
                            "widget: sólo token automático sin pagar. manual: diagnóstico asistido")
        p.add_argument("--pedir-clave", action="store_true", help="introduce la clave de 2Captcha con entrada oculta")
        p.add_argument("--env-file", type=Path, help="archivo de clave; por defecto .env junto al programa")
        p.add_argument("--captcha-wait", type=float, help="espera inicial del widget: auto 8, gratis 2, widget 15, manual 180 segundos")
        p.add_argument("--captcha-timeout", type=int, default=180)
        p.add_argument("--max-captcha-tasks", type=int, default=25)
        p.add_argument("--timeout", type=int, default=45, help="segundos máximos por espera del portal")
        p.add_argument("--attempts", type=int, default=2)
        p.add_argument("--delay", type=float, default=1, help="pausa entre facturas, en segundos (predeterminado 1)")
        p.add_argument("--verificacion-espera", type=float, default=120,
                       help="espera asistida en modo gratis (ambos CAPTCHA) o manual (documento)")
        p.add_argument("--headless", action="store_true",
                       help="sin ventana; compatible con auto, 2captcha y widget")
        p.add_argument("--canal", choices=["chrome", "msedge"], help="usa Chrome o Edge instalados en vez de Chromium")
        p.add_argument("--browser-path", default="", help="ruta a un ejecutable Chromium")
    args = parser.parse_args()
    if args.command == "verificar-captcha":
        try:
            key = obtener_clave(args.pedir_clave, args.env_file)
            if not key:
                raise CaptchaError("Configura TWOCAPTCHA_API_KEY o usa --pedir-clave.")
            start = time.perf_counter()
            result = TwoCaptcha(key)._post("getBalance", {"clientKey":key})
            print(json.dumps({"clave_valida":True, "saldo_positivo":Decimal(str(result["balance"])) > 0,
                              "tareas_creadas":0, "consulta_s":round(time.perf_counter()-start,3)},indent=2))
            return 0
        except (CaptchaError, CaptchaRechazado, OSError, ValueError) as exc:
            parser.error(str(exc))
        except (KeyError, ArithmeticError):
            parser.error("Respuesta inesperada de 2Captcha: no incluye un saldo numérico.")
    if args.dpi < 100:
        parser.error("--dpi debe ser >= 100.")
    if not 1 <= args.ocr_workers <= 16:
        parser.error("--ocr-workers debe estar entre 1 y 16.")
    args.out = args.out.resolve()

    ocr = OCR(args.tesseract, args.ocr_lang, args.ocr_timeout, args.ocr_workers)
    try:
        ocr.comprobar()
    except RuntimeError as exc:
        if not args.sin_ocr:
            parser.error(f"Tesseract no está disponible: {exc}. Instálalo (ver README.md) o usa --sin-ocr.")
    if args.command == "extraer":
        if args.demo:
            args.pdf_dir = BASE / "muestras" / "pdf"
        if not args.pdf_dir.is_dir():
            parser.error(f"No existe la carpeta {args.pdf_dir}; usa --pdf-dir.")
        registros = leer_cufes(args.input, args.nit) if args.input.exists() else []
        por_cufe = {t["cufe"]: t for t in registros}            # nombre = CUFE completo (descarga DIAN)
        por_prefijo = {t["cufe"][:12]: t for t in registros}    # nombre = factura_NN_<12hex>
        tasks = []
        for path in sorted(args.pdf_dir.glob("*.pdf")):
            stem = path.stem.lower()
            completo = re.search(r"[0-9a-f]{96}", stem)
            prefijo = re.search(r"_([0-9a-f]{12})$", stem)
            registro = {}
            if completo:
                registro = por_cufe.get(completo[0], {"cufe": completo[0]})
            elif prefijo and prefijo[1] in por_prefijo:
                registro = por_prefijo[prefijo[1]]
            tasks.append({"pdf": str(path), "cufe": registro.get("cufe", ""),
                          "nit": registro.get("nit") or args.nit})
        if not tasks:
            parser.error(f"No hay archivos .pdf en {args.pdf_dir}.")
    else:
        if args.timeout < 1 or args.attempts < 1 or args.delay < 0 or args.max_captcha_tasks < 1:
            parser.error("timeout>=1, attempts>=1, delay>=0 y max-captcha-tasks>=1 son obligatorios.")
        if args.captcha in ("manual", "gratis") and args.headless:
            parser.error(f"--captcha {args.captcha} necesita ver el navegador: quita --headless.")
        if (args.captcha_wait is not None and args.captcha_wait < 0) or args.verificacion_espera < 0:
            parser.error("Las esperas CAPTCHA deben ser >= 0.")
        if args.captcha_wait is None:
            args.captcha_wait = {"manual":180, "widget":15, "gratis":2}.get(args.captcha, 8)
        if not args.selectors.exists():
            parser.error(f"No existe {args.selectors}.")
        args.selectores = json.loads(args.selectors.read_text(encoding="utf-8"))
        if missing := [key for key in SELECTORES if not args.selectores.get(key)]:
            parser.error(f"Faltan selectores en {args.selectors.name}: {', '.join(missing)}")
        if args.pedir_clave and args.captcha not in ("2captcha", "auto"):
            parser.error("--pedir-clave sólo se usa con --captcha 2captcha o auto.")
        try:
            args.captcha_key = obtener_clave(args.pedir_clave, args.env_file) if args.captcha in ("2captcha", "auto") else ""
        except CaptchaError as exc:
            parser.error(str(exc))
        has_key = bool(args.captcha_key)
        if args.captcha == "2captcha" and not has_key:
            parser.error("Configura TWOCAPTCHA_API_KEY para usar --captcha 2captcha.")
        tasks = leer_cufes(args.input, args.nit, args.sin_nit)
        if args.limite > 0:
            tasks = tasks[:args.limite]
        print(f"RPA DIAN | facturas: {len(tasks)} | CAPTCHA: {args.captcha} (clave 2Captcha: {'sí' if has_key else 'no'}) | "
              f"NIT: {'sí' if any(t['nit'] for t in tasks) else 'no'} | OCR: "
              f"{'no (capa de texto)' if args.sin_ocr else 'Tesseract ' + ocr.languages}", flush=True)
        if args.captcha == "auto" and not has_key:
            print("AVISO: sin TWOCAPTCHA_API_KEY sólo funcionará si Turnstile entrega el token por sí solo.", flush=True)
    print(f"Resultados en: {args.out}", flush=True)
    args.out.mkdir(parents=True, exist_ok=True)

    started = datetime.now(timezone.utc)
    tracemalloc.start()
    start = time.perf_counter()
    try:
        if args.command == "extraer":
            frames, records = ejecutar_lote(args, tasks, ocr)
        else:
            with sync_playwright() as pw:
                try:
                    page, cerrar = abrir_navegador(pw, args)
                except PlaywrightError as exc:
                    parser.error(f"No se pudo abrir el navegador ({exc.message.splitlines()[0]}). "
                                 "Ejecuta: python -m playwright install chromium  (o usa --canal chrome)")
                page.set_default_timeout(args.timeout * 1000)
                try:
                    frames, records = ejecutar_lote(args, tasks, ocr, page)
                finally:
                    cerrar()
        elapsed = time.perf_counter() - start
        summary = resumir(args, records, frames, started, elapsed, tracemalloc.get_traced_memory()[1] / 1024 ** 2, ocr)
    finally:
        tracemalloc.stop()
    guardar_csv(tabla_metricas(records), args.out / "metricas.csv")
    (args.out / "resumen_metricas.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    escribir_reporte(args.out / "reporte_metricas.md", args, summary, records)
    print(json.dumps({k: v for k, v in summary.items() if not k.startswith("nota_")}, ensure_ascii=False, indent=2))
    print(f"Reporte: {args.out / 'reporte_metricas.md'}")
    return 0 if summary["ok"] == summary["facturas_solicitadas"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
