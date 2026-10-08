# Clave protegida y alternativas gratuitas

## Dos comandos para probar el portal

Pago, ambos CAPTCHA automáticos:

```powershell
python dian_rpa.py dian --captcha 2captcha --pedir-clave --out resultados_pago
```

La terminal pide la clave con entrada oculta. La clave queda en memoria del
proceso; no se guarda en un archivo ni en el historial como argumento. Si ya
está configurada `TWOCAPTCHA_API_KEY`, se puede omitir `--pedir-clave`.
También se admite un `.env` local junto al programa o `--env-file RUTA`.
La entrada oculta tiene prioridad sobre la variable de entorno; la variable
tiene prioridad sobre el archivo. Se lee sólo esa clave sin modificar el
entorno global. El archivo es texto plano y debe mantenerse fuera de Git.

Gratuito, widget y ayuda de la persona cuando sea necesaria:

```powershell
python dian_rpa.py dian --captcha gratis --out resultados_gratis
```

El robot espera dos segundos al widget. Si no hay token, muestra un aviso para
que la persona marque la casilla en el navegador; continúa al recibir el token.
Lo hace para la búsqueda y para la descarga. No usa 2Captcha aunque exista una
clave en el entorno. `--verificacion-espera 180` amplía la espera asistida en
ambos pasos. Este modo requiere un navegador visible y no admite `--headless`.

Para una primera prueba se puede añadir `--limite 1` a cualquiera de los dos.

## Demo gratuita y automática del OCR

```powershell
python dian_rpa.py extraer --demo --out resultados_demo
```

Incluye los diez PDF en `muestras/pdf/`. Procesa las imágenes con Tesseract sin
Internet ni CAPTCHA y conserva las validaciones de calidad. Evalúa el punto 2;
no pretende demostrar una descarga nueva del portal.

## Comprobar la API y explorar el widget

```powershell
python dian_rpa.py verificar-captcha --pedir-clave
python dian_rpa.py dian --captcha widget --limite 1 --out resultados_widget
python dian_rpa.py dian --captcha auto --captcha-wait 2 --out resultados_hibrido
```

`verificar-captcha` consulta `getBalance`, informa si la clave es válida y hay
saldo positivo, sin crear tareas, mostrar el importe ni guardar la credencial.
`widget` sólo espera un token automático durante 15 segundos; si no llega, se
detiene sin recurrir al servicio. `auto` intenta primero el widget y luego usa
2Captcha cuando tiene clave. El modo híbrido puede evitar algunos pagos cuando
el widget completa la verificación; también añade espera cuando no la completa.
No se ha demostrado que sea más rápido en este portal.

Turnstile Managed puede pedir una casilla según las señales del navegador.
Las pruebas del portal en Chromium y Chrome no produjeron un token nativo
tras 15 segundos. No se puede garantizar una solución gratuita completamente
automática para todos los CAPTCHA de DIAN. La implementación gratuita admite
intervención; el modo de pago es el utilizado para demostrar la automatización
integral exigida por el enunciado.

## Publicar sin exponer la clave

Una variable de entorno protege la clave frente a su inclusión accidental en
GitHub; no la oculta a quien controla el equipo donde corre Python. Cifrar u
ofuscar una clave dentro de un cliente descargable tampoco permite garantizar
su confidencialidad frente al usuario de ese cliente.

Hay dos formas de ofrecer el proyecto:

1. **Ejecución local:** cada evaluador usa su propia clave con entrada oculta.
   El repositorio contiene código, muestras y `.env.example` con un marcador.
2. **Usar el saldo del propietario:** el RPA de pago corre en un servidor
   controlado o en GitHub Actions. La clave se almacena como secreto del entorno
   y sólo llega al proceso del robot. El evaluador recibe PDF, CSV y métricas.

Para la segunda opción, el servicio debe autenticar al evaluador, limitar los
CUFE al lote autorizado y aplicar límites de ejecuciones y tareas CAPTCHA. El
límite local `--max-captcha-tasks` controla una ejecución; no sustituye una
cuota global del servidor.

GitHub permite secretos de entorno con revisores requeridos. Un flujo manual
ejecutado por el propietario, desde código revisado y con acceso limitado, es
una opción para esta prueba. No se debe dar permiso de edición del workflow a
personas sin confianza para permitirles usar el secreto. Los forks no reciben
los secretos del repositorio original. El evaluador puede revisar los resultados
sin recibir la API key. Este repositorio tiene un flujo de Actions y un formulario
de solicitud configurados para esta evaluación. El entorno `evaluacion` guarda
la clave y exige la aprobación del propietario antes de liberarla al robot.
La demo no usa ese entorno. [Pasos de uso y cierre](GITHUB_ACTIONS.md).

## Tiempos y límites

Las mediciones registradas se comparan en [evidencia_modalidades.json](evidencia_modalidades.json)
y [COMPARACION_MODALIDADES.md](COMPARACION_MODALIDADES.md). El lote de pago de diez facturas tardó
322,119 s, con 20 tareas, 195,291 s de CAPTCHA incluidos en el total y coste
reportado USD 0,02900. La extracción local de los mismos PDF tardó 70,335 s.
Son mediciones individuales, no garantías para otros equipos o condiciones.

Los registros nuevos incluyen `captcha_modo`, avisos de intervención y, por
tarea, consultas al proveedor, coste, tiempo del cliente y tiempo declarado
por 2Captcha. No guardan clave, token, ID de tarea ni IP del proveedor. El
tiempo de CAPTCHA incluye esperas automáticas y asistidas; la espera humana
no se interpreta como coste de CPU ni como tiempo automático del proveedor.

2Captcha indica que se esperen al menos cinco segundos entre consultas de
resultados pendientes; se conserva ese intervalo. Su API permite callbacks
para recibir la solución en un servidor registrado y reducir la espera por
sondeo. Es una alternativa para el futuro servidor protegido, sin una mejora
de tiempo medida todavía ni garantía de eliminar la latencia de resolución.

## Referencias oficiales

- [Comportamiento de los widgets Turnstile](https://developers.cloudflare.com/turnstile/concepts/widget/).
- [getBalance](https://2captcha.com/api-docs/get-balance).
- [getTaskResult e intervalo de espera](https://2captcha.com/api-docs/get-task-result).
- [createTask y callbackUrl](https://2captcha.com/api-docs/create-task).
- [Secretos de GitHub Actions](https://docs.github.com/en/actions/concepts/security/secrets).
- [Uso de secretos y restricciones de forks](https://docs.github.com/en/actions/how-tos/write-workflows/choose-what-workflows-do/use-secrets).
