"""Configuración local sin imprimir la clave ni modificar el entorno del proceso."""
import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import dian_rpa


class ConfiguracionClaveTest(unittest.TestCase):
    def test_env_local_por_defecto_y_sin_efectos_secundarios(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / ".env").write_text('\ufeff# Evaluación\nTWOCAPTCHA_API_KEY="clave-falsa-local"\n', encoding="utf-8")
            with patch.dict(os.environ, {}, clear=True), patch.object(dian_rpa, "BASE", root), \
                    patch("sys.stdout", new_callable=io.StringIO) as output:
                self.assertEqual(dian_rpa.obtener_clave(), "clave-falsa-local")
                self.assertNotIn("TWOCAPTCHA_API_KEY", os.environ)
                self.assertNotIn("clave-falsa-local", output.getvalue())

    def test_variable_del_entorno_tiene_prioridad(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "config.env"
            path.write_text("TWOCAPTCHA_API_KEY=clave-falsa-archivo\n")
            with patch.dict(os.environ, {"TWOCAPTCHA_API_KEY": "clave-falsa-entorno"}):
                self.assertEqual(dian_rpa.obtener_clave(env_file=path), "clave-falsa-entorno")

    def test_archivo_explicito_ausente_o_invalido_no_revela_contenido(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "config.env"
            with patch.dict(os.environ, {}, clear=True):
                with self.assertRaises(dian_rpa.CaptchaError):
                    dian_rpa.obtener_clave(env_file=path)
                for text in ('TWOCAPTCHA_API_KEY="valor-privado\n',
                             'TWOCAPTCHA_API_KEY=valor-privado\nTWOCAPTCHA_API_KEY=otro\n',
                             'OTRA_VARIABLE=valor-privado\n'):
                    path.write_text(text)
                    with self.assertRaises(dian_rpa.CaptchaError) as caught:
                        dian_rpa.obtener_clave(env_file=path)
                    self.assertNotIn("valor-privado", str(caught.exception))

    def test_sin_archivo_predeterminado_devuelve_clave_vacia(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch.dict(os.environ, {}, clear=True), patch.object(dian_rpa, "BASE", Path(folder)):
                self.assertEqual(dian_rpa.obtener_clave(), "")


if __name__ == "__main__":
    unittest.main()
