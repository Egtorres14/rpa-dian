# RPA DIAN: descarga de facturas y extracción OCR

Consulta facturas por CUFE en el portal de la DIAN, descarga su representación
gráfica y extrae los datos desde imágenes del PDF. Genera un DataFrame y un CSV
por factura, un consolidado y un reporte de tiempos y calidad.

Está desarrollado en Python con Playwright, 2Captcha, PyMuPDF, Tesseract y pandas.
El lote de evaluación contiene diez facturas. Según el enunciado, los datos son
ficticios y los resultados tienen fines exclusivamente evaluativos, sin uso
comercial ni compensación económica. El plazo de la prueba es de 24 horas.

## Probar desde GitHub sin instalar nada

Abre una [solicitud de evaluación](https://github.com/Egtorres14/rpa-dian/issues/new?template=evaluacion.yml)
y elige una o diez facturas. La solicitud inicia el flujo de Actions y espera
mi aprobación antes de usar el saldo de 2Captcha. No necesitas una API key.

Una vez aprobado, busca la ejecución correspondiente en
[Actions](https://github.com/Egtorres14/rpa-dian/actions/workflows/evaluacion.yml)
y descarga el artefacto **resultados**. Incluye PDF, CSV por factura, consolidado
y métricas. Los resultados son públicos y el artefacto se conserva siete días.

Como propietario, también puedo iniciar la modalidad `demo` o `dian` con
**Run workflow** en la rama `main`. La demo procesa las diez muestras sin gastar
saldo. La descarga nueva usa el secreto `TWOCAPTCHA_API_KEY` del entorno
`evaluacion`; la clave no se incluye en el código ni en el ZIP de resultados.

El flujo acepta únicamente el lote incluido y un intento por factura, con
un máximo de dos tareas CAPTCHA por factura. Los tiempos del robot excluyen
instalación, cola y aprobación. [Configuración y cierre de la evaluación](docs/GITHUB_ACTIONS.md).

## Instalación

Probado en Windows con Python 3.12.10. Desde PowerShell:

```powershell
git clone https://github.com/Egtorres14/rpa-dian.git
cd rpa-dian
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m playwright install chromium
winget install -e --id UB-Mannheim.TesseractOCR
```

Tesseract debe tener los idiomas `spa` y `eng`. Compruébalo con:

```powershell
& "C:\Program Files\Tesseract-OCR\tesseract.exe" --list-langs
```

Si falta español, copia `spa.traineddata` del
[repositorio de Tesseract](https://github.com/tesseract-ocr/tessdata) a la carpeta
`tessdata` de la instalación. El programa busca Tesseract en PATH y en su ruta
habitual de Windows; otra ubicación se indica con `--tesseract RUTA`.

Si PowerShell bloquea la activación, utiliza `.\.venv\Scripts\python.exe` en
lugar de `python` en los comandos siguientes. No hace falta cambiar la política
de ejecución. Las versiones usadas en la evaluación están en
[entorno.json](docs/metricas/entorno.json).

## Primera prueba: demo gratuita

```powershell
python dian_rpa.py extraer --demo --out resultados_demo
```

Procesa los diez PDF incluidos en `muestras/pdf/` con OCR real. Después de
instalar las dependencias, funciona sin Internet, navegador ni API key. Permite
evaluar la extracción; para probar una descarga nueva se usa el comando `dian`.
Los CSV de referencia están en [muestras/csv](muestras/csv).

**Resultado esperado:** diez archivos procesados, nueve `OK` y uno `REVISAR`.
La factura `HTFE-6599` tiene el código de producto vacío en el documento original.
Se conserva vacío y el programa termina con código de salida `1` para señalar
la observación. No se completa ese dato por inferencia.

## Consulta y descarga desde DIAN

### CAPTCHA automático con 2Captcha

Requiere conexión y una cuenta de 2Captcha con saldo. Para probar una factura:

```powershell
python dian_rpa.py verificar-captcha --pedir-clave
python dian_rpa.py dian --captcha 2captcha --pedir-clave --limite 1 --out resultados_pago
```

La clave se introduce con entrada oculta y queda en memoria. La verificación
consulta el saldo sin crear tareas CAPTCHA. Para procesar las diez facturas,
quita `--limite 1`. El robot resuelve por separado el CAPTCHA de búsqueda y el
de descarga, acepta el aviso de contraseña y valida el PDF recibido.

También acepta `TWOCAPTCHA_API_KEY` como variable de entorno. `.env.example`
documenta esa variable, pero el programa no carga archivos `.env` automáticamente.
Al ejecutar una copia local, cada evaluador usa su propia clave. Para utilizar
el saldo de evaluación, solicita una prueba desde GitHub como se explica arriba.
Clonar el repositorio no descarga los secretos de Actions.

### Opción gratuita con asistencia

```powershell
python dian_rpa.py dian --captcha gratis --limite 1 --out resultados_gratis
```

Abre el navegador y espera el token del widget. Si hace falta, solicita marcar
la casilla; continúa al recibir la verificación. Nunca llama a 2Captcha, aunque
haya una clave configurada. La espera asistida es de 120 segundos por CAPTCHA
y se ajusta con `--verificacion-espera`. Requiere un navegador visible.

En las pruebas del portal, el widget no produjo un token automático después de
15 segundos. Este modo puede necesitar ayuda humana; la automatización integral
del punto 1 se comprobó con 2Captcha.

Más opciones y protección de credenciales:
[Claves y modalidades](docs/CLAVES_Y_MODALIDADES.md).

## Cómo funciona

1. Valida los CUFE y llena el formulario de consulta con CUFE y NIT.
2. Resuelve los dos CAPTCHA con tokens independientes. El formulario de descarga
   conserva cookies y CSRF de la sesión del portal.
3. Guarda el PDF y comprueba su cabecera, páginas y contraseña. Rechaza HTML,
   archivos corruptos y contraseñas incorrectas.
4. Exporta las páginas a PNG. Tesseract localiza secciones y columnas a 300 DPI
   y lee las celdas a 400 DPI, conservando las descripciones multilínea.
5. Crea un DataFrame por PDF y exporta los CSV, el consolidado y las métricas.
   El texto nativo del PDF sirve para medir concordancia; los campos exportados
   proceden del OCR de las imágenes.

`cufes.json` contiene los CUFE y el NIT usado para consultar y abrir este lote.
El **NIT del emisor que se exporta se lee del documento** y puede ser distinto
del NIT de consulta. Para otro lote hay que proporcionar sus CUFE y NIT válidos.

## Datos y archivos de salida

| Campo solicitado | Columna CSV | Formato |
|---|---|---|
| Número de factura | `numero_factura` | Texto |
| Fecha de emisión | `fecha_emision` | AAAA-MM-DD |
| NIT del emisor | `nit_emisor` | Texto sin separadores |
| Código | `codigo` | Texto, conserva ceros |
| Descripción | `descripcion` | Texto |
| Cantidad | `cantidad` | Decimal con punto |
| Precio unitario | `precio_unitario` | Decimal con punto |

Cada fila representa un producto. Se agregan `archivo`, `cufe`, `pagina` y
`linea_producto` para identificar su origen. Los CSV se guardan en UTF-8 con BOM.

| Ruta dentro de la carpeta de salida | Contenido |
|---|---|
| `pdf/` | Originales descargados |
| `imagenes/`, `textos/` | PNG, texto OCR y comparación con el texto nativo |
| `csv/`, `facturas_consolidadas.csv` | Un CSV por factura y consolidado |
| `metricas.csv`, `detalle_proceso.json` | Tiempos y calidad por archivo |
| `resumen_metricas.json`, `reporte_metricas.md` | Totales y restricciones |
| `diagnostico/` | Captura y HTML cuando falla el portal |

Los estados son `OK`, `REVISAR`, `ERROR` y `OMITIDA`. La salida es `0` sólo si
todas las facturas quedan `OK`; cualquier otro estado devuelve `1`. Los
resultados generados y diagnósticos están excluidos de Git.

## Tiempos y restricciones

| Ejecución medida | Tiempo | Coste reportado |
|---|---:|---:|
| DIAN: consulta, descarga y OCR de diez facturas | 322,119 s | USD 0,02900 |
| DIAN: una factura completa | 28,627 s | USD 0,00290 |
| Demo: OCR de diez PDF locales | 65,128 s | USD 0 |

El lote integral produjo diez PDF, veinte páginas y veintiún productos. La
concordancia con el texto nativo fue del 100 % en 113 campos comparables; hubo
113 de 114 campos presentes. La concordancia no equivale a una validación con
anotaciones humanas independientes.

En la comparación de rendimiento, la extracción local bajó de 105,355 a 70,335 s
(33,24 %) y el flujo completo de 377,952 a 322,119 s (14,77 %). Se conservaron
los diez CSV. Son ejecuciones individuales en el mismo equipo; la conexión,
DIAN y la latencia de 2Captcha afectan los tiempos.

Por defecto se usan cuatro lecturas OCR simultáneas y una pausa de un segundo
entre facturas. `--ocr-workers 1` reduce la concurrencia. Las consultas al portal
son secuenciales, con dos intentos por factura y un límite de 25 tareas CAPTCHA.
La espera CAPTCHA ya está incluida en los tiempos de consulta y descarga:
no deben sumarse esas tres columnas. La memoria registrada por Python excluye
Chromium y Tesseract. Si cambia el portal o el formato del PDF, habrá que ajustar
`selectores.json` o las reglas de extracción.

Detalle de las mediciones: [comparación](docs/COMPARACION_MODALIDADES.md),
[evidencia](docs/evidencia_modalidades.json) y [métricas](docs/metricas).

## Pruebas e informe

```powershell
python -m unittest discover -s tests -v
python generar_informe.py --resultados docs/metricas --destino entrega --repositorio https://github.com/Egtorres14/rpa-dian --optimizacion docs/metricas/optimizacion_tiempos.json --modalidades docs/evidencia_modalidades.json
```

Las veinticuatro pruebas verifican la descarga con dos CAPTCHA y CSRF, archivos
inválidos, extracción de tablas, concurrencia, caché, solicitudes de GitHub,
resultados incompletos y protección de credenciales.
Usan un portal controlado, Chromium y Tesseract, sin consultar DIAN ni gastar saldo.

El [informe de la prueba técnica](docs/INFORME_PRUEBA_TECNICA.pdf) contiene las
respuestas y resultados medidos. Su [versión Markdown](docs/INFORME_PRUEBA_TECNICA.md)
permite revisarlo desde GitHub. Para generar el informe de una ejecución nueva,
cambia `--resultados` por su carpeta de salida y omite las comparaciones históricas.

Otros comandos útiles:

```powershell
python dian_rpa.py dian --captcha 2captcha --pedir-clave --resume --out resultados_pago
python dian_rpa.py extraer --pdf-dir resultados_pago/pdf --out resultados_extraer
python dian_rpa.py dian --help
```

`--resume` reutiliza los PDF válidos y vuelve a extraer sus datos. El modo
`--sin-ocr` lee el texto nativo para diagnóstico; la evaluación de imágenes
se realiza con el OCR predeterminado.
