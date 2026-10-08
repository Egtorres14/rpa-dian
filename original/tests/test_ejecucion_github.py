import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from ejecutar_github import empaquetar, solicitud, validar_resultados


class EjecucionGitHubTest(unittest.TestCase):
    def test_solicitud_manual_permite_solo_opciones_fijas(self):
        self.assertEqual(solicitud("workflow_dispatch", {"inputs": {"modalidad": "dian", "limite": "1"}}), ("dian", 1))
        self.assertEqual(solicitud("workflow_dispatch", {"inputs": {"modalidad": "demo", "limite": "1"}}), ("demo", 10))
        for inputs in ({"modalidad": "dian", "limite": "100"}, {"modalidad": "bash", "limite": "1"}):
            with self.assertRaises(ValueError):
                solicitud("workflow_dispatch", {"inputs": inputs})

    def test_issue_exige_etiqueta_y_cantidad_exacta(self):
        event = {"issue": {"labels": [{"name": "evaluacion-rpa"}], "body": "### Facturas\n\n10\n\n### Comentarios\n\nPrueba"}}
        self.assertEqual(solicitud("issues", event), ("dian", 10))
        for body in ("### Facturas\n\n1; echo secreto", "### Facturas\n\n100", "### Facturas\n\n1\n\n### Facturas\n\n10"):
            event["issue"]["body"] = body
            with self.assertRaises(ValueError):
                solicitud("issues", event)
        event["issue"]["labels"] = []
        with self.assertRaises(ValueError):
            solicitud("issues", event)

    def preparar_resultados(self, root, estado="REVISAR"):
        (root / "csv").mkdir()
        (root / "csv/factura_01_eccc372e4bdb.csv").write_text("codigo,descripcion\n,Producto\n", encoding="utf-8")
        (root / "facturas_consolidadas.csv").write_text("codigo,descripcion\n,Producto\n", encoding="utf-8")
        summary = {"facturas_solicitadas": 1, "ok": int(estado == "OK"), "revisar": int(estado == "REVISAR"), "errores": int(estado == "ERROR"), "omitidas": 0, "filas_productos": 1}
        records = [{"archivo": "factura_01_eccc372e4bdb.pdf", "estado": estado, "productos": 1}]
        (root / "resumen_metricas.json").write_text(json.dumps(summary))
        (root / "detalle_proceso.json").write_text(json.dumps(records))
        for name in ("metricas.csv", "reporte_metricas.md"):
            (root / name).write_text("Métricas", encoding="utf-8")

    def test_revision_completa_acepta_salida_uno(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            self.preparar_resultados(root)
            self.assertEqual(validar_resultados(root, 1, 1)[0]["revisar"], 1)

    def test_error_o_lote_incompleto_no_se_ocultan(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            with self.assertRaises((ValueError, FileNotFoundError)):
                validar_resultados(root, 1, 1)
            self.preparar_resultados(root, "ERROR")
            with self.assertRaises(ValueError):
                validar_resultados(root, 1, 0)
            with self.assertRaises(ValueError):
                validar_resultados(root, 10, 1)

    def test_csv_ausente_y_ruta_fuera_de_salida_fallan(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            self.preparar_resultados(root)
            (root / "csv/factura_01_eccc372e4bdb.csv").unlink()
            with self.assertRaises(FileNotFoundError):
                validar_resultados(root, 1, 1)
            (root / "detalle_proceso.json").write_text(json.dumps([{"archivo": "../secreto.pdf", "estado": "REVISAR", "productos": 1}]))
            with self.assertRaises(ValueError):
                validar_resultados(root, 1, 1)

    def test_zip_excluye_html_y_credenciales(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            self.preparar_resultados(root)
            pdfs = root / "pdf"
            pdfs.mkdir()
            (pdfs / "factura_01_eccc372e4bdb.pdf").write_bytes(b"%PDF-1.7 muestra")
            (root / "diagnostico").mkdir()
            (root / "diagnostico/sesion.html").write_text("cookie confidencial")
            (root / ".env").write_text("clave confidencial")
            _, records = validar_resultados(root, 1, 1)
            target = root / "resultados.zip"
            empaquetar(root, pdfs, records, target)
            with zipfile.ZipFile(target) as archive:
                self.assertFalse(any("diagnostico" in name or ".env" in name for name in archive.namelist()))
                self.assertIn("pdf/factura_01_eccc372e4bdb.pdf", archive.namelist())
            (root / "reporte_metricas.md").write_text("valor-privado-de-prueba")
            with self.assertRaises(ValueError):
                empaquetar(root, pdfs, records, target, secreto="valor-privado-de-prueba")


if __name__ == "__main__":
    unittest.main()
