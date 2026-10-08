"""Consulta las ejecuciones de GitHub Actions del repositorio sin salir de VS Code.

Uso:
    python scripts/github_actions.py                    lista workflows y últimas ejecuciones
    python scripts/github_actions.py ID                 trabajos, pasos y artefactos de una ejecución
    python scripts/github_actions.py ID --descargar     descarga el artefacto "resultados" y abre su PDF
    python scripts/github_actions.py ID --log           guarda el registro completo y lo abre en VS Code
    python scripts/github_actions.py [ID] --web         abre Actions (o la ejecución) en el navegador

Listar y ver el detalle usa la API pública de GitHub (no requiere sesión; GITHUB_TOKEN es opcional).
Descargar artefactos y registros requiere GitHub CLI (gh) con sesión iniciada.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
import webbrowser
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
REPO = "Egtorres14/rpa-dian"
API = "https://api.github.com"
COLOMBIA = timezone(timedelta(hours=-5))
CONCLUSION = {"success": "ÉXITO", "failure": "FALLO", "cancelled": "CANCELADA", "skipped": "OMITIDA",
              "timed_out": "TIEMPO AGOTADO", "action_required": "REQUIERE APROBACIÓN", "neutral": "NEUTRA"}
ESTADO = {"queued": "EN COLA", "in_progress": "EN CURSO", "waiting": "ESPERANDO", "pending": "PENDIENTE",
          "requested": "SOLICITADA"}


def api(ruta: str):
    cabeceras = {"Accept": "application/vnd.github+json", "User-Agent": "rpa-dian-local",
                 "X-GitHub-Api-Version": "2022-11-28"}
    if token := os.environ.get("GITHUB_TOKEN"):
        cabeceras["Authorization"] = f"Bearer {token}"
    try:
        with urllib.request.urlopen(urllib.request.Request(API + ruta, headers=cabeceras), timeout=20) as r:
            return json.load(r)
    except urllib.error.HTTPError as exc:
        if exc.code == 403:
            raise SystemExit("GitHub limitó las consultas sin sesión (60 por hora). Espera o define GITHUB_TOKEN.")
        if exc.code == 404:
            raise SystemExit(f"No encontrado: {ruta}. Revisa el número de ejecución o --repo.")
        raise SystemExit(f"Error de la API de GitHub: HTTP {exc.code}")
    except urllib.error.URLError as exc:
        raise SystemExit(f"Sin conexión con GitHub: {exc.reason}")


def hora(iso: str | None) -> str:
    if not iso:
        return "—"
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(COLOMBIA).strftime("%Y-%m-%d %H:%M")


def duracion(inicio: str | None, fin: str | None) -> str:
    if not inicio or not fin:
        return "—"
    s = (datetime.fromisoformat(fin.replace("Z", "+00:00")) - datetime.fromisoformat(inicio.replace("Z", "+00:00"))).total_seconds()
    if s < 0:
        return "—"
    return f"{int(s // 60)} min {int(s % 60):02d} s" if s >= 60 else f"{int(s)} s"


def resultado(obj: dict) -> str:
    if obj.get("status") != "completed":
        return ESTADO.get(obj.get("status"), str(obj.get("status")).upper())
    return CONCLUSION.get(obj.get("conclusion"), str(obj.get("conclusion")).upper())


def listar(repo: str, limite: int) -> None:
    workflows = api(f"/repos/{repo}/actions/workflows")["workflows"]
    print(f"Repositorio: https://github.com/{repo}/actions\n")
    print("Workflows")
    for w in workflows:
        print(f"  {w['name']}  ({w['path']}, {'activo' if w['state'] == 'active' else w['state']})")
    runs = api(f"/repos/{repo}/actions/runs?per_page={limite}")
    print(f"\nÚltimas ejecuciones ({len(runs['workflow_runs'])} de {runs['total_count']}; hora Colombia)")
    print(f"  {'ID':<12} {'Fecha':<17} {'Resultado':<19} {'Duración':<11} {'Evento':<18} Título")
    for r in runs["workflow_runs"]:
        print(f"  {r['id']:<12} {hora(r['created_at']):<17} {resultado(r):<19} "
              f"{duracion(r.get('run_started_at'), r['updated_at']):<11} {r['event']:<18} {r['display_title']}")
    print("\nDetalle: .\\rpa actions ID    Resultados: .\\rpa actions ID --descargar    Registro: .\\rpa actions ID --log")


def detalle(repo: str, run_id: str) -> dict:
    r = api(f"/repos/{repo}/actions/runs/{run_id}")
    print(f"{r['display_title']}  ·  ejecución {r['id']}  ·  {resultado(r)}")
    print(f"  Workflow: {r['name']} ({r['path']})")
    print(f"  Lanzada por {r['actor']['login']} ({r['event']}) en la rama {r['head_branch']}, commit {r['head_sha'][:7]}")
    print(f"  Inicio {hora(r.get('run_started_at'))}  ·  duración {duracion(r.get('run_started_at'), r['updated_at'])}")
    print(f"  {r['html_url']}\n")
    for job in api(f"/repos/{repo}/actions/runs/{run_id}/jobs")["jobs"]:
        print(f"  Trabajo «{job['name']}»: {resultado(job)}  ({duracion(job.get('started_at'), job.get('completed_at'))})")
        for paso in job.get("steps") or []:
            print(f"     {paso['number']:>2}. {resultado(paso):<10} {duracion(paso.get('started_at'), paso.get('completed_at')):>9}  {paso['name']}")
    artefactos = api(f"/repos/{repo}/actions/runs/{run_id}/artifacts")["artifacts"]
    print("\n  Artefactos:" if artefactos else "\n  Sin artefactos.")
    for a in artefactos:
        estado = "CADUCADO" if a["expired"] else f"disponible hasta {hora(a['expires_at'])}"
        print(f"     {a['name']}  ·  {a['size_in_bytes'] / 1024:.1f} KB  ·  {estado}")
    return r


def gh() -> str:
    exe = shutil.which("gh")
    if not exe:
        raise SystemExit("Para descargar artefactos o registros instala GitHub CLI: winget install GitHub.cli")
    return exe


def abrir_en_vscode(ruta: Path) -> None:
    code = shutil.which("code")
    if code and subprocess.run([code, "-r", str(ruta)], capture_output=True).returncode == 0:
        print(f"Abierto en VS Code: {ruta}")
    else:
        print(f"Archivo: {ruta}")


def descargar(repo: str, run_id: str) -> None:
    artefactos = api(f"/repos/{repo}/actions/runs/{run_id}/artifacts")["artifacts"]
    disponibles = [a for a in artefactos if not a["expired"]]
    if not disponibles:
        raise SystemExit("Esta ejecución no tiene artefactos disponibles (no se generaron o ya caducaron).")
    destino = BASE / "artefactos_github" / str(run_id)
    if destino.exists():
        shutil.rmtree(destino)
    destino.mkdir(parents=True)
    for a in disponibles:
        print(f"Descargando «{a['name']}»...")
        p = subprocess.run([gh(), "run", "download", str(run_id), "-R", repo, "-n", a["name"], "-D", str(destino)],
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        if p.returncode:
            raise SystemExit(f"gh no pudo descargar el artefacto: {p.stderr.strip() or p.stdout.strip()}\n"
                             "Comprueba la sesión con: gh auth status")
    # El artefacto "resultados" contiene resultados.zip con CSV, PDF y métricas.
    for interior in destino.glob("*.zip"):
        with zipfile.ZipFile(interior) as z:
            for nombre in z.namelist():
                ruta = (destino / nombre).resolve()
                if not ruta.is_relative_to(destino.resolve()):
                    raise SystemExit(f"Ruta no permitida dentro del ZIP: {nombre}")
            z.extractall(destino)
    print(f"Resultados en: {destino}")
    if (destino / "resumen_metricas.json").exists():
        subprocess.run([sys.executable, str(BASE / "ver_resultados.py"), str(destino), "--abrir"])
    else:
        abrir_en_vscode(destino)


def registro(repo: str, run_id: str) -> None:
    p = subprocess.run([gh(), "run", "view", str(run_id), "-R", repo, "--log"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    if p.returncode:
        raise SystemExit(f"gh no pudo leer el registro: {p.stderr.strip()}\nComprueba la sesión con: gh auth status")
    destino = BASE / "artefactos_github" / str(run_id)
    destino.mkdir(parents=True, exist_ok=True)
    archivo = destino / "registro.log"
    archivo.write_text(p.stdout, encoding="utf-8")
    print(f"Registro guardado ({len(p.stdout.splitlines())} líneas).")
    abrir_en_vscode(archivo)


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            # line_buffering: los mensajes salen en orden aunque después hable gh o ver_resultados.
            stream.reconfigure(errors="replace", line_buffering=True)
        except (AttributeError, ValueError):
            pass
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("run_id", nargs="?", help="número de la ejecución (columna ID)")
    parser.add_argument("--repo", default=REPO, help=f"repositorio propietario/nombre (predeterminado {REPO})")
    parser.add_argument("--limite", type=int, default=10, help="ejecuciones a listar (máx. 100)")
    accion = parser.add_mutually_exclusive_group()
    accion.add_argument("--descargar", action="store_true", help="descarga los artefactos y abre RESULTADOS.pdf")
    accion.add_argument("--log", action="store_true", help="guarda el registro completo y lo abre en VS Code")
    accion.add_argument("--web", action="store_true", help="abre la página en el navegador")
    args = parser.parse_args()

    if args.run_id and not args.run_id.isdigit():
        parser.error("El número de ejecución debe ser numérico (columna ID del listado).")
    if args.web:
        url = f"https://github.com/{args.repo}/actions" + (f"/runs/{args.run_id}" if args.run_id else "")
        webbrowser.open(url)
        print(f"Abierto en el navegador: {url}")
        return 0
    if (args.descargar or args.log) and not args.run_id:
        parser.error("Indica el número de la ejecución. Consulta la lista con: .\\rpa actions")
    if args.descargar:
        descargar(args.repo, args.run_id)
    elif args.log:
        registro(args.repo, args.run_id)
    elif args.run_id:
        detalle(args.repo, args.run_id)
    else:
        listar(args.repo, max(1, min(args.limite, 100)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
