# RPA DIAN y extracción OCR

Documento de respuestas · Prueba técnica

## Alcance de la evaluación

Esta es una prueba técnica con fines exclusivamente evaluativos. Según el enunciado, los datos son completamente ficticios y generados únicamente para evaluación. Los resultados y análisis no serán utilizados por la empresa para fines comerciales reales. La evaluación no conlleva compensación económica y su único objetivo es medir las competencias técnicas del aspirante. Duración indicada: 24 horas.

## Punto 1: consulta y descarga

Se consultan los diez CUFE con Playwright. El robot llena CUFE y NIT, resuelve el Turnstile de búsqueda y obtiene el documento. Resuelve también el segundo CAPTCHA con un token nuevo y la sitekey de la página del documento, mediante la API v2 de 2Captcha.

Pulsa Descargar PDF y acepta el aviso de contraseña. El JavaScript del portal envía su formulario POST con CAPTCHA, cookies y CSRF. El PDF se guarda y se valida por cabecera, páginas y contraseña; se rechazan respuestas HTML o corruptas. Se permiten hasta dos intentos por factura y se detiene el lote ante errores globales del proveedor o portal.

## Punto 2: imágenes, OCR y DataFrames

Cada página se exporta a PNG y se procesa con Tesseract. Con la configuración predeterminada se localiza la estructura a 300 DPI y se leen las celdas a 400 DPI. Las secciones identifican número de factura, fecha de emisión y NIT del emisor. Las líneas visibles de la tabla delimitan código, descripción, cantidad y precio unitario; cada celda se lee por separado. Se limpian líneas verdes en los recortes para conservar letras junto a los bordes.

El OCR ejecuta hasta 4 lecturas simultáneas de páginas o celdas. Reutiliza hasta dos imágenes decodificadas por factura y conserva el orden de resultados. PyMuPDF y las consultas DIAN se ejecutan en el hilo principal. Cada proceso Tesseract usa un hilo interno. Los tiempos OCR miden pared, sin sumar trabajos simultáneos.

Se crea un DataFrame por PDF, con una fila por producto y los datos de cabecera repetidos. Se exporta un CSV por archivo y un consolidado UTF-8 con BOM. Fecha: AAAA-MM-DD; cantidades y precios: Decimal con punto. Código y NIT son texto. Archivo, CUFE, página y línea de producto permiten rastrear cada registro.

La capa de texto del PDF se utiliza para contrastar el OCR; los valores exportados proceden de las imágenes. No se reemplazan errores de OCR con datos nativos ni se inventan campos ausentes. El NIT de consulta puede ser del receptor; el NIT del emisor se extrae de su sección en el PDF.

## Resultados medidos

Ejecución: 2026-10-02 22:04:12 a 2026-10-02 22:09:35 (America/Bogota). Modo: dian. Facturas: 10; OK: 9; REVISAR: 1; ERROR: 0; OMITIDAS: 0.

Páginas procesadas: 20; con OCR: 20; filas de producto: 21. Campos presentes: 113/114. Cobertura global: 99.12 %. Concordancia media por factura: 100.0 %; campos comparados: 113. Los campos vacíos en ambas fuentes no se cuentan como aciertos.

Optimización local, mismos diez PDF y mismo equipo: 105.355 s antes y 70.335 s después; reducción del 33.24 %. Los 10 CSV tienen exactamente los mismos valores. Se conserva el OCR de las imágenes, la resolución de 400 DPI y la relectura de códigos.

Flujo integral DIAN: 377.952 s antes y 322.119 s después; reducción observada del 14.77 %. La pausa cambia de 5 a 1 s: 36 s menos de espera programada para diez facturas. Son dos ejecuciones individuales; la latencia variable de 2Captcha impide atribuir toda la diferencia al código. Evidencia: docs/metricas/optimizacion_tiempos.json.

| # | Factura | Estado | Productos | Cobertura | Concordancia | Total (s) |
|---|---|---|---:|---:|---:|---:|
| 1 | FEBQ-243564 | OK | 1 | 100.0 % | 100.0 % | 38.97 |
| 2 | FE-184420 | OK | 1 | 100.0 % | 100.0 % | 32.23 |
| 3 | FEHP-620761 | OK | 2 | 100.0 % | 100.0 % | 38.00 |
| 4 | SFA-556956 | OK | 6 | 100.0 % | 100.0 % | 34.94 |
| 5 | FESR-15847 | OK | 1 | 100.0 % | 100.0 % | 26.77 |
| 6 | FC-1861 | OK | 5 | 100.0 % | 100.0 % | 29.28 |
| 7 | HTFE-6599 | REVISAR | 1 | 85.71 % | 100.0 % | 31.74 |
| 8 | CRIA-1406 | OK | 1 | 100.0 % | 100.0 % | 22.72 |
| 9 | HISF-277602 | OK | 2 | 100.0 % | 100.0 % | 27.88 |
| 10 | HUFE-510239 | OK | 1 | 100.0 % | 100.0 % | 27.43 |

Tiempo de esta ejecución: 322.119 s. Tareas CAPTCHA de la ejecución integral registrada: 20. Coste reportado: USD 0.02900.

## Restricciones y calidad

La factura HTFE-6599 tiene vacía la celda Código en el documento original, verificado visualmente. Se conserva el campo vacío y su estado REVISAR.

Cobertura significa presencia de campos, no exactitud. Concordancia OCR/PDF mide coincidencia con otra representación del mismo documento; no equivale a precisión contra una anotación humana independiente. La confianza de Tesseract tampoco es una probabilidad calibrada de exactitud.

Tiempo total: pared, incluye esperas y pausas; excluye instalación. CAPTCHA ya está incluido en consulta y descarga: no se suma otra vez. Memoria y CPU Python excluyen Chromium y Tesseract y no representan toda la RAM del equipo. Las lecturas simultáneas aumentan el uso de memoria de procesos externos. El coste incluye tareas cuyo coste devolvió la API; una tarea que agote el tiempo puede tener un cargo todavía pendiente.

Dependencias externas: disponibilidad de DIAN, formato HTML/PDF, NIT válido, conexión y saldo del proveedor. Turnstile utiliza tokens de un solo uso y 300 segundos de vigencia. Se evita reutilizar el token de búsqueda para descargar. Si cambia el sitio, se ajustan selectores.json y las reglas de extracción.

## Verificación y archivos entregados

Pruebas: python -m unittest discover -s tests -v. Cubren dos CAPTCHA y descarga POST con CSRF en Chromium, espera de descarga ante un body vacío, reintento ante PDF corrupto, descripción multilínea, letras junto a bordes, estructura a 400 DPI y exclusión de campos ausentes en la métrica de concordancia, concurrencia acotada, orden de páginas y filas e invalidación de imágenes en caché. El proveedor de pruebas no consume saldo. La ejecución real se registra separadamente.

Código: dian_rpa.py, extraccion.py y generar_informe.py. Documentación: README.md, requirements.txt, cufes.json, selectores.json y tests/. El repositorio incluye los diez PDF originales, sus CSV individuales y el consolidado en muestras/, y las métricas registradas en docs/metricas/. Cada ejecución genera además imágenes PNG, textos OCR, metricas.csv, detalle_proceso.json, resumen_metricas.json y reporte_metricas.md.

Repositorio GitHub: https://github.com/Egtorres14/rpa-dian

Referencias oficiales: https://2captcha.com/api-docs/cloudflare-turnstile y https://developers.cloudflare.com/turnstile/get-started/server-side-validation/.

## Modalidades y protección de la clave

Pago: python dian_rpa.py dian --captcha 2captcha --pedir-clave --out resultados_pago. La entrada oculta mantiene la clave en memoria, sin escribirla en el código ni en un archivo. También se admite TWOCAPTCHA_API_KEY para ejecuciones controladas.

Gratuito: python dian_rpa.py dian --captcha gratis --out resultados_gratis. Espera dos segundos al widget y solicita intervención si no recibe token. Nunca usa 2Captcha, aunque exista una clave en el entorno. Las pruebas en Chromium y Chrome no recibieron token automático tras 15 s; no se garantiza resolver gratuitamente los CAPTCHA reales sin una persona. La prueba asistida completa no fue cronometrada.

Demo: python dian_rpa.py extraer --demo --out resultados_demo. Usa los diez PDF incluidos en muestras/pdf y ejecuta el OCR real sin Internet ni CAPTCHA. Tardó 65.128 s y generó diez CSV con los mismos datos de la referencia. Permite evaluar el punto 2; no realiza una descarga nueva del portal.

Una clave dentro del Python descargable no se puede ocultar de forma efectiva a quien controla ese equipo. Para utilizar el saldo del propietario sin recibir su API key, el RPA debe ejecutarse en un servidor controlado o GitHub Actions con secretos, código revisado y acceso limitado. La publicación del repositorio no activa esa ejecución remota.

La API fue verificada con getBalance sin crear tareas. Los registros nuevos incluyen tiempos del cliente y proveedor, coste y consultas por tarea, sin clave, token, ID ni IP. Se conserva el intervalo oficial de cinco segundos. Callback es una alternativa de servidor pendiente de medir. Detalle: docs/evidencia_modalidades.json y docs/CLAVES_Y_MODALIDADES.md.
