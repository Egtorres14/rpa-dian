# Demo de OCR sin coste

Los diez PDF proceden de la ejecución DIAN registrada en `docs/metricas/`. Según el
enunciado, sus datos son ficticios y la prueba tiene fines exclusivamente
evaluativos. `manifest.json` registra nombre, CUFE y SHA-256 de cada original.
Los PDF cifrados se abren con el NIT de `cufes.json`.

`csv/` contiene un CSV de referencia por factura y `facturas_consolidadas.csv`
reúne los 21 productos. Son los resultados de esa ejecución, para comparar
la salida de la demo. Las nuevas imágenes, textos y métricas se generan en la
carpeta indicada con `--out`.

```powershell
python dian_rpa.py extraer --demo --out resultados_demo
```

Después de instalar las dependencias y Tesseract, este comando funciona sin
Internet, API key, navegador ni CAPTCHA. Renderiza las imágenes y ejecuta el
OCR real para generar diez CSV y sus métricas. Permite evaluar el punto 2;
la consulta y descarga del punto 1 se comprueban con el comando `dian`.

El código de la factura `HTFE-6599` está vacío en la fuente. El resultado debe
conservarlo vacío y marcar REVISAR: la salida 1 del programa refleja esa
observación, aunque los diez PDF hayan sido procesados.
