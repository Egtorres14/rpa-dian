# Cambios respecto a la versión original

El proyecto tiene ahora dos versiones que se ejecutan por separado y comparten el mismo
entorno virtual (`.venv`):

| Versión | Dónde está | Cómo se lanza | Resultados |
|---|---|---|---|
| **Original** (tal cual se entregó) | `original/` | `.\rpa-original <comando>` | `resultados_original_<modo>/` |
| **Nueva** (con correcciones) | raíz del proyecto | `.\rpa <comando>` | `resultados_<modo>/` |

`original/` es una copia exacta, byte a byte, de la entrega. No se ha tocado ninguno de sus
archivos. `.\rpa cambios` lo comprueba por hash, lista lo que cambió y abre cada diferencia
en el comparador de VS Code (izquierda: original, derecha: nueva).

La carpeta también es el repositorio local de
[Egtorres14/rpa-dian](https://github.com/Egtorres14/rpa-dian) (rama `main`). Los 57 archivos
publicados en GitHub coinciden con `original/`. Por eso el panel *Source Control* de VS Code
y `git diff` muestran exactamente los cambios de este documento. Hay dos protecciones locales,
que no se suben a GitHub: `.git/info/exclude` y un *pre-commit* que bloquea cualquier commit
con `.env`, `LEEME_EVALUADOR.md`, un `.docx` o la clave de 2Captcha.

## 1. Correcciones en el código y la documentación

Son cambios pequeños y verificados en el código. Ninguno modifica la lógica de extracción ni los
datos que salen en los CSV.

| # | Archivo | Problema en la versión original | Corrección |
|---|---|---|---|
| 1 | `requirements.txt` | Sólo fijaba versiones mínimas (`>=`). Una instalación futura podía traer versiones mayores sin probar, como pandas 4. | Se fijan las versiones exactas con las que se evaluó (`docs/metricas/entorno.json`): playwright 1.63.0, pymupdf 1.28.2, pandas 3.0.6, pillow 12.3.0, reportlab 5.0.1 y numpy 2.5.3. |
| 2 | `extraccion.py` | `--ocr-timeout` no se aplicaba a la lectura de celdas, que tenía 30 s fijos. Las páginas y regiones sí lo respetaban. | Las celdas usan también el valor de `--ocr-timeout`. Sólo cambia cuánto se espera antes de declarar un fallo de Tesseract; el texto leído es el mismo. |
| 3 | `dian_rpa.py` | `verificar-captcha` terminaba con un *traceback* si 2Captcha devolvía un código de error no fatal (`CaptchaRechazado`) o un saldo ausente o no numérico. | Esos casos se capturan y se muestran como un mensaje de error normal. |
| 4 | `generar_informe.py` | Con `--sin-ocr` o con PDF sin capa de texto, la concordancia es `null` y el informe escribía «None %». Una factura sin número leído aparecía como «None». | Se muestra «n/d» y, si falta el número, el nombre del archivo. Con los datos de `docs/metricas` el informe queda exactamente igual. |
| 5 | `README.md` | Indicaba «28,447 s» para una factura. La tabla del propio README y la evidencia (`evidencia_modalidades.json`) registran 28,627 s, y 28,447 no aparece en ninguna medición. | Se unifica en 28,627 s. |
| 6 | `.gitignore` | No excluía `LEEME_EVALUADOR.md` ni el `.docx`, que contienen la clave de 2Captcha en texto plano. Un `git add .` la publicaba. | Se excluyen los dos. También se comparte la configuración de `.vscode/` y se ignora `entrega_original/`. |

`README.md` incluye además una sección breve, «Atajos en VS Code».

### Revisado y no cambiado

- **CSV de facturas OMITIDA.** Si un lote relanzado con `--resume` se detiene, el CSV
  individual de las facturas omitidas se reescribe vacío. Corregirlo sólo en ese archivo dejaría
  el consolidado incoherente. Es una decisión de diseño y se deja como estaba.
- **La clave escrita en `LEEME_EVALUADOR.md`.** Está ahí a propósito, porque ese archivo va
  sólo en el ZIP privado para los evaluadores. Ahora queda fuera de Git.

## 2. Herramientas nuevas (no alteran el programa)

| Archivo | Para qué sirve |
|---|---|
| `rpa.cmd`, `rpa-original.cmd`, `scripts/rpa.ps1` | Lanzador de comandos para las dos versiones (`.\rpa ayuda`). Funciona aunque PowerShell bloquee scripts. |
| `ver_resultados.py` | Genera `RESULTADOS.pdf` a partir de cualquier carpeta de resultados y lo abre en VS Code. Incluye resumen, estado por factura, productos extraídos con las celdas vacías resaltadas y un gráfico de tiempos. |
| `scripts/github_actions.py` | Consulta GitHub Actions del repositorio desde la terminal (`.\rpa actions`): ejecuciones, trabajos, pasos y artefactos. No necesita sesión porque usa la API pública. Con `gh` también descarga el artefacto «resultados» a `artefactos_github/<id>/`, genera su `RESULTADOS.pdf` y guarda el registro completo. |
| `scripts/comparar_resultados.py` | Compara dos carpetas de resultados: estado por factura, cada campo de cada producto y tiempos. |
| `.vscode/` | Tareas (Run Task / `Ctrl+Shift+B`), depuración con F5 de las dos versiones, intérprete `.venv`, visores y extensiones recomendadas. |
| Visores en VS Code | PDF con `tomoki1207.pdf`. Word (`.docx`), Excel (`.xlsx`, `.xls`, `.xlsm`, `.ods`) y CSV en cuadrícula con Office Viewer (`cweijan.vscode-office`). Para ver un CSV como texto con colores (Rainbow CSV): *Reopen Editor With… → Text Editor*. Comandos: `.\rpa csv`, `.\rpa respuesta` y `.\rpa abrir <archivo>`. |
| `CAMBIOS.md` | Este documento. |

## 3. Verificación

Medido el 2026-10-08 en Windows 11, Python 3.12.10 y Tesseract 5.4.0:

| Comprobación | Original | Nueva |
|---|---|---|
| Pruebas unitarias | 28/28 OK | 28/28 OK |
| Demo OCR (10 PDF de `muestras/pdf`) | 9 OK · 1 REVISAR · 65,2 s | 9 OK · 1 REVISAR · 63,3 s |
| Datos extraídos (21 productos, todas las columnas) | referencia | idénticos campo a campo |
| Informe oficial con `docs/metricas` (`.md` y texto del PDF) | referencia | idéntico |
| Informe con concordancia `null` (caso sintético) | «None» 12 veces | «n/d», 0 «None» |
| `verificar-captcha` con respuestas anómalas simuladas (3 casos) | 3 *tracebacks* | 3 mensajes de error claros |
| `verificar-captcha` con la clave real (no crea tareas) | — | clave válida, saldo positivo |

La diferencia de tiempos entre ejecuciones es variación normal del equipo, no una optimización.

`REVISAR` corresponde a `HTFE-6599`, cuyo código de producto también falta en el PDF original.
En ambas versiones el programa termina con código 1 para señalarlo.

Para repetir la comparación: `.\rpa ambas` ejecuta las dos demos y compara lo extraído.
`.\rpa comparar` compara resultados ya generados.
