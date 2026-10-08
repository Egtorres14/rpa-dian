"""La descripción multilínea no debe invadir el código ni perder su última línea."""
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

import pymupdf
from PIL import Image

from extraccion import OCR, _comparar, extraer_factura, productos_celdas


class ExtraccionTest(unittest.TestCase):
    def extraer_imagen(self, nombre, dpi=300):
        imagen = Path(__file__).parent / "fixtures" / nombre
        ocr = OCR()
        ocr.comprobar()
        with tempfile.TemporaryDirectory() as carpeta:
            raiz = Path(carpeta)
            archivo = raiz / "factura.pdf"
            with Image.open(imagen) as png:
                ancho, alto = png.size
            with pymupdf.open() as doc:
                page = doc.new_page(width=ancho * 72 / 300, height=alto * 72 / 300)
                page.insert_image(page.rect, filename=str(imagen))
                doc.save(archivo)
            frame, stats = extraer_factura(archivo, raiz / "salida", ocr, dpi=dpi)
            return frame, stats

    def test_tabla_rasterizada_con_descripcion_multilinea(self):
        frame, stats = self.extraer_imagen("tabla_multilinea.png")
        self.assertEqual(len(frame), 1)
        fila = frame.iloc[0]
        self.assertEqual(fila["codigo"], "SPON1")
        self.assertEqual(fila["descripcion"], "CONSULTA DE PRIMERA V EZ POR NUTRICION Y DIE TETICA")
        self.assertEqual(fila["cantidad"], Decimal("1.00"))
        self.assertEqual(fila["precio_unitario"], Decimal("42400.00"))
        self.assertEqual(stats["fuente_texto"], "ocr")

    def test_400_dpi_conserva_la_deteccion_de_columnas(self):
        frame, _ = self.extraer_imagen("tabla_multilinea.png", dpi=400)
        self.assertEqual(len(frame), 1)
        self.assertEqual(frame.iloc[0]["codigo"], "SPON1")
        self.assertEqual(frame.iloc[0]["precio_unitario"], Decimal("42400.00"))

    def test_letra_junto_al_borde_de_columna_se_conserva(self):
        frame, _ = self.extraer_imagen("tabla_bordes.png")
        self.assertEqual(len(frame), 6)
        self.assertEqual(frame.iloc[4]["descripcion"],
                         "CPN HEPATITIS B ANTIGE NO DE SUPERFICIE PRUEB A RAPIDA")

    def test_campo_ausente_en_ocr_y_pdf_no_inflaciona_concordancia(self):
        meta = {"numero_factura": "FE-1", "fecha_emision": "2024-11-04", "nit_emisor": "819001302"}
        filas = [{"codigo": None, "descripcion": "Consulta", "cantidad": Decimal("1"),
                  "precio_unitario": Decimal("52000")}]
        compared, same, differences = _comparar(meta, filas, meta, filas)
        self.assertEqual(compared, 6)
        self.assertEqual(same, 6)
        self.assertEqual(differences, [])

    def test_codigo_con_baja_confianza_se_relee_en_otra_resolucion(self):
        fixtures = Path(__file__).parent / "fixtures"
        ocr = OCR()
        ocr.comprobar()
        imagen = fixtures / "codigo_400.png"
        scale = 400 / 72
        with Image.open(imagen) as png:
            ancho, alto = png.size
        geometry = ({"codigo": (0, ancho / scale)}, [(0, alto / scale)])
        filas = productos_celdas(geometry, 1, ",", ocr=ocr, image_path=imagen, scale=scale,
                                 alternative_image_path=fixtures / "codigo_300.png", alternative_scale=300 / 72)
        self.assertEqual(filas[0]["codigo"], "S19911")


if __name__ == "__main__":
    unittest.main()
