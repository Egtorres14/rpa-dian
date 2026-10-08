"""Genera RESULTADOS.pdf con el resumen visual de una carpeta de resultados y lo abre en VS Code.

Uso:
    python ver_resultados.py [carpeta] [--abrir]
    python ver_resultados.py --solo-abrir archivo.pdf|.csv|.xlsx|.docx

Sin carpeta, usa la ejecución más reciente (resultados_*/resumen_metricas.json).
Sólo lee los archivos que ya generó dian_rpa.py; no vuelve a ejecutar OCR.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.graphics.shapes import Drawing, Line, Rect, String
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (KeepTogether, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table,
                                TableStyle)

BASE = Path(__file__).resolve().parent

# Tinta y superficies (paleta de referencia, modo claro: un PDF se imprime en blanco).
TINTA = colors.HexColor("#0b0b0b")
TINTA_2 = colors.HexColor("#52514e")
TINTA_SUAVE = colors.HexColor("#898781")
REJILLA = colors.HexColor("#e1e0d9")
EJE = colors.HexColor("#c3c2b7")
PLANO = colors.HexColor("#f6f5f1")
BORDE = colors.HexColor("#dcdad2")
SERIE = colors.HexColor("#2a78d6")
ACENTO = colors.HexColor("#124e64")
# Estado: color + etiqueta de texto (el color nunca va solo).
ESTADOS = {
    "OK": (colors.HexColor("#0ca30c"), colors.HexColor("#e2f3e2")),
    "REVISAR": (colors.HexColor("#fab219"), colors.HexColor("#fff2d1")),
    "OMITIDA": (colors.HexColor("#ec835a"), colors.HexColor("#fde5da")),
    "ERROR": (colors.HexColor("#d03b3b"), colors.HexColor("#f8dddd")),
}
VACIO = colors.HexColor("#fff2d1")


def registrar_fuentes() -> tuple[str, str]:
    """Segoe UI en Windows; Helvetica en otros sistemas."""
    fuentes = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
    normal, negrita = fuentes / "segoeui.ttf", fuentes / "seguisb.ttf"
    if normal.exists() and negrita.exists():
        pdfmetrics.registerFont(TTFont("UI", str(normal)))
        pdfmetrics.registerFont(TTFont("UI-Semibold", str(negrita)))
        pdfmetrics.registerFontFamily("UI", normal="UI", bold="UI-Semibold")
        return "UI", "UI-Semibold"
    return "Helvetica", "Helvetica-Bold"


FUENTE, FUENTE_B = registrar_fuentes()


def estilo(nombre, size=9, fuente=FUENTE, color=TINTA, leading=None, **extra):
    return ParagraphStyle(nombre, fontName=fuente, fontSize=size, textColor=color,
                          leading=leading or size * 1.3, **extra)


E = {
    "titulo": estilo("titulo", 20, FUENTE_B, ACENTO, 24),
    "sub": estilo("sub", 9.5, color=TINTA_2),
    "h2": estilo("h2", 12.5, FUENTE_B, ACENTO, 16, spaceBefore=4, spaceAfter=6),
    "kpi_label": estilo("kpi_label", 8, color=TINTA_2),
    "kpi_valor": estilo("kpi_valor", 17, FUENTE_B, TINTA, 21),
    "kpi_nota": estilo("kpi_nota", 7.5, color=TINTA_SUAVE),
    "celda": estilo("celda", 8, leading=10),
    "celda_d": estilo("celda_d", 8, leading=10, alignment=2),
    "cab": estilo("cab", 8, FUENTE_B, TINTA_2, 10),
    "cab_d": estilo("cab_d", 8, FUENTE_B, TINTA_2, 10, alignment=2),
    "texto": estilo("texto", 9, color=TINTA_2, leading=13),
    "nota": estilo("nota", 7.5, color=TINTA_SUAVE, leading=10),
}


def num_es(valor, decimales=2) -> str:
    """1234567.5 -> 1.234.567,50 (formato colombiano)."""
    try:
        d = Decimal(str(valor))
    except (InvalidOperation, ValueError):
        return str(valor)
    texto = f"{d:,.{decimales}f}"
    return texto.replace(",", "_").replace(".", ",").replace("_", ".")


def segundos(valor) -> str:
    if valor is None:
        return "—"
    valor = float(valor)
    if valor >= 90:
        return f"{int(valor // 60)} min {num_es(valor % 60, 0)} s"
    return f"{num_es(valor, 1)} s"


def fecha_local(iso: str | None) -> str:
    if not iso:
        return ""
    try:
        return datetime.fromisoformat(iso).astimezone(timezone(timedelta(hours=-5))).strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return iso


def P(texto, st="celda") -> Paragraph:
    return Paragraph(escape(str(texto)), E[st])


def ultima_carpeta() -> Path | None:
    candidatas = sorted(BASE.glob("resultados_*/resumen_metricas.json"), key=lambda p: p.stat().st_mtime)
    return candidatas[-1].parent if candidatas else None


def leer_productos(carpeta: Path) -> list[dict]:
    ruta = carpeta / "facturas_consolidadas.csv"
    if not ruta.exists():
        return []
    with ruta.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


# ---------------------------------------------------------------- bloques

def tarjetas(summary: dict, records: list[dict], ancho: float) -> Table:
    total = summary.get("facturas_solicitadas", len(records))
    fallidas = summary.get("errores", 0) + summary.get("omitidas", 0)
    if summary.get("modo") == "dian":
        captcha = ("Coste CAPTCHA", num_es(summary.get("captcha_coste_reportado_usd") or 0, 4),
                   f"USD · {summary.get('captcha_resueltos', 0)} resueltos", None)
    else:
        # Extracción de PDF locales: no se consulta la DIAN, así que no hay CAPTCHA que medir.
        captcha = ("CAPTCHA", "—", "no aplica: PDF locales, sin DIAN", None)
    datos = [
        ("Facturas", str(total), f"{summary.get('paginas', 0)} páginas", None),
        ("OK", str(summary.get("ok", 0)), "listas para usar", "OK"),
        ("Revisar", str(summary.get("revisar", 0)), "datos a comprobar", "REVISAR"),
        ("Error u omitida", str(fallidas), "sin resultado completo", "ERROR"),
        ("Productos", str(summary.get("filas_productos", 0)), "filas extraídas", None),
        ("Tiempo total", segundos(summary.get("tiempo_total_s")),
         f"media {segundos(summary.get('tiempo_factura_medio_s'))} por factura", None),
        ("Concordancia OCR", f"{num_es(summary.get('concordancia_ocr_pdf_media_pct', 0), 1)} %",
         f"cobertura {num_es(summary.get('cobertura_campos_media_pct', 0), 1)} %", None),
        captcha,
    ]
    # Las columnas impares son huecos en blanco entre tarjetas.
    n, gap = len(datos), 6
    fila, anchos = [], []
    for i, (l, v, nota, _) in enumerate(datos):
        if i:
            fila.append("")
            anchos.append(gap)
        fila.append([P(l, "kpi_label"), P(v, "kpi_valor"), P(nota, "kpi_nota")])
        anchos.append((ancho - gap * (n - 1)) / n)
    t = Table([fila], colWidths=anchos, hAlign="LEFT")
    cmds = [("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 8), ("RIGHTPADDING", (0, 0), (-1, -1), 6),
            ("TOPPADDING", (0, 0), (-1, -1), 7), ("BOTTOMPADDING", (0, 0), (-1, -1), 8)]
    for i, (*_, estado) in enumerate(datos):
        c = 2 * i
        cmds += [("BACKGROUND", (c, 0), (c, 0), PLANO), ("BOX", (c, 0), (c, 0), 0.5, BORDE)]
        if estado:
            cmds.append(("LINEABOVE", (c, 0), (c, 0), 3, ESTADOS[estado][0]))
    t.setStyle(TableStyle(cmds))
    return t


def tabla(filas, anchos, extra=None, alinear_derecha=()) -> Table:
    t = Table(filas, colWidths=anchos, repeatRows=1, hAlign="LEFT")
    cmds = [("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("LINEBELOW", (0, 0), (-1, 0), 0.8, EJE),
            ("LINEBELOW", (0, 1), (-1, -1), 0.4, REJILLA),
            ("TOPPADDING", (0, 0), (-1, -1), 4.5), ("BOTTOMPADDING", (0, 0), (-1, -1), 4.5),
            ("LEFTPADDING", (0, 0), (-1, -1), 5), ("RIGHTPADDING", (0, 0), (-1, -1), 5)]
    t.setStyle(TableStyle(cmds + (extra or [])))
    return t


def tabla_estados(records: list[dict], ancho: float) -> Table:
    cab = ["#", "Factura", "Archivo", "Fecha", "NIT emisor", "Págs.", "Productos", "Cobertura",
           "Concordancia", "Confianza OCR", "Tiempo", "Estado"]
    derecha = {5, 6, 7, 8, 9, 10}
    filas = [[P(c, "cab_d" if i in derecha else "cab") for i, c in enumerate(cab)]]
    extra = []
    for i, r in enumerate(records, 1):
        estado = r.get("estado", "")
        conc = r.get("concordancia_ocr_pdf_pct")
        fila = [i, r.get("numero_factura") or "—", r.get("archivo", ""), r.get("fecha_emision") or "—",
                r.get("nit_emisor") or "—", r.get("paginas", 0), r.get("productos", 0),
                f"{num_es(r.get('cobertura_campos_pct', 0), 1)} %",
                f"{num_es(conc, 1)} %" if conc is not None else "n/d",
                num_es(r["confianza_ocr_media"], 1) if r.get("confianza_ocr_media") is not None else "—",
                segundos(r.get("total_s")), estado]
        filas.append([P(v, "celda_d" if j in derecha else "celda") for j, v in enumerate(fila)])
        if estado in ESTADOS:
            fuerte, suave = ESTADOS[estado]
            extra += [("BACKGROUND", (-1, i), (-1, i), suave), ("LINEBEFORE", (-1, i), (-1, i), 3, fuerte)]
    proporciones = [3, 11, 19, 9, 9, 5, 7, 8, 9, 9, 8, 8]
    anchos = [ancho * p / sum(proporciones) for p in proporciones]
    return tabla(filas, anchos, extra)


def observaciones(summary: dict, records: list[dict]) -> list:
    bloque = []
    for r in records:
        if r.get("estado") != "OK":
            texto = r.get("error") or r.get("advertencias") or "Revisar campos ausentes."
            nombre = r.get("numero_factura") or r.get("archivo", "")
            bloque.append(Paragraph(f"<b>{escape(nombre)}</b> ({escape(r.get('estado', ''))}): {escape(str(texto))}",
                                    E["texto"]))
    for aviso in summary.get("advertencias_ocr") or []:
        bloque.append(Paragraph(escape(str(aviso)), E["texto"]))
    return bloque


def tabla_productos(productos: list[dict], ancho: float) -> Table:
    cab = ["Factura", "Fecha", "NIT emisor", "Código", "Descripción", "Cantidad", "Precio unitario", "Pág./línea"]
    derecha = {5, 6, 7}
    filas = [[P(c, "cab_d" if i in derecha else "cab") for i, c in enumerate(cab)]]
    extra = []
    columnas = ["numero_factura", "fecha_emision", "nit_emisor", "codigo", "descripcion"]
    for i, p in enumerate(productos, 1):
        valores = [p.get(c, "") for c in columnas]
        valores += [num_es(p["cantidad"]) if p.get("cantidad") else "",
                    num_es(p["precio_unitario"]) if p.get("precio_unitario") else "",
                    f"{p.get('pagina', '')}/{p.get('linea_producto', '')}"]
        celdas = []
        for j, v in enumerate(valores):
            if v in ("", None):
                celdas.append(P("(vacío)", "nota"))
                extra.append(("BACKGROUND", (j, i), (j, i), VACIO))
            else:
                celdas.append(P(v, "celda_d" if j in derecha else "celda"))
        filas.append(celdas)
    proporciones = [10, 8, 8, 8, 40, 7, 11, 7]
    anchos = [ancho * p / sum(proporciones) for p in proporciones]
    return tabla(filas, anchos, extra)


def grafico_tiempos(records: list[dict], ancho: float) -> Drawing:
    """Barras horizontales de una sola serie: tiempo total por factura."""
    datos = [(r.get("numero_factura") or r.get("archivo", ""), float(r.get("total_s") or 0)) for r in records]
    alto_barra, paso = 11, 19
    margen_izq, margen_der, arriba, abajo = 92, 40, 6, 22
    alto = arriba + paso * len(datos) + abajo
    d = Drawing(ancho, alto)
    maximo = max((v for _, v in datos), default=1) or 1
    # Marcas del eje en números redondos.
    paso_eje = next(s for s in (1, 2, 5, 10, 15, 20, 30, 60, 120, 300, 600, 1e9) if maximo / s <= 6)
    tope = paso_eje * (int(maximo // paso_eje) + 1)
    ancho_plot = ancho - margen_izq - margen_der
    x = lambda v: margen_izq + ancho_plot * v / tope
    base_y = abajo
    for k in range(int(tope // paso_eje) + 1):
        vx = x(k * paso_eje)
        d.add(Line(vx, base_y, vx, alto - arriba, strokeColor=REJILLA, strokeWidth=0.6))
        d.add(String(vx, base_y - 12, f"{num_es(k * paso_eje, 0)} s", fontName=FUENTE, fontSize=7,
                     fillColor=TINTA_SUAVE, textAnchor="middle"))
    d.add(Line(margen_izq, base_y, margen_izq, alto - arriba, strokeColor=EJE, strokeWidth=0.8))
    for i, (nombre, valor) in enumerate(datos):
        y = alto - arriba - paso * (i + 1) + (paso - alto_barra) / 2
        w = max(x(valor) - margen_izq, 1)
        # Extremo redondeado de 4 px y base recta sobre el eje.
        d.add(Rect(margen_izq, y, w, alto_barra, rx=3, ry=3, fillColor=SERIE, strokeColor=None))
        d.add(Rect(margen_izq, y, min(w, 4), alto_barra, fillColor=SERIE, strokeColor=None))
        d.add(String(margen_izq - 6, y + 2.5, nombre[:18], fontName=FUENTE, fontSize=7.5,
                     fillColor=TINTA_2, textAnchor="end"))
        d.add(String(margen_izq + w + 4, y + 2.5, num_es(valor, 1), fontName=FUENTE, fontSize=7.5,
                     fillColor=TINTA, textAnchor="start"))
    return d


# ---------------------------------------------------------------- documento

def generar(carpeta: Path) -> Path:
    summary = json.loads((carpeta / "resumen_metricas.json").read_text(encoding="utf-8"))
    detalle = carpeta / "detalle_proceso.json"
    records = json.loads(detalle.read_text(encoding="utf-8")) if detalle.exists() else []
    productos = leer_productos(carpeta)

    destino = carpeta / "RESULTADOS.pdf"
    pagina = landscape(A4)
    margen = 34
    ancho = pagina[0] - 2 * margen
    doc = SimpleDocTemplate(str(destino), pagesize=pagina, leftMargin=margen, rightMargin=margen,
                            topMargin=30, bottomMargin=34, title=f"Resultados RPA DIAN · {carpeta.name}",
                            author="ver_resultados.py")

    modo = {"extraer": "Extracción OCR de PDF locales", "dian": "Consulta y descarga DIAN + OCR"}.get(
        summary.get("modo", ""), summary.get("modo", ""))
    config = (f"OCR x{summary.get('ocr_workers', '?')} · {summary.get('dpi', '?')} DPI · "
              f"inicio {fecha_local(summary.get('inicio_utc'))} (hora Colombia)")
    story = [
        Paragraph("Resultados RPA DIAN", E["titulo"]),
        Paragraph(escape(f"{modo} · carpeta {carpeta.name} · {config}"), E["sub"]),
        Spacer(1, 12),
        tarjetas(summary, records, ancho),
        Spacer(1, 16),
        Paragraph("Estado por factura", E["h2"]),
        tabla_estados(records, ancho),
    ]
    obs = observaciones(summary, records)
    if obs:
        story += [Spacer(1, 12), KeepTogether([Paragraph("Observaciones", E["h2"]), *obs])]

    story += [PageBreak(), Paragraph("Productos extraídos", E["h2"])]
    if productos:
        story += [Paragraph("Una fila por producto, tal como sale del OCR de las imágenes. Las celdas "
                            "resaltadas están vacías en el resultado.", E["nota"]), Spacer(1, 6),
                  tabla_productos(productos, ancho)]
    else:
        story.append(Paragraph("No hay facturas_consolidadas.csv en esta carpeta.", E["texto"]))

    if records:
        bloque = [Paragraph("Tiempo total por factura (segundos)", E["h2"]), grafico_tiempos(records, ancho * 0.75)]
        if summary.get("modo") == "dian" and summary.get("nota_tiempos"):
            bloque.append(Paragraph(escape(summary["nota_tiempos"]), E["nota"]))
        story += [Spacer(1, 16), KeepTogether(bloque)]

    generado = datetime.now().strftime("%Y-%m-%d %H:%M")

    def pie(canvas, doc_):
        canvas.saveState()
        canvas.setFont(FUENTE, 7.5)
        canvas.setFillColor(TINTA_SUAVE)
        canvas.drawString(margen, 18, f"Generado {generado} · {carpeta.resolve()}")
        canvas.drawRightString(pagina[0] - margen, 18, f"Página {doc_.page}")
        canvas.restoreState()

    doc.build(story, onFirstPage=pie, onLaterPages=pie)
    return destino


def abrir(pdf: Path) -> None:
    """Abre el archivo en la ventana actual de VS Code (PDF: tomoki1207.pdf; CSV, Excel y Word:
    Office Viewer) o, sin VS Code, con el programa predeterminado de Windows."""
    pdf = pdf.resolve()
    code = shutil.which("code")
    if code:
        resultado = subprocess.run([code, "-r", str(pdf)], capture_output=True)
        if resultado.returncode == 0:
            print(f"Abierto en VS Code: {pdf}")
            return
    if hasattr(os, "startfile"):
        os.startfile(pdf)  # noqa: S606 - abre el visor predeterminado de Windows
        print(f"Abierto con el visor del sistema: {pdf}")
    else:
        print(f"PDF generado: {pdf}")


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("carpeta", nargs="?", type=Path, help="carpeta de resultados (por defecto, la más reciente)")
    parser.add_argument("--abrir", action="store_true", help="abre el PDF en VS Code al terminar")
    parser.add_argument("--solo-abrir", type=Path, metavar="ARCHIVO",
                        help="sólo abre un archivo existente (PDF, CSV, Excel o Word) en VS Code")
    args = parser.parse_args()

    if args.solo_abrir:
        if not args.solo_abrir.is_file():
            parser.error(f"No existe {args.solo_abrir}")
        abrir(args.solo_abrir)
        return 0
    carpeta = args.carpeta or ultima_carpeta()
    if carpeta is None:
        parser.error("No hay carpetas resultados_* todavía. Ejecuta primero: .\\rpa demo")
    if not (carpeta / "resumen_metricas.json").exists():
        parser.error(f"{carpeta} no contiene resumen_metricas.json")
    pdf = generar(carpeta)
    print(f"PDF de resultados: {pdf.resolve()}")
    if args.abrir:
        abrir(pdf)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
