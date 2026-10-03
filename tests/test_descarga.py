"""Flujo de dos CAPTCHA y descarga real en navegador, sin consumir saldo del proveedor."""
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs

import pymupdf
from playwright.sync_api import sync_playwright

from dian_rpa import CaptchaManager, DianRPA, descargar_con_reintentos, pdf_valido


class ProveedorPrueba:
    key = "clave-de-prueba"

    def __init__(self):
        self.tareas = []

    def resolver(self, task):
        self.tareas.append(task)
        return "token-de-prueba-de-longitud-suficiente-" + task["websiteKey"]


class PaginaConBodyVacio:
    """Simula un body no disponible en la API; el navegador recibe el PDF real."""
    def __init__(self, page):
        self.page = page
        self.handlers = {}

    def __getattr__(self, name):
        return getattr(self.page, name)

    def on(self, event, handler):
        if event == "response":
            def envolver(response):
                handler(SimpleNamespace(headers=response.headers, ok=response.ok, body=lambda: b""))
            self.handlers[handler] = envolver
            self.page.on(event, envolver)
        else:
            self.page.on(event, handler)

    def remove_listener(self, event, handler):
        self.page.remove_listener(event, self.handlers.pop(handler, handler))


BUSQUEDA = """<input id="DocumentKey"><input id="SearchDocumentNit">
<form id="search-document-form"><button class="search-document" type="button"
onclick="if(document.querySelector('[name=cf-turnstile-response]').value)
location.href='/Document/ShowDocumentToPublic';">Buscar</button></form>
<div class="cf-turnstile" data-sitekey="clave-busqueda">
<input type="hidden" name="cf-turnstile-response"></div>"""

DOCUMENTO = """<form id="postForm" method="post" action="/Document/DownloadPDF">
<input name="__RequestVerificationToken" type="hidden" value="csrf-prueba">
<input name="trackId" type="hidden" value="cufe-prueba">
<input name="token" type="hidden" value="sesion-prueba">
<input name="captcha" type="hidden">
<a class="downloadLink" href="javascript:void(0)" onclick="
document.querySelector('.modal-download-info').style.display='block';">Descargar PDF</a>
</form><div class="cf-turnstile" data-sitekey="clave-descarga">
<input type="hidden" name="cf-turnstile-response"></div>
<div class="bootbox modal-download-info" style="display:none">
<span class="sub-title">Este archivo contiene contraseña y corresponde al NIT digitado</span>
<div class="modal-footer"><button class="btn-primary" onclick="descargar()">Aceptar</button></div>
</div><script>async function descargar() {
const form = document.getElementById('postForm');
form.querySelector('[name=captcha]').value = document.querySelector('[name=cf-turnstile-response]').value;
document.querySelector('.modal-download-info').style.display='none';
const response = await fetch(form.action, {method:'POST', body:new URLSearchParams(new FormData(form))});
const a = document.createElement('a'); a.download='factura.pdf';
a.href = URL.createObjectURL(await response.blob()); document.body.appendChild(a); a.click();
}</script>"""


class DescargaTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch(headless=True)
        cls.selectores = json.loads(Path("selectores.json").read_text(encoding="utf-8"))
        with pymupdf.open() as doc:
            doc.new_page().insert_text((72, 72), "Factura de prueba")
            cls.pdf = doc.tobytes()

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()

    def crear_portal(self, primera_corrupta=False, body_vacio=False, modo="2captcha", tokens_widget=False):
        context = self.browser.new_context(accept_downloads=True)
        self.addCleanup(context.close)
        page = context.new_page()
        if body_vacio:
            page = PaginaConBodyVacio(page)
        posts = []

        def atender(route):
            request = route.request
            if request.url.endswith("DownloadPDF"):
                payload = parse_qs(request.post_data or "")
                posts.append(payload)
                correcto = payload.get("captcha") == ["token-de-prueba-de-longitud-suficiente-clave-descarga"]
                route.fulfill(status=200 if correcto else 400,
                              content_type="application/pdf" if correcto else "text/html",
                              body=(b"respuesta truncada" if primera_corrupta and len(posts) == 1
                                    else self.pdf if correcto else b"CAPTCHA incorrecto"))
            else:
                documento = DOCUMENTO.replace("a.click();", "setTimeout(() => a.click(), 500);") if body_vacio else DOCUMENTO
                html = documento if "ShowDocument" in request.url else BUSQUEDA
                if tokens_widget:
                    key = "clave-descarga" if "ShowDocument" in request.url else "clave-busqueda"
                    html += "<script>document.querySelector('[name=cf-turnstile-response]').value = " + json.dumps(
                        "token-de-prueba-de-longitud-suficiente-" + key) + ";</script>"
                route.fulfill(content_type="text/html", body=html)

        page.route("https://dian-prueba.invalid/**", atender)
        proveedor = ProveedorPrueba()
        manager = CaptchaManager(modo, proveedor, wait_s=0)
        rpa = DianRPA(page, "https://dian-prueba.invalid/User/SearchDocument", self.selectores,
                      manager, timeout_ms=3000, verificacion_s=0.1, headless=True)
        return rpa, proveedor, posts

    def test_gratis_descarga_con_tokens_del_widget_sin_consumir_proveedor(self):
        rpa, proveedor, posts = self.crear_portal(modo="gratis", tokens_widget=True)
        task = {"cufe":"cufe-prueba", "nit":"900156264"}
        rpa.consultar(task)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/"gratis.pdf"
            rpa.descargar(task, path)
            self.assertTrue(pdf_valido(path))
        self.assertEqual(proveedor.tareas, [])
        self.assertEqual(rpa.captcha.calls, 2)
        self.assertEqual(rpa.captcha.providers, ["widget", "widget"])
        self.assertEqual(posts[0]["__RequestVerificationToken"], ["csrf-prueba"])

    def test_resuelve_segundo_captcha_y_descarga_pdf_por_post_con_csrf(self):
        rpa, proveedor, posts = self.crear_portal()
        task = {"cufe": "cufe-prueba", "nit": "900156264"}
        rpa.consultar(task)
        with tempfile.TemporaryDirectory() as carpeta:
            archivo = Path(carpeta) / "factura.pdf"
            rpa.descargar(task, archivo)
            self.assertTrue(pdf_valido(archivo), "Debe guardar un PDF legible tras resolver ambos CAPTCHA")
        self.assertEqual([t["websiteKey"] for t in proveedor.tareas], ["clave-busqueda", "clave-descarga"])
        self.assertEqual(posts[0]["__RequestVerificationToken"], ["csrf-prueba"])
        self.assertEqual(posts[0]["trackId"], ["cufe-prueba"])
        self.assertEqual(posts[0]["token"], ["sesion-prueba"])
        self.assertEqual(rpa.captcha.calls, 2)

    def test_respuesta_pdf_corrupta_se_reintenta_sin_declararla_valida(self):
        rpa, proveedor, posts = self.crear_portal(primera_corrupta=True)
        task = {"cufe": "cufe-prueba", "nit": "900156264"}
        record = {"consulta_s": 0.0, "descarga_s": 0.0}
        with tempfile.TemporaryDirectory() as carpeta:
            raiz = Path(carpeta)
            path = raiz / "factura.pdf"
            args = SimpleNamespace(attempts=2, out=raiz)
            descargar_con_reintentos(rpa, task, path, record, args, "factura")
            self.assertTrue(pdf_valido(path))
        self.assertEqual(len(posts), 2)
        self.assertEqual(record["intentos"], 2)

    def test_body_vacio_de_api_no_descarta_descarga_valida_del_navegador(self):
        rpa, _, posts = self.crear_portal(body_vacio=True)
        task = {"cufe": "cufe-prueba", "nit": "900156264"}
        rpa.consultar(task)
        with tempfile.TemporaryDirectory() as carpeta:
            path = Path(carpeta) / "factura.pdf"
            rpa.descargar(task, path)
            self.assertTrue(pdf_valido(path))
        self.assertEqual(len(posts), 1)


if __name__ == "__main__":
    unittest.main()
