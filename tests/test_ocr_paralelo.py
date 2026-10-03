"""El OCR concurrente debe conservar el orden y limitar trabajo y lecturas de disco."""
import tempfile
import threading
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import pymupdf
from PIL import Image

from extraccion import OCR, extraer_factura, productos_celdas


class OCRParaleloTest(unittest.TestCase):
    def test_lectura_de_paginas_concurrente_conserva_orden(self):
        fixture = Path(__file__).parent / "fixtures" / "tabla_multilinea.png"
        ocr = OCR()
        ocr.comprobar()
        leer_original = ocr.palabras
        lock = threading.Lock()
        dos_lecturas = threading.Event()
        activas = pico = 0

        def leer(path, scale):
            nonlocal activas, pico
            with lock:
                activas += 1
                pico = max(pico, activas)
                if activas == 2:
                    dos_lecturas.set()
            dos_lecturas.wait(.15)
            try:
                return leer_original(path, scale)
            finally:
                with lock:
                    activas -= 1

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            path = root / "dos_paginas.pdf"
            with Image.open(fixture) as image:
                width, height = image.size
            with pymupdf.open() as doc:
                for _ in range(2):
                    page = doc.new_page(width=width * 72 / 300, height=height * 72 / 300)
                    page.insert_image(page.rect, filename=str(fixture))
                doc.save(path)
            with patch.object(ocr, "palabras", side_effect=leer):
                frame, stats = extraer_factura(path, root, ocr)
            ocr.limpiar_imagenes()
        self.assertGreater(pico, 1, "Las páginas todavía se leen secuencialmente")
        self.assertEqual(list(frame["pagina"]), [1, 2])
        self.assertEqual(list(frame["codigo"]), ["SPON1", "SPON1"])
        self.assertEqual(stats["paginas_ocr"], 2)

    def test_celdas_concurrentes_limitadas_y_resultados_en_orden(self):
        ocr = OCR()
        ocr.workers = 4
        lock = threading.Lock()
        cuatro_lecturas = threading.Event()
        activas = pico = 0

        def leer(_path, box, _scale, **_kwargs):
            nonlocal activas, pico
            with lock:
                activas += 1
                pico = max(pico, activas)
                if activas == 4:
                    cuatro_lecturas.set()
            cuatro_lecturas.wait(.15)
            with lock:
                activas -= 1
            columna, fila = int(box[0] / 10), int(box[1] / 20)
            return ((f"C{fila}", f"Producto {fila}", "2,00", "45,00")[columna], 95)

        columnas = dict(zip(("codigo", "descripcion", "cantidad", "precio_unitario"),
                            ((0, 10), (10, 20), (20, 30), (30, 40))))
        with patch.object(ocr, "texto_region", side_effect=leer):
            filas = productos_celdas((columnas, [(0, 20), (20, 40)]), 1, ",",
                                     ocr=ocr, image_path=Path("control.png"))
        self.assertGreater(pico, 1, "Las celdas todavía se leen secuencialmente")
        self.assertLessEqual(pico, 4)
        self.assertEqual([r["codigo"] for r in filas], ["C0", "C1"])
        self.assertEqual([r["descripcion"] for r in filas], ["Producto 0", "Producto 1"])
        self.assertEqual(filas[1]["cantidad"], Decimal("2.00"))
        self.assertEqual(filas[1]["precio_unitario"], Decimal("45.00"))

    def test_imagen_se_decodifica_una_vez_y_se_refresca_si_cambia(self):
        ocr = OCR()
        fixture = Path(__file__).parent / "fixtures" / "codigo_300.png"
        ocr.comprobar()
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "celda.png"
            path.write_bytes(fixture.read_bytes())
            with Image.open(path) as image:
                width, height = image.size
            box = (0, 0, width, height)
            with patch("extraccion.Image.open", wraps=Image.open) as abrir:
                self.assertEqual(ocr.texto_region(path, box, 1, psm=6, quitar_lineas=True)[0], "S19911")
                self.assertEqual(ocr.texto_region(path, box, 1, psm=6, quitar_lineas=True)[0], "S19911")
                self.assertEqual(abrir.call_count, 1, "Se decodificó otra vez la misma imagen")
                Image.new("RGB", (width, height), "white").save(path)
                self.assertEqual(ocr.texto_region(path, box, 1, psm=6)[0], "")
                self.assertEqual(abrir.call_count, 2)


if __name__ == "__main__":
    unittest.main()
