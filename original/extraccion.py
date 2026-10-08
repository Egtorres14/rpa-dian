"""Parte 2: OCR de las imágenes exportadas de cada PDF y extracción de los campos pedidos.

Cada página se exporta a PNG y se lee con Tesseract (o, con --sin-ocr, con la capa de texto del
PDF). Las palabras se ubican por coordenadas para separar "Datos del Documento", "Datos del
Emisor/Vendedor" y la tabla "Detalles de Productos". Si el PDF trae capa de texto, esta sirve de
referencia para medir la concordancia del OCR; no reemplaza los valores leídos de la imagen.
"""
from __future__ import annotations

import csv
import io
import os
import re
import shutil
import statistics
import subprocess
import time
import unicodedata
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from difflib import SequenceMatcher
from pathlib import Path
from threading import Lock

import pandas as pd
import numpy as np
import pymupdf
from PIL import Image, ImageChops

FIELDS = ["numero_factura", "fecha_emision", "nit_emisor", "codigo",
          "descripcion", "cantidad", "precio_unitario"]
COLUMNS = ["archivo", "cufe", "pagina", "linea_producto", *FIELDS]
TESSERACT_WINDOWS = Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe")

TITULOS = {
    "documento": ["datos del documento"],
    "emisor": ["datos del emisor", "datos del vendedor", "datos del facturador"],
    "adquiriente": ["datos del adquiriente", "datos del adquirente", "datos del comprador",
                    "datos del receptor", "datos del cliente"],
    "productos": ["detalles de productos", "detalles del producto", "detalle de productos",
                  "detalle del producto", "detalles de los productos"],
}
_ETIQUETA_FACTURA = r"\b(?:numero|nro\.?|no\.?)\s*(?:de\s*)?(?:la\s*)?"
_VALOR_FACTURA = r"(?:\s*electronica)?(?:\s*de\s*venta)?\s*[:#.]?\s*((?=[\w./-]*\d)[A-Za-z0-9][\w./-]*)"
RE_FACTURA = re.compile(_ETIQUETA_FACTURA + r"(?:factura|documento)" + _VALOR_FACTURA, re.I)
# Fuera de la sección sólo vale "factura": "Número documento" también es el del comprador.
RE_FACTURA_PAGINA = re.compile(_ETIQUETA_FACTURA + r"factura" + _VALOR_FACTURA, re.I)
RE_FECHA = re.compile(r"fecha\s*de\s*emision\s*[:.]?\s*([\s\S]{0,40})", re.I)
_VALOR_NIT = r"\s*[:.]?\s*(\d[\d.]{4,}(?:\s*-\s*\d)?)"
RE_NIT = re.compile(r"\bnit\b(?:\s*del\s*(?:emisor|vendedor|facturador))?" + _VALOR_NIT, re.I)
RE_NIT_EMISOR = re.compile(r"\bnit\s*del\s*(?:emisor|vendedor|facturador)" + _VALOR_NIT, re.I)
MESES = {"ene": 1, "feb": 2, "mar": 3, "abr": 4, "may": 5, "jun": 6, "jul": 7,
         "ago": 8, "sep": 9, "set": 9, "oct": 10, "nov": 11, "dic": 12}

COLUMNAS = {"codigo": ("codigo", "cod"), "descripcion": ("descripcion",), "cantidad": ("cantidad", "cant")}
PRECIO = ("precio", "valor", "vr", "vlr")
UNITARIO = ("unitario", "unit")
# Rótulos que no se exportan pero delimitan columnas (si no, su valor se pegaría al vecino).
OTRAS = {"nro", "no", "#", "item", "u/m", "um", "u.m", "unidad", "und", "medida", "descuento",
         "descuentos", "dcto", "recargo", "recargos", "cargo", "iva", "inc", "ica", "%", "subtotal",
         "total", "impuesto", "impuestos", "base", "tarifa", "neto", "bruto"}
FIN_TABLA = re.compile(r"(?:datos (?:totales|de)|totales?\b|total a pagar|subtotal\b|observaciones\b|"
                       r"notas?\b|cufe\b|impuestos\b|retenciones\b|valor (?:total|en letras))")
RUIDO = re.compile(r"pagina \d+ de \d+|documento generado|representacion grafica")


def sin_tildes(texto: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", texto) if not unicodedata.combining(c))


def normalizar(texto: str) -> str:
    return " ".join(sin_tildes(texto).lower().split())


def parecido(texto: str, objetivo: str, minimo: float = 0.8) -> bool:
    """Igualdad tolerante a errores de OCR (sólo para palabras de 5 o más letras)."""
    return texto == objetivo or (len(objetivo) >= 5 and SequenceMatcher(None, texto, objetivo).ratio() >= minimo)


def decimal_local(texto: str, separador: str = ",") -> Decimal | None:
    """Convierte un número con separador decimal explícito.

    Con ',' 1.234,56 -> 1234.56; con '.' 1,234.56 -> 1234.56. Si el texto contradice el
    separador (1,234.56 leído con ','), devuelve None en vez de un valor equivocado.
    """
    value = re.sub(r"(?:COP|USD|EUR|\$|\s)", "", texto or "", flags=re.I)
    if value.startswith("(") and value.endswith(")"):
        value = "-" + value[1:-1]
    miles = "." if separador == "," else ","
    if separador in value and miles in value and value.rfind(miles) > value.rfind(separador):
        return None
    # Un separador de miles sin decimal sólo se acepta en grupos de tres (1.500 / 1,500).
    if separador in value or re.fullmatch(rf"-?\d{{1,3}}(?:{re.escape(miles)}\d{{3}})+", value):
        value = value.replace(miles, "").replace(separador, ".")
    elif miles in value:
        value = value.replace(miles, ".")
    if not re.fullmatch(r"-?\d+(?:\.\d+)?", value):
        return None
    try:
        return Decimal(value)
    except InvalidOperation:
        return None


def detectar_separador(textos) -> str | None:
    """Vota el separador decimal con los números del documento; None si no hay evidencia."""
    votos = {",": 0, ".": 0}
    for texto in textos:
        value = re.sub(r"(?:COP|\$|\s)", "", texto, flags=re.I)
        if not re.fullmatch(r"-?\d[\d.,]*\d", value):
            continue
        if "," in value and "." in value:
            votos["," if value.rfind(",") > value.rfind(".") else "."] += 1
        elif re.fullmatch(r"-?\d+,\d{2}", value) or re.fullmatch(r"-?\d{1,3}(?:\.\d{3}){2,}", value):
            votos[","] += 1
        elif re.fullmatch(r"-?\d+\.\d{2}", value) or re.fullmatch(r"-?\d{1,3}(?:,\d{3}){2,}", value):
            votos["."] += 1
    if votos[","] == votos["."]:
        return None
    return "," if votos[","] > votos["."] else "."


def fecha_iso(texto: str) -> str | None:
    texto = normalizar(texto or "")
    match = re.search(r"\b(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})\b", texto)
    if match:
        year, month, day = match.groups()
    elif match := re.search(r"\b(\d{1,2})[-/.](\d{1,2})[-/.](\d{4})\b", texto):
        day, month, year = match.groups()
    elif match := re.search(r"\b(\d{1,2})\s*(?:de\s+|-|/)?\s*(" + "|".join(MESES) + r")[a-z]*\.?\s*"
                            r"(?:de\s+|del\s+|-|/)?\s*(\d{4})\b", texto):
        day, month, year = match[1], MESES[match[2]], match[3]
    else:
        return None
    try:
        return date(int(year), int(month), int(day)).isoformat()
    except ValueError:
        return None


@dataclass
class Word:
    x0: float
    y0: float
    x1: float
    y1: float
    text: str
    confidence: float | None = None


def agrupar_lineas(words: list[Word]) -> list[list[Word]]:
    groups: list[list[Word]] = []
    for word in sorted(words, key=lambda w: ((w.y0 + w.y1) / 2, w.x0)):
        cy = (word.y0 + word.y1) / 2
        if groups:
            previous_y = statistics.mean((w.y0 + w.y1) / 2 for w in groups[-1])
            tolerance = max(3.0, min(5.5, (word.y1 - word.y0) * .40))
        if groups and abs(cy - previous_y) <= tolerance:
            groups[-1].append(word)
        else:
            groups.append([word])
    return [sorted(g, key=lambda w: w.x0) for g in groups]


def texto_lineas(words: list[Word]) -> str:
    return "\n".join(" ".join(w.text for w in line) for line in agrupar_lineas(words))


def _lineas_verdes(rgb):
    rgb = rgb.astype(np.int16)
    return ((rgb[:, :, 1] > 140) & (rgb[:, :, 0] < 150)
            & (rgb[:, :, 1] - rgb[:, :, 0] > 40)
            & (rgb[:, :, 1] - rgb[:, :, 2] > 25))


class OCR:
    def __init__(self, executable: str = "tesseract", languages: str = "spa+eng",
                 timeout: int = 120, workers: int = 4):
        if workers < 1:
            raise ValueError("workers debe ser >= 1")
        found = shutil.which(executable)
        if not found and executable == "tesseract" and TESSERACT_WINDOWS.exists():
            found = str(TESSERACT_WINDOWS)
        self.executable = found or executable
        self.requested_languages = languages
        self.languages = ""
        self.timeout = timeout
        self.workers = workers
        self.warnings: list[str] = []
        # Un hilo por proceso evita multiplicar los hilos internos de Tesseract
        # al ejecutar varias celdas. No modifica el entorno del usuario.
        self._environment = {**os.environ, "OMP_THREAD_LIMIT": "1"}
        self._images = OrderedDict()
        self._image_lock = Lock()

    def limpiar_imagenes(self) -> None:
        with self._image_lock:
            for _, image in self._images.values():
                image.close()
            self._images.clear()

    def _recortar(self, image_path: Path, box, scale: float):
        """Reutiliza dos imágenes decodificadas; el recorte pertenece al llamador."""
        path = Path(image_path).resolve()
        with self._image_lock:
            stat = path.stat()
            version = (stat.st_mtime_ns, stat.st_size)
            cached = self._images.pop(path, None)
            if cached is not None and cached[0] != version:
                cached[1].close()
                cached = None
            if cached is None:
                with Image.open(path) as source:
                    image = source.convert("RGB")
            else:
                image = cached[1]
            self._images[path] = (version, image)
            while len(self._images) > 2:
                _, (_, old_image) = self._images.popitem(last=False)
                old_image.close()
            left, top = max(0, int(box[0] * scale)), max(0, int(box[1] * scale))
            right, bottom = min(image.width, int(box[2] * scale)), min(image.height, int(box[3] * scale))
            return image.crop((left, top, right, bottom)) if right > left and bottom > top else None

    def comprobar(self) -> None:
        try:
            result = subprocess.run([self.executable, "--list-langs"], capture_output=True,
                                    text=True, timeout=20, env=self._environment)
        except (OSError, subprocess.SubprocessError) as exc:
            raise RuntimeError(f"no se pudo ejecutar '{self.executable}' ({exc})") from exc
        installed = {line.strip() for line in (result.stdout + "\n" + result.stderr).splitlines()
                     if line.strip() and "list of available" not in line.lower()}
        chosen = [lang for lang in self.requested_languages.split("+") if lang in installed]
        if not chosen:
            raise RuntimeError(f"Tesseract no tiene los idiomas {self.requested_languages}; instala spa o eng.")
        self.languages = "+".join(chosen)
        if self.languages != self.requested_languages:
            self.warnings.append(f"OCR usa {self.languages}; falta parte de {self.requested_languages}.")

    @staticmethod
    def _leer_tsv(tsv: str, scale: float) -> list[Word]:
        words = []
        # QUOTE_NONE: una comilla leída por el OCR no debe unir filas del TSV.
        for row in csv.DictReader(io.StringIO(tsv), delimiter="\t", quoting=csv.QUOTE_NONE):
            text = (row.get("text") or "").strip()
            try:
                confidence = float(row.get("conf") or -1)
            except ValueError:
                continue
            if not text or confidence < 0:
                continue
            x, y, w, h = [float(row[key]) / scale for key in ("left", "top", "width", "height")]
            words.append(Word(x, y, x + w, y + h, text, confidence))
        return words

    def palabras(self, path: Path, scale: float) -> list[Word]:
        if not self.languages:
            self.comprobar()
        result = subprocess.run([self.executable, str(path), "stdout", "-l", self.languages,
                                 "--psm", "3", "tsv"], check=True, capture_output=True, text=True,
                                encoding="utf-8", errors="replace", timeout=self.timeout, env=self._environment)
        return self._leer_tsv(result.stdout, scale)

    def numero_celda(self, image_path: Path, box: tuple[float, float, float, float],
                     scale: float, separator: str) -> Decimal | None:
        """Segunda lectura de una celda vacía: la segmentación global puede perder un dígito."""
        text, _ = self.texto_region(image_path, box, scale, whitelist="0123456789,.-")
        return decimal_local(text, separator)

    def texto_region(self, image_path: Path, box: tuple[float, float, float, float],
                     scale: float, whitelist: str = "", psm: int = 7,
                     quitar_lineas: bool = False) -> tuple[str, float]:
        if not self.languages:
            self.comprobar()
        crop = self._recortar(image_path, box, scale)
        if crop is None:
            return "", 0.0
        if quitar_lineas:
            rgb = np.asarray(crop).copy()
            rgb[_lineas_verdes(rgb)] = 255
            crop = Image.fromarray(rgb)
        crop = crop.convert("L")
        padded = Image.new("L", (crop.width + 30, crop.height + 30), 255)
        padded.paste(crop, (15, 15))
        stream = io.BytesIO()
        padded.save(stream, format="PNG")
        command = [self.executable, "stdin", "stdout", "-l", self.languages, "--psm", str(psm)]
        if whitelist:
            command += ["-c", "tessedit_char_whitelist=" + whitelist]
        result = subprocess.run([*command, "tsv"], input=stream.getvalue(),
                                capture_output=True, check=True, timeout=30, env=self._environment)
        words = self._leer_tsv(result.stdout.decode("utf-8", "replace"), 1.0)
        return " ".join(w.text for w in words), statistics.mean(w.confidence for w in words) if words else 0.0

    def palabras_region(self, image_path: Path, box: tuple[float, float, float, float],
                        scale: float, psm: int = 6) -> list[Word]:
        """OCR de una región con el PSM indicado; devuelve Words en coordenadas de la página.

        Aislar una banda (p. ej. la tabla) ayuda a Tesseract: en la página completa, con psm 3,
        la fila numérica de la tabla de la DIAN se pierde; recortada y con psm 6 se lee bien. Se
        recortan las franjas en blanco de arriba y abajo porque psm 6 falla con mucho espacio."""
        if not self.languages:
            self.comprobar()
        pad = 20
        crop = self._recortar(image_path, box, scale)
        if crop is None:
            return []
        crop = crop.convert("L")
        ink = ImageChops.invert(crop).getbbox()  # franja con tinta (descarta blanco arriba/abajo)
        y_trim = 0
        if ink:
            y_trim = ink[1]
            crop = crop.crop((0, ink[1], crop.width, ink[3]))
        if crop.width <= 0 or crop.height <= 0:
            return []
        padded = Image.new("L", (crop.width + 2 * pad, crop.height + 2 * pad), 255)
        padded.paste(crop, (pad, pad))
        stream = io.BytesIO()
        padded.save(stream, format="PNG")
        result = subprocess.run([self.executable, "stdin", "stdout", "-l", self.languages,
                                 "--psm", str(psm), "tsv"], input=stream.getvalue(),
                                capture_output=True, check=True, timeout=self.timeout, env=self._environment)
        words = []
        for w in self._leer_tsv(result.stdout.decode("utf-8", "replace"), 1.0):
            words.append(Word((w.x0 - pad) / scale + box[0], (w.y0 - pad + y_trim) / scale + box[1],
                              (w.x1 - pad) / scale + box[0], (w.y1 - pad + y_trim) / scale + box[1],
                              w.text, w.confidence))
        return words


def _tokens_titulo(line: list[Word]) -> list[tuple[str, Word]]:
    tokens = []
    for word in line:
        for part in re.split(r"[/|]", normalizar(word.text)):
            part = part.strip(".,:;()[]-")
            if part:
                tokens.append((part, word))
    return tokens


def _titulo(words: list[Word], frases: list[str], minimo: float = 0.88) -> tuple[float, float, float] | None:
    """Ubica un título de sección, tolerando errores de OCR. Devuelve (x0, y0, y1)."""
    for line in agrupar_lineas(words):
        tokens = _tokens_titulo(line)
        textos = [token for token, _ in tokens]
        for frase in frases:
            partes = frase.split()
            for i in range(len(tokens) - len(partes) + 1):
                ventana = " ".join(textos[i:i + len(partes)])
                if ventana == frase or (parecido(textos[i], partes[0], .75)
                                        and SequenceMatcher(None, ventana, frase).ratio() >= minimo):
                    found = [word for _, word in tokens[i:i + len(partes)]]
                    return min(w.x0 for w in found), min(w.y0 for w in found), max(w.y1 for w in found)
    return None


def metadatos(words: list[Word], width: float) -> dict[str, str | None]:
    """Número de factura, fecha de emisión y NIT del emisor (nunca el NIT del comprador)."""
    result: dict[str, str | None] = dict.fromkeys(FIELDS[:3])
    titulos = {key: _titulo(words, frases) for key, frases in TITULOS.items()}

    def seccion(key: str, derecha: float = float("inf")) -> str:
        titulo = titulos[key]
        if not titulo:
            return ""
        _, y0, y1 = titulo
        fin = min((t[1] for t in titulos.values() if t and t[1] > y0 + 10), default=float("inf"))
        return sin_tildes(texto_lineas([w for w in words if y1 - 2 < w.y0 < fin and w.x0 < derecha]))

    # La sección primero; la página completa sólo como respaldo.
    pagina = sin_tildes(texto_lineas(words))
    documento = seccion("documento")
    for texto, patron in ((documento, RE_FACTURA), (pagina, RE_FACTURA_PAGINA)):
        if texto and not result["numero_factura"] and (match := patron.search(texto)):
            result["numero_factura"] = match[1]
    for texto in (documento, pagina):
        if texto and not result["fecha_emision"] and (match := RE_FECHA.search(texto)):
            result["fecha_emision"] = fecha_iso(match[1])
    derecha = float("inf")
    emisor, adquiriente = titulos["emisor"], titulos["adquiriente"]
    if emisor and adquiriente and abs(adquiriente[1] - emisor[1]) < 8 and adquiriente[0] > emisor[0] + 80:
        derecha = adquiriente[0] - 5  # Emisor y adquiriente lado a lado: sólo la columna del emisor.
    for texto, patron in ((seccion("emisor", derecha), RE_NIT), (pagina, RE_NIT_EMISOR)):
        if texto and not result["nit_emisor"] and (match := patron.search(texto)):
            result["nit_emisor"] = re.sub(r"[.\s]", "", match[1])
    return result


def _limpio(texto: str) -> str:
    return normalizar(texto).strip(".:;,()[]")


def _clave(texto: str) -> str | None:
    for key, nombres in COLUMNAS.items():
        if any(parecido(texto, nombre) for nombre in nombres):
            return key
    return None


def _cabecera(words: list[Word]) -> tuple[dict[str, tuple[float, float]], float] | None:
    """Deduce las columnas de la tabla de productos de sus rótulos (también en dos líneas)."""
    for line in agrupar_lineas(words):
        if not any(parecido(_limpio(w.text), "descripcion") for w in line):
            continue
        top, bottom = min(w.y0 for w in line), max(w.y1 for w in line)
        band = [w for w in words if top - 12 <= w.y0 <= bottom + 10]
        starts: dict[str, float] = {}
        others: list[float] = []
        header: list[Word] = []
        for word in sorted(band, key=lambda w: w.x0):
            value = _limpio(word.text)
            if key := _clave(value):
                header.append(word)
                if key in starts:
                    others.append(word.x0)
                else:
                    starts[key] = word.x0
            elif any(parecido(value, p) for p in PRECIO) or re.fullmatch(r"(?:precio|valor|vr|vlr)\.?unit\w*", value):
                header.append(word)
                unit = [w for w in band if abs(w.x0 - word.x0) < 65
                        and any(parecido(_limpio(w.text), u) for u in UNITARIO)]
                if "precio_unitario" not in starts and (unit or "unit" in value):
                    starts["precio_unitario"] = word.x0
                    header.extend(unit)
                else:
                    others.append(word.x0)  # p. ej. "Precio unitario de venta" (con impuestos)
            elif value in OTRAS:
                header.append(word)
                others.append(word.x0)
        if len(starts) != 4:
            continue
        ordered = sorted(set([*starts.values(), *others]))
        ranges = {key: (x - 6, min((o for o in ordered if o > x + 8), default=float("inf")) - 6)
                  for key, x in starts.items()}
        return ranges, max(w.y1 for w in header)
    return None


def _numero(cell: list[Word], sep: str) -> Decimal | None:
    """Número de una celda; si la columna capturó varios valores, toma el primero válido."""
    value = decimal_local(" ".join(w.text for w in cell), sep)
    if value is None:
        for word in cell:
            if (value := decimal_local(word.text, sep)) is not None:
                break
    return value


def productos(words: list[Word], pagina: int, sep: str = ",", previa=None) -> tuple[list[dict], list[str], tuple | None]:
    """Filas bajo la cabecera de la tabla. Sin cabecera en la página, `previa` (de una página
    anterior) la trata como continuación: sólo cuentan filas con cantidad y precio."""
    header, continuacion = _cabecera(words), False
    if header is None and previa is not None:
        header, continuacion = (previa[0], 0.0), True
    if header is None:
        return [], [f"Página {pagina}: no se reconocieron las cuatro columnas del producto."], None
    ranges, y = header
    output: list[dict] = []
    warnings = []
    pending = ""
    for line in agrupar_lineas([w for w in words if (w.y0 + w.y1) / 2 > y]):
        full = normalizar(" ".join(w.text for w in line))
        if FIN_TABLA.match(full):
            break
        if RUIDO.search(full):
            continue
        # Cada palabra va a la columna que contiene su centro: tolera números alineados a la derecha.
        cells = {key: [w for w in line if x0 <= (w.x0 + w.x1) / 2 < x1] for key, (x0, x1) in ranges.items()}
        texts = {key: " ".join(w.text for w in cell).strip() for key, cell in cells.items()}
        quantity = _numero(cells["cantidad"], sep)
        price = _numero(cells["precio_unitario"], sep)
        if continuacion and (quantity is None or price is None):
            continue
        if quantity is not None or price is not None:
            output.append({"pagina": pagina, "codigo": texts["codigo"] or None,
                           "descripcion": " ".join(filter(None, (pending, texts["descripcion"]))) or None,
                           "cantidad": quantity, "precio_unitario": price,
                           "_y0": min(w.y0 for w in line), "_y1": max(w.y1 for w in line)})
            pending = ""
            if quantity is None or price is None:
                warnings.append(f"Página {pagina}: cantidad o precio no interpretable en una fila.")
        else:
            # Línea sin cantidad ni precio: continuación de la descripción de la fila anterior. El
            # texto desbordado invade la columna del código (la descripción empieza a su izquierda),
            # así que se une código + descripción. Antes de la primera fila va a `pending`.
            resto = " ".join(filter(None, (texts["codigo"], texts["descripcion"]))).strip()
            if not resto:
                continue
            if output:
                output[-1]["descripcion"] = " ".join(filter(None, (output[-1]["descripcion"], resto)))
            else:
                pending = " ".join(filter(None, (pending, resto)))
    return output, warnings, header


def geometria_tabla(image_path: Path, words: list[Word], scale: float):
    """Límites de celdas desde las líneas verdes de la representación gráfica DIAN.

    Los títulos están centrados y no definen el inicio de cada columna. Las líneas
    de la imagen sí delimitan códigos, textos multilínea y valores de cada fila.
    Si no se detecta una cuadrícula completa, se conserva el parser por palabras.
    """
    header = _cabecera(words)
    if header is None:
        return None
    with Image.open(image_path) as imagen:
        verde = _lineas_verdes(np.asarray(imagen.convert("RGB")))
    y = int(header[1] * scale)
    banda = verde[max(0, y - 20):max(1, y - 5)]
    if not banda.size:
        return None

    def centros(indices):
        return [float(g.mean()) / scale for g in
                np.split(indices, np.where(np.diff(indices) > 1)[0] + 1) if len(g)]

    bordes = centros(np.flatnonzero(banda.mean(axis=0) > .6))
    if len(bordes) < 5:
        return None
    columnas = {}
    for key, (inicio, _) in header[0].items():
        etiqueta_x = inicio + 6
        intervalo = next(((a, b) for a, b in zip(bordes, bordes[1:]) if a <= etiqueta_x < b), None)
        if intervalo is None:
            return None
        columnas[key] = intervalo
    if len(set(columnas.values())) != 4:
        return None
    izquierda, derecha = int(bordes[0] * scale), int(bordes[-1] * scale)
    lineas = centros(np.flatnonzero(verde[:, izquierda:derecha].mean(axis=1) > .7))
    lineas = [linea for linea in lineas if linea >= header[1] - 1]
    filas = []
    for top, bottom in zip(lineas, lineas[1:]):
        if bottom - top < 5:
            continue
        # Termina al salir de esta tabla: no incluir Descuentos/Recargos Globales.
        tramo = verde[int((top + 1) * scale):int((bottom - 1) * scale)]
        continuidad = [tramo[:, int(borde * scale)].mean() for borde in bordes]
        if np.mean(continuidad) < .5:
            break
        filas.append((top, bottom))
    return (columnas, filas) if filas else None


def productos_celdas(geometry, pagina: int, sep: str, *, words=None, ocr=None,
                     image_path=None, scale=1.0, alternative_image_path=None, alternative_scale=1.0):
    """Lee cada celda aislada por OCR; `words` se usa sólo para la referencia PDF."""
    columnas, filas = geometry
    output = [{"pagina": pagina, "_y0": top, "_y1": bottom} for top, bottom in filas]

    def leer_celda(job):
        index, key, left, right, top, bottom = job
        if ocr is not None:
            box = (left, top + 1, right, bottom - 1)
            texto, confianza = ocr.texto_region(image_path, box, scale, psm=6,
                                               whitelist="0123456789,.-" if key in ("cantidad", "precio_unitario") else "",
                                               quitar_lineas=True)
            if key == "codigo" and confianza < 90 and alternative_image_path is not None:
                alternativo, confianza_alternativa = ocr.texto_region(alternative_image_path, box,
                                                                    alternative_scale, psm=6, quitar_lineas=True)
                if alternativo and confianza_alternativa > confianza:
                    texto = alternativo  # Ambas opciones proceden de OCR, sin copiar el texto PDF.
        else:
            celda = [w for w in words if left <= (w.x0 + w.x1) / 2 < right
                     and top <= (w.y0 + w.y1) / 2 < bottom]
            texto = texto_lineas(celda)
        texto = " ".join(texto.split())
        value = decimal_local(texto, sep) if key in ("cantidad", "precio_unitario") else texto or None
        return index, key, value

    jobs = [(i, key, left, right, top, bottom) for i, (top, bottom) in enumerate(filas)
            for key, (left, right) in columnas.items()]
    if ocr is not None and ocr.workers > 1 and jobs:
        if not ocr.languages:
            ocr.comprobar()
        # Sólo las lecturas de Tesseract son concurrentes; PyMuPDF sigue en el hilo principal.
        with ThreadPoolExecutor(max_workers=min(ocr.workers, len(jobs))) as pool:
            for index, key, value in pool.map(leer_celda, jobs):
                output[index][key] = value
    else:
        for index, key, value in map(leer_celda, jobs):
            output[index][key] = value
    return output


@dataclass
class _Pagina:
    numero: int
    imagen: Path
    ancho: float
    alto: float
    nativas: list[Word]
    palabras: list[Word]
    ocr: bool = False

    @property
    def con_texto(self) -> bool:
        return sum(len(w.text) for w in self.nativas) >= 60


def _releer_metadatos(ocr: OCR, pagina: _Pagina, detected: dict, scale: float,
                      warnings: list[str], log: list[str]) -> float:
    """Relee por región los campos de cabecera con baja confianza. Devuelve los segundos usados."""
    seconds = 0.0
    native = metadatos(pagina.nativas, pagina.ancho) if pagina.con_texto else {}
    for key, value in detected.items():
        if not value:
            continue
        if key == "fecha_emision":
            candidates = [w for w in pagina.palabras if fecha_iso(w.text) == value]
        else:
            target = re.sub(r"[.\s]", "", normalizar(value))
            candidates = [w for w in pagina.palabras if re.sub(r"[.\s]", "", normalizar(w.text)) == target]
        if not candidates or candidates[0].confidence is None or candidates[0].confidence >= 75:
            continue
        word = candidates[0]
        start = time.perf_counter()
        reread, confidence = ocr.texto_region(pagina.imagen, (word.x0 - 3, word.y0 - 4, word.x1 + 3, word.y1 + 4), scale)
        seconds += time.perf_counter() - start
        if key == "fecha_emision":
            valid = fecha_iso(reread)
        elif key == "nit_emisor":
            valid = re.sub(r"[.\s]", "", reread) if re.fullmatch(r"[\d.]+(?:\s*-\s*\d)?", reread) else None
        else:
            valid = reread if re.fullmatch(r"[\w./-]+", reread) else None
        # Una capa de texto existente permite aceptar una relectura algo menos segura.
        native_match = bool(valid) and normalizar(str(valid)) == normalizar(str(native.get(key) or ""))
        if valid and confidence > word.confidence and (confidence >= 75 or (native_match and confidence >= 65)):
            detected[key] = valid
            log.append(f"RELECTURA {key}: {valid} (confianza {confidence:.1f})")
        else:
            warnings.append(f"Página {pagina.numero}: {key} tiene baja confianza OCR; revisar.")
    return seconds


def _igual(a, b) -> bool:
    if isinstance(a, Decimal) or isinstance(b, Decimal):
        return a is not None and b is not None and Decimal(a) == Decimal(b)
    return re.sub(r"\s", "", normalizar(str(a or ""))) == re.sub(r"\s", "", normalizar(str(b or "")))


def _comparar(meta_ocr: dict, filas_ocr: list[dict], meta_pdf: dict, filas_pdf: list[dict]) -> tuple[int, int, list[str]]:
    """Compara campo a campo lo leído por OCR con lo leído de la capa de texto del PDF."""
    pares = [(key, meta_ocr.get(key), meta_pdf.get(key)) for key in FIELDS[:3] if meta_ocr.get(key) or meta_pdf.get(key)]
    for index in range(max(len(filas_ocr), len(filas_pdf))):
        ocr_row = filas_ocr[index] if index < len(filas_ocr) else {}
        pdf_row = filas_pdf[index] if index < len(filas_pdf) else {}
        pares += [(f"fila {index + 1} {key}", ocr_row.get(key), pdf_row.get(key)) for key in FIELDS[3:]]
    # Un campo vacío en ambas fuentes no es evidencia de una extracción correcta.
    pares = [(campo, a, b) for campo, a, b in pares
             if (a is not None and a != "") or (b is not None and b != "")]
    diferencias = [f"{campo}: OCR={a} | PDF={b}" for campo, a, b in pares if not _igual(a, b)]
    return len(pares), len(pares) - len(diferencias), diferencias


def abrir_pdf(pdf_path: Path, passwords=()) -> "pymupdf.Document":
    """Abre el PDF y, si está cifrado, lo descifra con la primera contraseña que sirva.

    Las representaciones gráficas de la DIAN vienen protegidas con contraseña = NIT del emisor o
    receptor digitado al descargar. `passwords` son las candidatas a probar (p. ej. ese NIT)."""
    doc = pymupdf.open(pdf_path)
    if not doc.needs_pass:
        return doc
    for password in (*passwords, ""):
        if password and doc.authenticate(str(password)):
            return doc
    doc.close()
    intentadas = ", ".join(str(p) for p in passwords if p) or "ninguna"
    raise RuntimeError(f"PDF cifrado: la contraseña no coincide (probadas: {intentadas}). "
                       "En la DIAN la contraseña es el NIT del emisor o receptor; pásalo con --password o --nit.")


def extraer_factura(pdf_path: Path, output_dir: Path, ocr: OCR, *, cufe: str = "",
                    usar_ocr: bool = True, dpi: int = 300,
                    decimal_separator: str = "auto", passwords=()) -> tuple[pd.DataFrame, dict]:
    """Devuelve UN DataFrame por PDF (una fila por producto, cabecera repetida) y sus métricas."""
    start = time.perf_counter()
    images = output_dir / "imagenes" / pdf_path.stem
    images.mkdir(parents=True, exist_ok=True)
    text_dir = output_dir / "textos"
    text_dir.mkdir(parents=True, exist_ok=True)
    scale = dpi / 72
    pages: list[_Pagina] = []
    lecturas = []
    confidences: list[float] = []
    render_s = read_s = parse_s = 0.0
    # 1) Renderiza en el hilo principal; después lee los PNG con Tesseract.
    with abrir_pdf(pdf_path, passwords) as doc:
        for number, page in enumerate(doc, 1):
            t = time.perf_counter()
            image_path = images / f"pagina_{number:03d}.png"
            page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False).save(image_path)
            # PSM 3 localiza mejor los encabezados a 300 DPI; las celdas se leen
            # desde la imagen principal de mayor resolución, sin perder detalle.
            layout_path, layout_scale = image_path, scale
            if dpi > 300 and usar_ocr:
                layout_scale = 300 / 72
                layout_path = images / f"estructura_300_{number:03d}.png"
                page.get_pixmap(matrix=pymupdf.Matrix(layout_scale, layout_scale), alpha=False).save(layout_path)
            native = [Word(*w[:4], w[4]) for w in page.get_text("words", sort=True)]
            render_s += time.perf_counter() - t
            info = _Pagina(number, image_path, page.rect.width, page.rect.height, native, native)
            if usar_ocr or not info.con_texto:
                lecturas.append((info, layout_path, layout_scale))
            pages.append(info)
    if lecturas:
        if not ocr.languages:
            ocr.comprobar()
        t = time.perf_counter()
        def leer_pagina(job):
            _, path, image_scale = job
            return ocr.palabras(path, image_scale)

        with ThreadPoolExecutor(max_workers=min(ocr.workers, len(lecturas))) as pool:
            for (info, _, _), words in zip(lecturas, pool.map(leer_pagina, lecturas)):
                info.palabras, info.ocr = words, True
                confidences.extend(w.confidence for w in words if w.confidence is not None)
        read_s += time.perf_counter() - t  # Pared; no suma tiempos de trabajos simultáneos.
    # 2) Separador decimal del documento: 1.234,56 o 1,234.56 (capa de texto si existe).
    if decimal_separator in (",", "."):
        sep = decimal_separator
    else:
        sep = detectar_separador(w.text for p in pages for w in (p.nativas if p.con_texto else p.palabras)) or ","
    metadata = dict.fromkeys(FIELDS[:3])
    items: list[dict] = []
    warnings: list[str] = []
    all_text: list[str] = []
    compared = equal = 0
    previous = previous_pdf = None
    cufe_verified = False
    # 3) Campos de cabecera y tabla de productos, página por página.
    for p in pages:
        t = time.perf_counter()
        additional_ocr_s = 0.0
        log = [f"--- PÁGINA {p.numero} ({'OCR' if p.ocr else 'texto PDF'}) ---", texto_lineas(p.palabras)]
        detected = metadatos(p.palabras, p.ancho)
        if p.ocr:
            additional_ocr_s += _releer_metadatos(ocr, p, detected, scale, warnings, log)
        for key, value in detected.items():
            if value and metadata[key] and value != metadata[key]:
                warnings.append(f"{key}: valor distinto entre páginas; revisar.")
            if value and not metadata[key]:
                metadata[key] = value
        geometry = geometria_tabla(p.imagen, p.palabras, scale)
        if geometry:
            t_ocr = time.perf_counter()
            rows = productos_celdas(geometry, p.numero, sep, words=p.palabras,
                                    ocr=ocr if p.ocr else None, image_path=p.imagen, scale=scale,
                                    alternative_image_path=images / f"estructura_300_{p.numero:03d}.png" if dpi > 300 and usar_ocr else None,
                                    alternative_scale=300 / 72)
            if p.ocr:
                additional_ocr_s += time.perf_counter() - t_ocr
            page_warnings, header = [], (geometry[0], geometry[1][0][0])
            log.append("LECTURA POR CELDAS: " + " | ".join(str({k: r[k] for k in FIELDS[3:]}) for r in rows))
        else:
            rows, page_warnings, header = productos(p.palabras, p.numero, sep, previous)
        if p.ocr and not geometry:
            cabecera = _cabecera(p.palabras)
            completas = lambda rs: sum(r["cantidad"] is not None and r["precio_unitario"] is not None for r in rs)
            # El OCR de página completa (psm 3) pierde las filas numéricas de la tabla de la DIAN.
            # Si se detecta la cabecera pero faltan filas, se relee la banda de la tabla con psm 6.
            if cabecera and (not rows or completas(rows) < len(rows)):
                tabla = _titulo(p.palabras, TITULOS["productos"])
                _, header_bottom = cabecera
                top = (tabla[2] if tabla else header_bottom - 28)  # el recorte de blancos ajusta el borde
                fin = p.alto
                for line in agrupar_lineas([w for w in p.palabras if w.y0 > header_bottom + 2]):
                    if FIN_TABLA.match(normalizar(" ".join(w.text for w in line))):
                        fin = min(w.y0 for w in line)
                        break
                # psm 6 se rompe con el borde inferior del recuadro y el blanco tras la última fila;
                # se prueban varios límites inferiores y se conserva el que da más filas completas.
                t_ocr = time.perf_counter()
                mejor = None
                for bottom in sorted({min(header_bottom + delta, fin) for delta in (12, 22, 35, 55, 85, 130, 200, 320)}):
                    banda = ocr.palabras_region(p.imagen, (0, top, p.ancho, bottom), scale, psm=6)
                    candidato = productos(banda, p.numero, sep, previous)
                    puntaje = (completas(candidato[0]), len(candidato[0]))
                    if mejor is None or puntaje > mejor[0]:
                        mejor = (puntaje, candidato)
                additional_ocr_s += time.perf_counter() - t_ocr
                rows2, warn2, header2 = mejor[1]
                if completas(rows2) > completas(rows) or (not rows and rows2):
                    rows, page_warnings, header = rows2, warn2, header2
                    log.append("RELECTURA TABLA (psm 6): " + " | ".join(
                        f"{r.get('codigo')} / {r.get('descripcion')} / {r.get('cantidad')} / {r.get('precio_unitario')}"
                        for r in rows2))
        previous = header or previous
        if p.ocr and header:
            for row in rows:
                for key in ("cantidad", "precio_unitario"):
                    if row[key] is None:
                        x0, x1 = header[0][key]
                        t_ocr = time.perf_counter()
                        row[key] = ocr.numero_celda(p.imagen, (x0, row["_y0"] - 4, min(x1, p.ancho), row["_y1"] + 4),
                                                    scale, sep)
                        additional_ocr_s += time.perf_counter() - t_ocr
            page_warnings = [w for w in page_warnings if "cantidad o precio no interpretable" not in w]
            if any(row[key] is None for row in rows for key in ("cantidad", "precio_unitario")):
                page_warnings.append(f"Página {p.numero}: cantidad o precio no interpretable tras segunda lectura.")
        for row in rows:
            row.pop("_y0", None)
            row.pop("_y1", None)
        items.extend(rows)
        warnings.extend(page_warnings)
        if p.ocr and p.con_texto:
            if geometry:
                filas_pdf = productos_celdas(geometry, p.numero, sep, words=p.nativas)
                header_pdf = header
            else:
                filas_pdf, _, header_pdf = productos(p.nativas, p.numero, sep, previous_pdf)
            previous_pdf = header_pdf or previous_pdf
            for row in filas_pdf:
                row.pop("_y0", None)
                row.pop("_y1", None)
            n, same, diferencias = _comparar(detected, rows, metadatos(p.nativas, p.ancho), filas_pdf)
            compared, equal = compared + n, equal + same
            if diferencias:
                log.append("DIFERENCIAS OCR vs capa de texto del PDF:\n" + "\n".join(diferencias))
        if cufe:
            native_cufe = "".join(w.text for w in p.nativas).lower()
            cufe_verified = (cufe_verified or cufe in native_cufe
                             or cufe in re.sub(r"\s", "", normalizar(texto_lineas(p.palabras))))
            if not cufe_verified and p.ocr:
                anchors = [w for w in p.palabras if normalizar(w.text).strip(":") == "cufe"]
                if anchors:
                    anchor = anchors[0]
                    t_ocr = time.perf_counter()
                    hex_text, _ = ocr.texto_region(p.imagen, (anchor.x0 - 3, anchor.y0 - 4, p.ancho, anchor.y0 + 45),
                                                   scale, whitelist="0123456789abcdefABCDEF", psm=6)
                    additional_ocr_s += time.perf_counter() - t_ocr
                    cufe_verified = cufe in re.sub(r"\s", "", hex_text).lower()
                    log.append("RELECTURA HEXADECIMAL CUFE: " + hex_text)
        all_text.append("\n".join(log))
        read_s += additional_ocr_s
        parse_s += time.perf_counter() - t - additional_ocr_s
    for number, item in enumerate(items, 1):
        item.update(metadata, archivo=pdf_path.name, cufe=cufe, linea_producto=number)
    frame = pd.DataFrame(items, columns=COLUMNS)
    for column in ("archivo", "cufe", "numero_factura", "fecha_emision", "nit_emisor", "codigo", "descripcion"):
        frame[column] = frame[column].astype("string")
    (text_dir / f"{pdf_path.stem}.txt").write_text("\n\n".join(all_text), encoding="utf-8")
    missing = [key for key, value in metadata.items() if not value]
    if missing:
        warnings.append("Cabecera incompleta: " + ", ".join(missing))
    for numero, item in enumerate(items, 1):
        ausentes = [key for key in FIELDS[3:] if item[key] is None or item[key] == ""]
        if ausentes:
            warnings.append(f"Producto {numero}: campos ausentes ({', '.join(ausentes)}); revisar la fuente.")
    if not items:
        warnings.append("No se extrajeron productos. El CSV tendrá cabecera, sin filas.")
    if cufe and not cufe_verified:
        warnings.append("CUFE no verificado dentro del PDF (ausente o ilegible).")
    if compared and equal < compared:
        warnings.append(f"El OCR difiere de la capa de texto del PDF en {compared - equal} de {compared} "
                        f"campos (detalle en textos/{pdf_path.stem}.txt).")
    present = sum(bool(metadata[key]) for key in FIELDS[:3])
    present += sum(item[key] is not None and item[key] != "" for item in items for key in FIELDS[3:])
    expected = 3 + 4 * len(items)
    stats = {**metadata, "paginas": len(pages), "paginas_ocr": sum(p.ocr for p in pages),
             "fuente_texto": "ocr" if all(p.ocr for p in pages) else ("pdf" if not any(p.ocr for p in pages) else "mixta"),
             "separador_decimal": sep, "productos": len(items),
             "campos_presentes": present, "campos_esperados": expected,
             "cobertura_campos_pct": round(100 * present / expected, 2),
             "confianza_ocr_media": round(statistics.mean(confidences), 2) if confidences else None,
             "campos_comparados_ocr_pdf": compared,
             "concordancia_ocr_pdf_pct": round(100 * equal / compared, 2) if compared else None,
             "render_png_s": round(render_s, 6), "lectura_ocr_s": round(read_s, 6), "parseo_s": round(parse_s, 6),
             "extraccion_s": round(time.perf_counter() - start, 6),
             "advertencias": warnings}
    return frame, stats
