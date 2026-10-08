# Comandos rápidos del RPA DIAN. Uso desde la terminal de VS Code:
#   .\rpa <comando> [opciones extra]            -> versión NUEVA (raíz del proyecto)
#   .\rpa-original <comando> [opciones extra]   -> versión ORIGINAL congelada en original\
# Ejecuta ".\rpa ayuda" para ver la lista completa.

$ErrorActionPreference = "Continue"
$Raiz = Split-Path -Parent $PSScriptRoot
$Py = Join-Path $Raiz ".venv\Scripts\python.exe"
$Inicio = (Get-Location).Path
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"

$lista = @($args)
$Original = $lista.Count -gt 0 -and "$($lista[0])".ToLower() -eq "original"
if ($Original) { $lista = @($lista | Select-Object -Skip 1) }
$Comando = if ($lista.Count -gt 0) { "$($lista[0])".ToLower() } else { "ayuda" }
$Extra = @($lista | Select-Object -Skip 1)

# La versión original se ejecuta desde su carpeta (sus rutas son relativas a sí misma)
# y deja los resultados en la raíz con el prefijo resultados_original_.
$Dir = if ($Original) { Join-Path $Raiz "original" } else { $Raiz }
$Prefijo = if ($Original) { "resultados_original_" } else { "resultados_" }
$Version = if ($Original) { "ORIGINAL (copia congelada, sin cambios)" } else { "NUEVA (con correcciones)" }
Set-Location $Dir

function Titulo($texto) { Write-Host ""; Write-Host "== $texto ==" -ForegroundColor Cyan }
function Ok($texto) { Write-Host "  [OK]    $texto" -ForegroundColor Green }
function Aviso($texto) { Write-Host "  [AVISO] $texto" -ForegroundColor Yellow }
function Fallo($texto) { Write-Host "  [FALLO] $texto" -ForegroundColor Red }

function Exigir-Venv {
    if (-not (Test-Path $Py)) {
        Fallo "No existe el entorno virtual. Ejecuta primero: .\rpa instalar"
        exit 1
    }
}

function Ruta-Absoluta($ruta) { [IO.Path]::GetFullPath([IO.Path]::Combine($Inicio, $ruta)) }

function Buscar-PythonBase {
    # Prefiere 3.12 (versión probada); si no está, cualquier Python >= 3.11 (mínimo de pandas 3 y numpy 2.5).
    $candidatos = @()
    if (Get-Command py -ErrorAction SilentlyContinue) {
        foreach ($v in "3.12", "3.13", "3.11") {
            $exe = & py "-$v" -c "import sys; print(sys.executable)" 2>$null
            if ($LASTEXITCODE -eq 0 -and $exe) { $candidatos += $exe.Trim() }
        }
    }
    if (Get-Command python -ErrorAction SilentlyContinue) { $candidatos += (Get-Command python).Source }
    foreach ($exe in $candidatos) {
        $ok = & $exe -c "import sys; print(int(sys.version_info >= (3, 11)))" 2>$null
        if ($ok -eq "1") { return $exe }
    }
    return $null
}

function Buscar-Tesseract {
    $cmd = Get-Command tesseract -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    $defecto = "C:\Program Files\Tesseract-OCR\tesseract.exe"
    if (Test-Path $defecto) { return $defecto }
    return $null
}

function Valor-Out($predeterminado) {
    # Si el usuario pasa --out, se respeta para luego abrir esa carpeta.
    for ($i = 0; $i -lt $Extra.Count - 1; $i++) {
        if ($Extra[$i] -eq "--out") { return Ruta-Absoluta $Extra[$i + 1] }
    }
    return $predeterminado
}

function Ejecutar-Rpa([string[]] $argumentos, [string] $nombre) {
    Exigir-Venv
    $carpeta = Valor-Out (Join-Path $Raiz ($Prefijo + $nombre))
    Write-Host "Versión: $Version" -ForegroundColor Magenta
    & $Py dian_rpa.py @argumentos --out $carpeta @Extra
    $codigo = $LASTEXITCODE
    if ($codigo -ne 0 -and (Test-Path (Join-Path $carpeta "resumen_metricas.json"))) {
        Aviso "Código de salida ${codigo}: alguna factura quedó en REVISAR, ERROR u OMITIDA."
    }
    if (-not $Original -and (Test-Path (Join-Path $carpeta "resumen_metricas.json"))) {
        & $Py (Join-Path $Raiz "ver_resultados.py") $carpeta --abrir
    }
    exit $codigo
}

function Archivos-Proyecto([string] $base) {
    # Archivos del proyecto, sin entorno virtual, resultados ni cachés.
    $fuera = ".venv", "original", "entrega", "entrega_original", "__pycache__", ".git"
    Get-ChildItem $base -Force | Where-Object { $_.Name -notin $fuera -and $_.Name -notlike "resultados_*" } | ForEach-Object {
        if ($_.PSIsContainer) { Get-ChildItem $_.FullName -Recurse -File -Force | Where-Object { $_.FullName -notmatch '\\__pycache__\\' } }
        else { $_ }
    } | ForEach-Object { $_.FullName.Substring($base.Length + 1) }
}

$SoloNueva = "ver", "abrir", "csv", "respuesta", "actions", "cambios", "comparar", "ambas"
if ($Original -and $Comando -in $SoloNueva) {
    Fallo "'$Comando' es una herramienta de la versión nueva. Usa: .\rpa $Comando"
    exit 2
}

switch ($Comando) {
    "instalar" {
        Titulo "Entorno virtual (compartido por ambas versiones)"
        if (-not (Test-Path $Py)) {
            $base = Buscar-PythonBase
            if (-not $base) { Fallo "No se encontró Python >= 3.11. Instala Python 3.12 desde python.org."; exit 1 }
            Write-Host "  Creando .venv con $base"
            & $base -m venv (Join-Path $Raiz ".venv")
            if ($LASTEXITCODE -ne 0) { Fallo "No se pudo crear .venv"; exit 1 }
        }
        Ok (& $Py --version)

        Titulo "Dependencias de Python (requirements.txt)"
        & $Py -m pip install --disable-pip-version-check -q --upgrade pip
        & $Py -m pip install --disable-pip-version-check -r (Join-Path $Raiz "requirements.txt")
        if ($LASTEXITCODE -ne 0) { Fallo "pip install falló"; exit 1 }

        Titulo "Navegador Chromium para Playwright"
        & $Py -m playwright install chromium
        if ($LASTEXITCODE -ne 0) { Fallo "No se pudo instalar Chromium"; exit 1 }

        Titulo "Tesseract OCR"
        if (-not (Buscar-Tesseract)) {
            Aviso "Tesseract no está instalado. Instalando con winget..."
            winget install -e --id UB-Mannheim.TesseractOCR --accept-source-agreements --accept-package-agreements
        }

        Titulo "Extensiones de VS Code (visores PDF, Word, Excel y CSV)"
        if (Get-Command code -ErrorAction SilentlyContinue) {
            $instaladas = code --list-extensions 2>$null
            foreach ($id in "tomoki1207.pdf", "cweijan.vscode-office", "mechatroner.rainbow-csv") {
                if ($instaladas -contains $id) { Ok "$id ya instalada" }
                else { code --install-extension $id 2>$null | Out-Null; Ok "$id instalada" }
            }
        } else { Aviso "El comando 'code' no está en PATH; instala las extensiones recomendadas desde VS Code" }
        & $PSCommandPath verificar
        exit $LASTEXITCODE
    }

    { $_ -in "verificar", "entorno" } {
        $errores = 0
        Titulo "Python"
        if (Test-Path $Py) { Ok "$(& $Py --version) en .venv" } else { Fallo "Falta .venv (ejecuta .\rpa instalar)"; $errores++ }

        if (Test-Path $Py) {
            Titulo "Paquetes"
            $chequeo = @'
import importlib.metadata as md, os, sys
faltan = 0
for mod, dist in [("playwright", "playwright"), ("pymupdf", "pymupdf"), ("pandas", "pandas"),
                  ("PIL", "pillow"), ("reportlab", "reportlab"), ("numpy", "numpy")]:
    try:
        __import__(mod)
        print(f"OK|{dist} {md.version(dist)}")
    except Exception as exc:
        faltan += 1
        print(f"FALLO|{dist}: {exc}")
try:
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        ruta = pw.chromium.executable_path
    if os.path.exists(ruta):
        print("OK|Chromium " + ruta)
    else:
        faltan += 1
        print("FALLO|Chromium no descargado (python -m playwright install chromium)")
except Exception as exc:
    faltan += 1
    print(f"FALLO|Chromium: {exc}")
sys.exit(1 if faltan else 0)
'@
            $lineas = $chequeo | & $Py -
            foreach ($l in $lineas) {
                $estado, $texto = $l -split "\|", 2
                if ($estado -eq "OK") { Ok $texto } else { Fallo $texto; $errores++ }
            }
        }

        Titulo "Tesseract OCR"
        $tess = Buscar-Tesseract
        if ($tess) {
            Ok ((& $tess --version 2>&1 | Select-Object -First 1) -as [string])
            $idiomas = & $tess --list-langs 2>&1 | Select-Object -Skip 1 | ForEach-Object { "$_".Trim() }
            foreach ($idioma in "spa", "eng") {
                if ($idiomas -contains $idioma) { Ok "Idioma $idioma" } else { Fallo "Falta el idioma $idioma (copia $idioma.traineddata en tessdata)"; $errores++ }
            }
        } else { Fallo "Tesseract no encontrado (winget install -e --id UB-Mannheim.TesseractOCR)"; $errores++ }

        Titulo "Clave 2Captcha (sólo para descargas de la DIAN)"
        $envFile = Join-Path $Raiz ".env"
        if ($env:TWOCAPTCHA_API_KEY) { Ok "Variable de entorno TWOCAPTCHA_API_KEY definida" }
        elseif ((Test-Path $envFile) -and (Select-String -Path $envFile -Pattern '^\s*TWOCAPTCHA_API_KEY\s*=\s*\S' -Quiet) -and -not (Select-String -Path $envFile -Pattern 'tu_clave' -Quiet)) { Ok "Clave encontrada en .env (no se muestra)" }
        else { Aviso "Sin clave: la demo funciona igual; para la DIAN copia .env.example a .env" }

        Titulo "Versiones del código"
        if (Test-Path (Join-Path $Raiz "original\dian_rpa.py")) { Ok "Original congelada en original\ (.\rpa-original)" } else { Aviso "No existe original\" }
        Ok "Nueva en la raíz del proyecto (.\rpa)"

        Titulo "Visores en VS Code"
        if (Get-Command code -ErrorAction SilentlyContinue) {
            $instaladas = code --list-extensions 2>$null
            $visores = [ordered]@{ "tomoki1207.pdf" = "PDF"; "cweijan.vscode-office" = "Word, Excel y CSV"; "mechatroner.rainbow-csv" = "CSV como texto con colores" }
            foreach ($id in $visores.Keys) {
                if ($instaladas -contains $id) { Ok "$($visores[$id]): $id" }
                else { Aviso "Falta el visor de $($visores[$id]): code --install-extension $id" }
            }
        } else { Aviso "El comando 'code' no está en PATH; los archivos se abrirán con los programas de Windows" }

        Write-Host ""
        if ($errores -eq 0) { Write-Host "Todo listo. Prueba: .\rpa demo" -ForegroundColor Green; exit 0 }
        Write-Host "$errores problema(s). Ejecuta .\rpa instalar" -ForegroundColor Red; exit 1
    }

    "test" {
        Exigir-Venv
        Write-Host "Versión: $Version" -ForegroundColor Magenta
        & $Py -m unittest discover -s tests -v @Extra
        exit $LASTEXITCODE
    }

    "demo" { Ejecutar-Rpa @("extraer", "--demo") "demo" }

    "extraer" {
        if ($Extra.Count -eq 0 -or "$($Extra[0])".StartsWith("-")) { Fallo "Uso: .\rpa extraer <carpeta_con_pdf> [opciones]"; exit 2 }
        $carpeta = Ruta-Absoluta $Extra[0]
        $Extra = @($Extra | Select-Object -Skip 1)
        Ejecutar-Rpa @("extraer", "--pdf-dir", $carpeta) "extraer"
    }

    "saldo" {
        Exigir-Venv
        & $Py dian_rpa.py verificar-captcha @Extra
        exit $LASTEXITCODE
    }

    "una" {
        Aviso "Descarga real de 1 factura con 2Captcha (coste aprox. USD 0,003)."
        Ejecutar-Rpa @("dian", "--captcha", "2captcha", "--limite", "1", "--attempts", "1", "--max-captcha-tasks", "2") "pago"
    }

    "lote" {
        Aviso "Descarga real de las 10 facturas con 2Captcha (coste aprox. USD 0,03, unos 5 minutos)."
        Ejecutar-Rpa @("dian", "--captcha", "2captcha") "lote"
    }

    "gratis" {
        Aviso "Abre el navegador; si el CAPTCHA lo pide, marca la casilla a mano."
        Ejecutar-Rpa @("dian", "--captcha", "gratis", "--limite", "1") "gratis"
    }

    "informe" {
        Exigir-Venv
        $destino = Join-Path $Raiz $(if ($Original) { "entrega_original" } else { "entrega" })
        & $Py generar_informe.py --resultados docs/metricas --destino $destino `
            --repositorio https://github.com/Egtorres14/rpa-dian `
            --optimizacion docs/metricas/optimizacion_tiempos.json `
            --modalidades docs/evidencia_modalidades.json
        if ($LASTEXITCODE -ne 0 -or $Original) { exit $LASTEXITCODE }
        & $Py (Join-Path $Raiz "ver_resultados.py") --solo-abrir (Join-Path $destino "INFORME_PRUEBA_TECNICA.pdf")
        exit $LASTEXITCODE
    }

    "ver" {
        Exigir-Venv
        $carpetas = @($Extra | Where-Object { -not "$_".StartsWith("-") } | ForEach-Object { Ruta-Absoluta $_ })
        & $Py ver_resultados.py @carpetas --abrir
        exit $LASTEXITCODE
    }

    "abrir" {
        Exigir-Venv
        if ($Extra.Count -eq 0) { Fallo "Uso: .\rpa abrir <archivo .pdf, .csv, .xlsx o .docx>"; exit 2 }
        & $Py ver_resultados.py --solo-abrir (Ruta-Absoluta $Extra[0])
        exit $LASTEXITCODE
    }

    "csv" {
        # CSV consolidado de una carpeta (por defecto, la ejecución más reciente) en vista de cuadrícula.
        Exigir-Venv
        if ($Extra.Count -ge 1) { $archivo = Join-Path (Ruta-Absoluta $Extra[0]) "facturas_consolidadas.csv" }
        else {
            $archivo = Get-ChildItem $Raiz -Directory -Filter "resultados_*" |
                ForEach-Object { Get-Item (Join-Path $_.FullName "facturas_consolidadas.csv") -ErrorAction SilentlyContinue } |
                Sort-Object LastWriteTime | Select-Object -Last 1 | ForEach-Object { $_.FullName }
        }
        if (-not $archivo -or -not (Test-Path $archivo)) { Fallo "No hay facturas_consolidadas.csv. Ejecuta antes: .\rpa demo"; exit 1 }
        & $Py ver_resultados.py --solo-abrir $archivo
        exit $LASTEXITCODE
    }

    "actions" {
        # GitHub Actions del repositorio: lista, detalle, artefactos (con PDF), registro o navegador.
        Exigir-Venv
        & $Py (Join-Path $PSScriptRoot "github_actions.py") @Extra
        exit $LASTEXITCODE
    }

    "respuesta" {
        Exigir-Venv
        $docx = Get-ChildItem $Raiz -Filter "RESPUESTA*.docx" | Select-Object -First 1
        if (-not $docx) { Fallo "No se encontró el documento de respuesta (.docx)"; exit 1 }
        & $Py ver_resultados.py --solo-abrir $docx.FullName
        exit $LASTEXITCODE
    }

    "comparar" {
        Exigir-Venv
        $a = if ($Extra.Count -ge 1) { Ruta-Absoluta $Extra[0] } else { Join-Path $Raiz "resultados_original_demo" }
        $b = if ($Extra.Count -ge 2) { Ruta-Absoluta $Extra[1] } else { Join-Path $Raiz "resultados_demo" }
        & $Py (Join-Path $PSScriptRoot "comparar_resultados.py") $a $b
        exit $LASTEXITCODE
    }

    "ambas" {
        # Misma demo con las dos versiones y comparación de lo extraído.
        Titulo "1/3 Demo con la versión ORIGINAL"
        & $PSCommandPath original demo @Extra
        Titulo "2/3 Demo con la versión NUEVA"
        & $PSCommandPath demo @Extra
        Titulo "3/3 Comparación"
        & $PSCommandPath comparar
        exit $LASTEXITCODE
    }

    "cambios" {
        $orig = Join-Path $Raiz "original"
        if (-not (Test-Path $orig)) { Fallo "No existe original\"; exit 1 }
        $antes = @(Archivos-Proyecto $orig)
        $ahora = @(Archivos-Proyecto $Raiz)
        $modificados = @($antes | Where-Object { ($_ -in $ahora) -and ((Get-FileHash (Join-Path $orig $_)).Hash -ne (Get-FileHash (Join-Path $Raiz $_)).Hash) })
        $nuevos = @($ahora | Where-Object { $_ -notin $antes })
        $borrados = @($antes | Where-Object { $_ -notin $ahora })

        Titulo "Cambios de la versión nueva respecto a original\"
        foreach ($f in $modificados) { Write-Host "  MODIFICADO  $f" -ForegroundColor Yellow }
        foreach ($f in $nuevos) { Write-Host "  NUEVO       $f" -ForegroundColor Green }
        foreach ($f in $borrados) { Write-Host "  ELIMINADO   $f" -ForegroundColor Red }
        Write-Host ""
        Write-Host "  $($modificados.Count) modificados, $($nuevos.Count) nuevos, $($borrados.Count) eliminados. Detalle: CAMBIOS.md"
        if ($Extra -contains "--sin-abrir") { exit 0 }
        if (Get-Command code -ErrorAction SilentlyContinue) {
            Write-Host "  Abriendo las diferencias en VS Code (izquierda: original, derecha: nueva)..."
            code -r (Join-Path $Raiz "CAMBIOS.md")
            foreach ($f in $modificados) { code -r --diff (Join-Path $orig $f) (Join-Path $Raiz $f) }
        }
        exit 0
    }

    default {
        $esAyuda = $Comando -in "ayuda", "help", "-h", "--help", "/?"
        if (-not $esAyuda) { Fallo "Comando desconocido: $Comando" }
        Write-Host @"

RPA DIAN - dos versiones lado a lado

  .\rpa <comando>            versión NUEVA (raíz): correcciones + PDF de resultados en VS Code
  .\rpa-original <comando>   versión ORIGINAL tal cual se entregó (carpeta original\), sin cambios

  Preparación (entorno compartido)
    instalar        Crea .venv, instala requirements, Chromium y comprueba Tesseract
    verificar       Diagnóstico del entorno (Python, paquetes, Chromium, Tesseract, clave, visor PDF)
    test            Ejecuta las pruebas unitarias de esa versión (sin Internet ni saldo)

  Ejecución (ambas versiones)
    demo            OCR de las 10 facturas de muestras\pdf, gratis y sin Internet
    extraer <dir>   OCR de los PDF de una carpeta
    saldo           Comprueba la clave de 2Captcha sin gastar saldo
    una             Descarga y procesa 1 factura de la DIAN (2Captcha, ~USD 0,003)
    lote            Descarga y procesa las 10 facturas (2Captcha, ~USD 0,03)
    gratis          Descarga 1 factura con CAPTCHA asistido (navegador visible)
    informe         Regenera el informe oficial de la prueba (entrega\ o entrega_original\)

  Sólo versión nueva
    ver [carpeta]   Genera RESULTADOS.pdf de una carpeta (por defecto, la última) y lo abre en VS Code
    csv [carpeta]   Abre facturas_consolidadas.csv (por defecto, de la última ejecución) en cuadrícula
    respuesta       Abre el documento de respuesta (.docx) en VS Code
    abrir <archivo> Abre un PDF, CSV, Excel o Word en su visor dentro de VS Code

  GitHub Actions (repositorio Egtorres14/rpa-dian)
    actions                 Lista los workflows y las últimas ejecuciones
    actions <id>            Trabajos, pasos y artefactos de una ejecución
    actions <id> --descargar  Descarga sus resultados y abre el PDF (requiere gh con sesión)
    actions <id> --log      Guarda el registro completo y lo abre en VS Code (requiere gh)
    actions [id] --web      Abre Actions o la ejecución en el navegador
    cambios         Lista los archivos cambiados y abre las diferencias en VS Code
    comparar [a b]  Compara los resultados de dos carpetas (por defecto: original vs nueva demo)
    ambas           Ejecuta la demo con las dos versiones y compara lo extraído

  Resultados: la versión nueva escribe en resultados_<modo>\ y la original en resultados_original_<modo>\
  Las opciones extra se pasan a dian_rpa.py, por ejemplo:  .\rpa demo --ocr-workers 2
"@
        if ($esAyuda) { exit 0 } else { exit 2 }
    }
}
