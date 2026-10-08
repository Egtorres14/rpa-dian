"""Compara dos carpetas de resultados: estados por factura, productos extraídos y tiempos.

Uso: python scripts/comparar_resultados.py CARPETA_A CARPETA_B
Termina con código 0 si los datos extraídos coinciden y 1 si hay diferencias.
"""
from __future__ import annotations

import csv
import json
import sys
from pathlib import Path


def cargar(carpeta: Path):
    resumen = json.loads((carpeta / "resumen_metricas.json").read_text(encoding="utf-8"))
    detalle = json.loads((carpeta / "detalle_proceso.json").read_text(encoding="utf-8"))
    with (carpeta / "facturas_consolidadas.csv").open(encoding="utf-8-sig", newline="") as f:
        filas = list(csv.DictReader(f))
    return resumen, {r["archivo"]: r for r in detalle}, filas


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass
    if len(sys.argv) != 3:
        print(__doc__)
        return 2
    a, b = Path(sys.argv[1]), Path(sys.argv[2])
    for carpeta in (a, b):
        if not (carpeta / "resumen_metricas.json").exists():
            print(f"Falta {carpeta}\\resumen_metricas.json. Ejecuta antes la demo de esa versión.")
            return 2
    res_a, det_a, filas_a = cargar(a)
    res_b, det_b, filas_b = cargar(b)

    print(f"A: {a.name}\nB: {b.name}\n")
    diferencias = 0

    print("Estado por factura")
    for archivo in sorted(set(det_a) | set(det_b)):
        ea = det_a.get(archivo, {}).get("estado", "(falta)")
        eb = det_b.get(archivo, {}).get("estado", "(falta)")
        marca = "igual" if ea == eb else "DISTINTO"
        diferencias += ea != eb
        print(f"  {archivo:<34} {ea:<8} {eb:<8} {marca}")

    columnas = [c for c in (filas_a[0].keys() if filas_a else []) if c != "archivo"]
    clave = lambda r: (r.get("archivo"), r.get("pagina"), r.get("linea_producto"))
    ia, ib = {clave(r): r for r in filas_a}, {clave(r): r for r in filas_b}
    cambios = []
    for k in sorted(set(ia) | set(ib), key=str):
        ra, rb = ia.get(k), ib.get(k)
        if ra is None or rb is None:
            cambios.append(f"  {k[0]} pág. {k[1]} línea {k[2]}: sólo existe en {'B' if ra is None else 'A'}")
            continue
        for c in columnas:
            if ra.get(c) != rb.get(c):
                cambios.append(f"  {k[0]} pág. {k[1]} línea {k[2]} · {c}: {ra.get(c)!r} -> {rb.get(c)!r}")
    diferencias += len(cambios)
    print(f"\nProductos extraídos: A={len(filas_a)} filas, B={len(filas_b)} filas")
    print("\n".join(cambios) if cambios else "  Todos los campos coinciden.")

    print("\nTiempos (orientativos: dependen de la carga del equipo)")
    for campo, nombre in [("tiempo_total_s", "Total"), ("tiempo_ocr_total_s", "OCR"),
                          ("tiempo_parseo_total_s", "Parseo")]:
        print(f"  {nombre:<7} A={res_a.get(campo)} s   B={res_b.get(campo)} s")

    print("\nResultado:", "SIN DIFERENCIAS en los datos extraídos" if not diferencias
          else f"{diferencias} diferencia(s)")
    return 0 if not diferencias else 1


if __name__ == "__main__":
    raise SystemExit(main())
