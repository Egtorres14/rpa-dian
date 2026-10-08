"""Ejecuta el lote fijo de evaluación y prepara resultados para GitHub Actions."""

import csv
import hashlib
import json
import os
import re
import subprocess
import sys
import zipfile
from pathlib import Path

BASE = Path(__file__).resolve().parent
NOMBRE_PDF = re.compile(r"factura_\d{2}_[0-9a-f]{12}\.pdf")
REPORTES = ("facturas_consolidadas.csv", "resumen_metricas.json", "detalle_proceso.json",
            "metricas.csv", "reporte_metricas.md")


def solicitud(event_name: str, event: dict) -> tuple[str, int]:
    """Acepta sólo modalidades conocidas y una o diez facturas del lote incluido."""
    if event_name == "workflow_dispatch":
        inputs = event.get("inputs", {})
        mode, count = inputs.get("modalidad"), inputs.get("limite")
        if mode not in ("demo", "dian") or count not in ("1", "10"):
            raise ValueError("Modalidad o cantidad no permitida.")
        return mode, 10 if mode == "demo" else int(count)
    if event_name == "issues":
        issue = event.get("issue", {})
        labels = {item["name"] for item in issue.get("labels", [])}
        if "evaluacion-rpa" not in labels:
            raise ValueError("La solicitud no tiene la etiqueta de evaluación.")
        sections = re.findall(r"^### Facturas\s*\n+(.*?)(?=^### |\Z)",
                              issue.get("body") or "", re.MULTILINE | re.DOTALL)
        if len(sections) != 1 or sections[0].strip() not in ("1", "10"):
            raise ValueError("El formulario debe solicitar una o diez facturas.")
        return "dian", int(sections[0].strip())
    raise ValueError("Evento no permitido.")


def leer_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def filas_csv(path: Path) -> int:
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return sum(1 for _ in csv.DictReader(stream))


def validar_resultados(output: Path, expected: int, returncode: int) -> tuple[dict, list]:
    """REVISAR es admisible con CSV completo; errores y resultados parciales fallan."""
    if returncode not in (0, 1):
        raise ValueError("El proceso no terminó correctamente.")
    summary = leer_json(output / "resumen_metricas.json")
    records = leer_json(output / "detalle_proceso.json")
    if (summary["facturas_solicitadas"] != expected or len(records) != expected
            or summary["errores"] or summary["omitidas"]
            or summary["ok"] + summary["revisar"] != expected):
        raise ValueError("El lote tiene errores o no está completo. Revisa el registro de ejecución.")
    states = [record["estado"] for record in records]
    if (any(state not in ("OK", "REVISAR") for state in states)
            or states.count("OK") != summary["ok"] or states.count("REVISAR") != summary["revisar"]):
        raise ValueError("Los estados del detalle no coinciden con el resumen.")
    names = [record["archivo"] for record in records]
    if len(set(names)) != expected or any(not NOMBRE_PDF.fullmatch(name) for name in names):
        raise ValueError("Nombre de factura no permitido.")
    rows = 0
    for record in records:
        count = filas_csv(output / "csv" / Path(record["archivo"]).with_suffix(".csv"))
        if count < 1 or count != record["productos"]:
            raise ValueError("El CSV no contiene todos los productos de la factura.")
        rows += count
    if rows != summary["filas_productos"] or filas_csv(output / "facturas_consolidadas.csv") != rows:
        raise ValueError("El consolidado no coincide con los CSV por factura.")
    return summary, records


def empaquetar(output: Path, pdf_dir: Path, records: list, target: Path, secreto: str = ""):
    """Publica una lista cerrada de archivos; excluye HTML, sesiones e imágenes."""
    files = [(output / name, name) for name in REPORTES]
    for record in records:
        name = record["archivo"]
        if not NOMBRE_PDF.fullmatch(name):
            raise ValueError("Nombre de factura no permitido.")
        pdf = pdf_dir / name
        data = pdf.read_bytes()
        if not data.startswith(b"%PDF-") or (record.get("sha256_pdf")
                and hashlib.sha256(data).hexdigest() != record["sha256_pdf"]):
            raise ValueError("El PDF no coincide con el documento procesado.")
        files.extend([(pdf, "pdf/" + name),
                      (output / "csv" / Path(name).with_suffix(".csv"), "csv/" + Path(name).with_suffix(".csv").name)])
    # Comprueba toda la selección antes de crear el ZIP, incluso si se reutiliza el destino.
    payloads = [(name, path.read_bytes()) for path, name in files]
    if secreto and any(secreto.encode() in data for _, data in payloads):
        raise ValueError("Se detectó una credencial en los resultados; no se publican.")
    target.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in payloads:
            archive.writestr(name, data)


def resumen_github(mode: str, summary: dict):
    text = (f"## Resultado: {mode}\n\n"
            f"{summary['facturas_solicitadas']} facturas: {summary['ok']} OK, "
            f"{summary['revisar']} para revisar, {summary['errores']} errores.\n\n"
            f"Tiempo del RPA y OCR: {summary['tiempo_total_s']} s; excluye instalación y aprobación.\n\n"
            f"Tareas CAPTCHA: {summary['captcha_tareas_remotas']}; "
            f"coste reportado: USD {summary['captcha_coste_reportado_usd']}.\n\n"
            "Descarga el artefacto **resultados** al final de esta ejecución. "
            "Contiene PDF, CSV por factura, consolidado y métricas.\n")
    if summary["revisar"]:
        text += "\nHay observaciones de calidad. Consulta `detalle_proceso.json` antes de usar los CSV.\n"
        print("::warning::Lote completo con observaciones de calidad en el OCR.")
    if path := os.environ.get("GITHUB_STEP_SUMMARY"):
        with Path(path).open("a", encoding="utf-8") as stream:
            stream.write(text)


def main() -> int:
    key = os.environ.get("TWOCAPTCHA_API_KEY", "")
    try:
        mode, count = solicitud(os.environ["GITHUB_EVENT_NAME"],
                                leer_json(Path(os.environ["GITHUB_EVENT_PATH"])))
        output = BASE / "resultados_github"
        if output.exists():
            raise ValueError("La carpeta de salida ya existe; se necesita una ejecución limpia.")
        command = [sys.executable, str(BASE / "dian_rpa.py")]
        if mode == "dian":
            if not key:
                raise ValueError("Configura TWOCAPTCHA_API_KEY en el entorno evaluacion.")
            check = subprocess.run(command + ["verificar-captcha"], capture_output=True, text=True, check=False)
            if check.returncode:
                raise ValueError("No se pudo verificar la clave con 2Captcha; no se inicia el lote.")
            balance = json.loads(check.stdout)
            if not balance.get("clave_valida") or not balance.get("saldo_positivo"):
                raise ValueError("La clave no es válida o no tiene saldo positivo.")
            command += ["dian", "--captcha", "2captcha", "--limite", str(count),
                        "--attempts", "1", "--max-captcha-tasks", str(2 * count)]
            pdf_dir = output / "pdf"
        else:
            command += ["extraer", "--demo"]
            pdf_dir = BASE / "muestras" / "pdf"
        command += ["--ocr-workers", "2", "--out", str(output)]
        result = subprocess.run(command, check=False)
        summary, records = validar_resultados(output, count, result.returncode)
        empaquetar(output, pdf_dir, records, BASE / "artefactos_github/resultados.zip", secreto=key)
        resumen_github(mode, summary)
        return 0
    except (ValueError, OSError, KeyError, TypeError) as exc:
        message = str(exc).replace(key, "[clave oculta]") if key else str(exc)
        print(f"La evaluación no se completó: {message}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
