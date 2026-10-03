"""El modo gratuito nunca consume saldo; la clave no se persiste ni se imprime."""
import io
import json
import os
import unittest
from unittest.mock import patch

from playwright.sync_api import sync_playwright

import dian_rpa
from dian_rpa import CaptchaManager, TwoCaptcha, VerificacionPendiente


class ProveedorProhibido:
    key = "clave-falsa-que-no-debe-usarse"

    def resolver(self, task):
        raise AssertionError("El modo gratuito intentó consumir 2Captcha")


class ModosCaptchaTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch(headless=True)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()

    def pagina(self, script=""):
        context = self.browser.new_context()
        self.addCleanup(context.close)
        page = context.new_page()
        page.set_content('<div class="cf-turnstile" data-sitekey="sitekey-prueba">'
                         '<input type="hidden" name="cf-turnstile-response"></div>' + script)
        return page

    def test_gratis_agotado_no_usa_proveedor_aunque_exista_clave(self):
        page = self.pagina()
        manager = CaptchaManager("gratis", ProveedorProhibido(), wait_s=0)
        manager.manual_wait_s = .01
        with self.assertRaises(VerificacionPendiente):
            manager.resolver(page)
        self.assertEqual(manager.calls, 0)

    def test_gratis_continua_cuando_llega_token_tras_aviso(self):
        page = self.pagina()
        manager = CaptchaManager("gratis", ProveedorProhibido(), wait_s=0)
        manager.manual_wait_s = 1
        page.evaluate("setTimeout(() => document.querySelector('input').value = "
                      "'token-de-prueba-entregado-por-el-widget', 150)")
        manager.resolver(page)
        self.assertEqual(manager.calls, 1)
        self.assertEqual(manager.providers, ["widget_tras_aviso"])
        self.assertEqual(manager.intervenciones_solicitadas, 1)

    def test_widget_sin_token_no_recurre_a_clave_disponible(self):
        manager = CaptchaManager("widget", ProveedorProhibido(), wait_s=0)
        with self.assertRaises(VerificacionPendiente):
            manager.resolver(self.pagina())

    def test_clave_interactiva_no_modifica_entorno_ni_sale_en_stdout(self):
        self.assertTrue(callable(getattr(dian_rpa, "obtener_clave", None)),
                        "Falta lectura de clave con entrada oculta")
        secret = "clave-falsa-ingresada-en-terminal"
        with patch.dict(os.environ, {"TWOCAPTCHA_API_KEY":"clave-previa"}), \
             patch("sys.stdin.isatty", return_value=True), \
             patch("getpass.getpass", return_value=secret), \
             patch("sys.stdout", new_callable=io.StringIO) as output:
            key = dian_rpa.obtener_clave(pedir=True)
            self.assertEqual(key, secret)
            self.assertEqual(os.environ["TWOCAPTCHA_API_KEY"], "clave-previa")
            self.assertNotIn(secret, output.getvalue())

    def test_error_remoto_no_imprime_clave_si_proveedor_la_repite(self):
        secret = "clave-falsa-que-repite-el-proveedor"
        provider = TwoCaptcha(secret)
        response = io.BytesIO(json.dumps({'errorId':1, 'errorCode':'ERROR_KEY_DOES_NOT_EXIST',
                                         'errorDescription':f'Clave incorrecta {secret}'}).encode())
        with patch("urllib.request.urlopen", return_value=response):
            with self.assertRaises(dian_rpa.CaptchaError) as caught:
                provider._post('getBalance', {'clientKey':secret})
        self.assertNotIn(secret, str(caught.exception))

    def test_metricas_por_tarea_excluyen_clave_token_id_e_ip(self):
        secret = "clave-falsa-privada"
        token = "token-falso-privado"
        provider = TwoCaptcha(secret)
        respuestas = [
            {'taskId':123456, 'errorId':0},
            {'status':'processing', 'errorId':0},
            {'status':'ready', 'errorId':0, 'solution':{'token':token},
             'cost':'0.00145', 'createTime':100, 'endTime':106, 'ip':'127.0.0.99'},
        ]
        with patch.object(provider, '_post', side_effect=respuestas), patch('time.sleep'):
            self.assertEqual(provider.resolver({'type':'TurnstileTaskProxyless'}), token)
        detail = provider.details[0]
        self.assertEqual(detail['consultas_resultado'], 2)
        self.assertEqual(detail['tiempo_proveedor_s'], 6)
        self.assertEqual(detail['coste_reportado_usd'], '0.00145')
        serialized = json.dumps(provider.details)
        for private in [secret,token,'123456','127.0.0.99']:
            self.assertNotIn(private, serialized)


if __name__ == "__main__":
    unittest.main()
