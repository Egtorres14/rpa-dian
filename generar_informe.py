"""Genera el documento de entrega en Markdown y PDF desde una ejecución registrada."""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle


DISCLAIMER = (
    "Esta es una prueba técnica con fines exclusivamente evaluativos. Según el enunciado, "
    "los datos son completamente ficticios y generados únicamente para evaluación. Los resultados "
    "y análisis no serán utilizados por la empresa para fines comerciales reales. La evaluación "
    "no conlleva compensación económica y su único objetivo es medir las competencias técnicas "
    "del aspirante. Duración indicada: 24 horas."
)


def fecha_bogota(value):
    return datetime.fromisoformat(value).astimezone(timezone(timedelta(hours=-5))).strftime("%Y-%m-%d %H:%M:%S")


def crear_contenido(summary, records, repositorio, nota, adquisicion=None, optimizacion=None, modalidades=None):
    presentes = sum(r.get("campos_presentes", 0) for r in records)
    esperados = sum(r.get("campos_esperados", 0) for r in records)
    comparados = sum(r.get("campos_comparados_ocr_pdf", 0) for r in records)
    sections = [
        ("Alcance de la evaluación", [DISCLAIMER]),
        ("Punto 1: consulta y descarga", [
            "Se consultan los diez CUFE con Playwright. El robot llena CUFE y NIT, resuelve el "
            "Turnstile de búsqueda y obtiene el documento. Resuelve también el segundo CAPTCHA "
            "con un token nuevo y la sitekey de la página del documento, mediante la API v2 de 2Captcha.",
            "Pulsa Descargar PDF y acepta el aviso de contraseña. El JavaScript del portal envía "
            "su formulario POST con CAPTCHA, cookies y CSRF. El PDF se guarda y se valida por "
            "cabecera, páginas y contraseña; se rechazan respuestas HTML o corruptas. Se permiten "
            "hasta dos intentos por factura y se detiene el lote ante errores globales del proveedor o portal.",
        ]),
        ("Punto 2: imágenes, OCR y DataFrames", [
            "Cada página se exporta a PNG y se procesa con Tesseract. Con la configuración "
            "predeterminada se localiza la estructura a 300 DPI y se leen las celdas a 400 DPI. Las secciones identifican "
            "número de factura, fecha de emisión y NIT del emisor. Las líneas visibles de la tabla "
            "delimitan código, descripción, cantidad y precio unitario; cada celda se lee por separado. "
            "Se limpian líneas verdes en los recortes para conservar letras junto a los bordes.",
            f"El OCR ejecuta hasta {summary.get('ocr_workers', 1)} lecturas simultáneas de páginas o celdas. "
            "Reutiliza hasta dos imágenes decodificadas por factura y conserva el orden de resultados. "
            "PyMuPDF y las consultas DIAN se ejecutan en el hilo principal. Cada proceso Tesseract "
            "usa un hilo interno. Los tiempos OCR miden pared, sin sumar trabajos simultáneos.",
            "Se crea un DataFrame por PDF, con una fila por producto y los datos de cabecera repetidos. "
            "Se exporta un CSV por archivo y un consolidado UTF-8 con BOM. Fecha: AAAA-MM-DD; "
            "cantidades y precios: Decimal con punto. Código y NIT son texto. Archivo, CUFE, página "
            "y línea de producto permiten rastrear cada registro.",
            "La capa de texto del PDF se utiliza para contrastar el OCR; los valores exportados "
            "proceden de las imágenes. No se reemplazan errores de OCR con datos nativos ni se "
            "inventan campos ausentes. El NIT de consulta puede ser del receptor; el NIT del emisor "
            "se extrae de su sección en el PDF.",
        ]),
        ("Resultados medidos", [
            f"Ejecución: {fecha_bogota(summary['inicio_utc'])} a {fecha_bogota(summary['fin_utc'])} "
            f"(America/Bogota). Modo: {summary['modo']}. Facturas: {summary['facturas_solicitadas']}; "
            f"OK: {summary['ok']}; REVISAR: {summary['revisar']}; ERROR: {summary['errores']}; "
            f"OMITIDAS: {summary['omitidas']}.",
            f"Páginas procesadas: {summary['paginas']}; con OCR: {summary['paginas_ocr']}; "
            f"filas de producto: {summary['filas_productos']}. Campos presentes: {presentes}/{esperados}. "
            f"Cobertura global: {100 * presentes / esperados if esperados else 0:.2f} %. "
            f"Concordancia media por factura: {summary['concordancia_ocr_pdf_media_pct']} %; "
            f"campos comparados: {comparados}. Los campos vacíos en ambas fuentes no se cuentan como aciertos.",
        ]),
        ("Restricciones y calidad", [
            "Cobertura significa presencia de campos, no exactitud. Concordancia OCR/PDF mide "
            "coincidencia con otra representación del mismo documento; no equivale a precisión "
            "contra una anotación humana independiente. La confianza de Tesseract tampoco es "
            "una probabilidad calibrada de exactitud.",
            "Tiempo total: pared, incluye esperas y pausas; excluye instalación. CAPTCHA ya está "
            "incluido en consulta y descarga: no se suma otra vez. Memoria y CPU Python excluyen "
            "Chromium y Tesseract y no representan toda la RAM del equipo. Las lecturas simultáneas "
            "aumentan el uso de memoria de procesos externos. El coste incluye tareas cuyo coste devolvió la API; una tarea "
            "que agote el tiempo puede tener un cargo todavía pendiente.",
            "Dependencias externas: disponibilidad de DIAN, formato HTML/PDF, NIT válido, conexión "
            "y saldo del proveedor. Turnstile utiliza tokens de un solo uso y 300 segundos de "
            "vigencia. Se evita reutilizar el token de búsqueda para descargar. Si cambia el sitio, "
            "se ajustan selectores.json y las reglas de extracción.",
        ]),
        ("Verificación y archivos entregados", [
            "Pruebas: python -m unittest discover -s tests -v. Cubren dos CAPTCHA y descarga POST "
            "con CSRF en Chromium, espera de descarga ante un body vacío, reintento ante PDF "
            "corrupto, descripción multilínea, letras junto a bordes, estructura a 400 DPI y "
            "exclusión de campos ausentes en la métrica de concordancia, concurrencia acotada, "
            "orden de páginas y filas e invalidación de imágenes en caché. El "
            "proveedor de pruebas no consume saldo. La ejecución real se registra separadamente.",
            "Código: dian_rpa.py, extraccion.py y generar_informe.py. Documentación: README.md, "
            "requirements.txt, cufes.json, selectores.json y tests/. El repositorio incluye los diez "
            "PDF originales, sus CSV individuales y el consolidado en muestras/, y las métricas "
            "registradas en docs/metricas/. Cada ejecución genera además imágenes PNG, textos OCR, "
            "metricas.csv, detalle_proceso.json, resumen_metricas.json y reporte_metricas.md.",
            "Repositorio GitHub: " + (repositorio or "no indicado; usa --repositorio para incluir su enlace."),
            "Referencias oficiales: https://2captcha.com/api-docs/cloudflare-turnstile y "
            "https://developers.cloudflare.com/turnstile/get-started/server-side-validation/.",
        ]),
    ]
    if nota:
        sections[4][1].insert(0, nota)
    if adquisicion:
        sections[3][1].insert(0,
            f"Punto 1 verificado en una ejecución integral DIAN de {adquisicion['tiempo_total_s']:.2f} s, "
            f"entre {fecha_bogota(adquisicion['inicio_utc'])} y {fecha_bogota(adquisicion['fin_utc'])} "
            f"(America/Bogota), con {adquisicion['captcha_tareas_remotas']} tareas CAPTCHA. "
            "Los PDF se reutilizaron para la reextracción final descrita a continuación. "
            f"Tiempo automático acumulado de estas dos ejecuciones: "
            f"{adquisicion['tiempo_total_s'] + summary['tiempo_total_s']:.2f} s; excluye depuración y revisión humana.")
    if optimizacion:
        local = optimizacion['benchmark_local']
        sections[3][1].append(
            f"Optimización local, mismos diez PDF y mismo equipo: {local['antes_s']:.3f} s antes y "
            f"{local['despues_s']:.3f} s después; reducción del {local['reduccion_pct']:.2f} %. "
            f"Los {local['csv_identicos']} CSV tienen exactamente los mismos valores. Se conserva "
            "el OCR de las imágenes, la resolución de 400 DPI y la relectura de códigos.")
        if integral := optimizacion.get('flujo_dian'):
            sections[3][1].append(
                f"Flujo integral DIAN: {integral['antes_s']:.3f} s antes y "
                f"{integral['despues_s']:.3f} s después; reducción observada "
                f"del {integral['reduccion_pct']:.2f} %. La pausa cambia de 5 a 1 s: "
                "36 s menos de espera programada para diez facturas. Son dos ejecuciones "
                "individuales; la latencia variable de 2Captcha impide atribuir toda la "
                "diferencia al código. Evidencia: docs/metricas/optimizacion_tiempos.json.")
    if modalidades:
        demo = next(r for r in modalidades['mediciones'] if r['nombre'] == 'Demo OCR: diez PDF')
        sections.append(("Modalidades y protección de la clave", [
            "Pago: python dian_rpa.py dian --captcha 2captcha --pedir-clave --out resultados_pago. "
            "La entrada oculta mantiene la clave en memoria, sin escribirla en el código ni en un archivo. "
            "También se admite TWOCAPTCHA_API_KEY para ejecuciones controladas.",
            "Gratuito: python dian_rpa.py dian --captcha gratis --out resultados_gratis. "
            "Espera dos segundos al widget y solicita intervención si no recibe token. Nunca usa "
            "2Captcha, aunque exista una clave en el entorno. Las pruebas en Chromium y Chrome "
            "no recibieron token automático tras 15 s; no se garantiza resolver gratuitamente "
            "los CAPTCHA reales sin una persona. La prueba asistida completa no fue cronometrada.",
            "Demo: python dian_rpa.py extraer --demo --out resultados_demo. Usa los diez PDF incluidos "
            "en muestras/pdf y ejecuta el OCR real sin Internet ni CAPTCHA. "
            f"Tardó {demo['tiempo_s']:.3f} s y generó diez CSV con los mismos datos de la referencia. "
            "Permite evaluar el punto 2; no realiza una descarga nueva del portal.",
            "Una clave dentro del Python descargable no se puede ocultar de forma efectiva a quien "
            "controla ese equipo. Para utilizar el saldo del propietario sin recibir su API key, el "
            "RPA debe ejecutarse en un servidor controlado o GitHub Actions con secretos, código "
            "revisado y acceso limitado. La publicación del repositorio no activa esa ejecución remota.",
            "La API fue verificada con getBalance sin crear tareas. Los registros nuevos incluyen "
            "tiempos del cliente y proveedor, coste y consultas por tarea, sin clave, token, ID ni IP. "
            "Se conserva el intervalo oficial de cinco segundos. Callback es una alternativa de "
            "servidor pendiente de medir. Detalle: docs/evidencia_modalidades.json y "
            "docs/CLAVES_Y_MODALIDADES.md.",
        ]))
    return sections


def crear_pdf(path, sections, summary, records, adquisicion=None, modalidades=None):
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name="TextoInforme", fontName="Helvetica", fontSize=10,
                              leading=14, spaceAfter=9, textColor=colors.HexColor("#243349")))
    styles.add(ParagraphStyle(name="Celda", fontSize=8, leading=10))
    styles["Title"].textColor = colors.HexColor("#124e64")
    styles["Heading2"].textColor = colors.HexColor("#124e64")
    story = [Paragraph("RPA DIAN y extracción OCR", styles["Title"]),
             Paragraph("Documento de respuestas · Prueba técnica", styles["TextoInforme"])]

    def tabla(rows, widths):
        wrapped = [[Paragraph(escape(str(c)), styles["Celda"]) for c in row] for row in rows]
        t = Table(wrapped, colWidths=widths, repeatRows=1, hAlign="LEFT")
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e4f1f4")),
            ("GRID", (0, 0), (-1, -1), .4, colors.HexColor("#c8d7df")),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ]))
        return t

    for index, (title, paragraphs) in enumerate(sections):
        if index in (3, 4, 6):
            story.append(PageBreak())
        story.append(Paragraph(escape(title), styles["Heading2"]))
        for p in paragraphs:
            story.append(Paragraph(escape(p), styles["TextoInforme"]))
        if index == 3:
            consulta = adquisicion or summary
            tiempos = [
                ["Métrica", "Valor medido"],
                ["Tiempo reextracción final / media / p95" if adquisicion else "Tiempo total / media por factura / p95", f"{summary['tiempo_total_s']:.2f} / {summary['tiempo_factura_medio_s']:.2f} / {summary['tiempo_factura_p95_s']:.2f} s"],
                ["Consulta / descarga / CAPTCHA (integral previa)" if adquisicion else "Consulta / descarga / CAPTCHA (incluido)", f"{consulta['tiempo_consulta_total_s']:.2f} / {consulta['tiempo_descarga_total_s']:.2f} / {consulta['tiempo_captcha_total_s']:.2f} s"],
                ["OCR / parseo", f"{summary['tiempo_ocr_total_s']:.2f} / {summary['tiempo_parseo_total_s']:.2f} s"],
                ["Tareas CAPTCHA / coste (integral registrada)", f"{consulta['captcha_tareas_remotas']} / USD {consulta['captcha_coste_reportado_usd']}"],
                ["Memoria pico Python", f"{summary['memoria_pico_python_mib']:.2f} MiB"],
            ]
            story.append(tabla(tiempos, [290, 220]))
            story.append(Spacer(1, 14))
            rows = [["#", "Factura", "Estado", "Productos", "Cobertura", "Concordancia", "Extracción (s)" if adquisicion else "Total (s)"]]
            for i, r in enumerate(records, 1):
                rows.append([i, r.get("numero_factura", r["archivo"]), r["estado"], r.get("productos", 0),
                             f"{r.get('cobertura_campos_pct', 0)} %", f"{r.get('concordancia_ocr_pdf_pct', 'n/d')} %",
                             f"{r['total_s']:.2f}"])
            story.append(tabla(rows, [22, 92, 61, 58, 81, 106, 90]))
        if index == 4:
            incidentes = [r for r in records if r["estado"] != "OK"]
            for r in incidentes:
                mensaje = f"{r.get('numero_factura', r['archivo'])}: {r.get('error') or r.get('advertencias') or 'Revisar campos ausentes.'}"
                story.append(Paragraph(escape(mensaje), styles["TextoInforme"]))

        if index == 6 and modalidades:
            rows = [["Modalidad", "Facturas / alcance", "Tiempo (s)", "Coste (USD)"]]
            for r in modalidades['mediciones']:
                tiempo = f"{r['tiempo_s']:.3f}" if r['tiempo_s'] is not None else "No medido"
                alcance = f"{r['facturas']}" if r['facturas'] is not None else "Asistido"
                if r['nombre'].startswith('Widget'):
                    alcance += " / 0 PDF"
                elif r['nombre'].startswith('Demo'):
                    alcance += " / sólo OCR"
                rows.append([r['nombre'], alcance, tiempo, r['coste_usd']])
            story.append(tabla(rows, [190, 130, 100, 90]))

    def pie(canvas, doc):
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(colors.HexColor("#63748a"))
        canvas.drawString(42, 24, "Prueba evaluativa · Evidencia de ejecución")
        canvas.drawRightString(A4[0] - 42, 24, str(doc.page))

    doc = SimpleDocTemplate(str(path), pagesize=A4, rightMargin=42, leftMargin=42,
                            topMargin=36, bottomMargin=42, title="RPA DIAN y extracción OCR")
    doc.build(story, onFirstPage=pie, onLaterPages=pie)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--resultados", type=Path, default=Path("resultados_dian"))
    parser.add_argument("--destino", type=Path, default=Path("entrega"))
    parser.add_argument("--repositorio", default="")
    parser.add_argument("--nota", default="", help="observación humana verificada para el informe")
    parser.add_argument("--adquisicion", type=Path, help="resumen de una ejecución integral previa, cuando se reextraen sus PDF")
    parser.add_argument("--optimizacion", type=Path, help="comparación medida de tiempos antes y después")
    parser.add_argument("--modalidades", type=Path, help="comparación medida de pago, widget y demo gratuita")
    args = parser.parse_args()
    summary = json.loads((args.resultados / "resumen_metricas.json").read_text(encoding="utf-8"))
    records = json.loads((args.resultados / "detalle_proceso.json").read_text(encoding="utf-8"))
    adquisicion = json.loads(args.adquisicion.read_text(encoding="utf-8")) if args.adquisicion else None
    optimizacion = json.loads(args.optimizacion.read_text(encoding="utf-8")) if args.optimizacion else None
    modalidades = json.loads(args.modalidades.read_text(encoding="utf-8")) if args.modalidades else None
    sections = crear_contenido(summary, records, args.repositorio, args.nota, adquisicion, optimizacion, modalidades)
    args.destino.mkdir(parents=True, exist_ok=True)
    lines = ["# RPA DIAN y extracción OCR", "", "Documento de respuestas · Prueba técnica", ""]
    for title, paragraphs in sections:
        lines += [f"## {title}", "", "\n\n".join(paragraphs), ""]
        if title == "Resultados medidos":
            lines += ["| # | Factura | Estado | Productos | Cobertura | Concordancia | Total (s) |",
                      "|---|---|---|---:|---:|---:|---:|"]
            for i, r in enumerate(records, 1):
                lines.append(f"| {i} | {r.get('numero_factura', r['archivo'])} | {r['estado']} | {r.get('productos', 0)} | "
                             f"{r.get('cobertura_campos_pct', 0)} % | {r.get('concordancia_ocr_pdf_pct', 'n/d')} % | {r['total_s']:.2f} |")
            consulta = adquisicion or summary
            lines += ["", f"Tiempo de esta ejecución: {summary['tiempo_total_s']} s. Tareas CAPTCHA de la ejecución "
                      f"integral registrada: {consulta['captcha_tareas_remotas']}. Coste reportado: "
                      f"USD {consulta['captcha_coste_reportado_usd']}.", ""]
    stem = args.destino / "INFORME_PRUEBA_TECNICA"
    stem.with_suffix(".md").write_text("\n".join(lines), encoding="utf-8")
    crear_pdf(stem.with_suffix(".pdf"), sections, summary, records, adquisicion, modalidades)
    print(stem.with_suffix(".pdf").resolve())


if __name__ == "__main__":
    main()
