"""Local web app for the bidder asset test: upload PDFs in the browser, get PASS/FAIL results.

Usage:
    .venv/bin/python tools/asset_test_app.py [--port 8765] [--no-browser]
    (or double-click "Asset Test.command" in the project folder)

Runs only on this computer (http://127.0.0.1) — nothing is uploaded to the internet.
Each upload batch is a "job" in .tmp/web/<job id>/ and goes through the same tools as the CLI workflow:
    extract_pdf_text.py -> ocr_pdf_pages.py (per bidder) -> parse_financials.py -> evaluate_ratios.py
Joint ventures: reports given the same bidder letter form one JV bid; they are tagged A1, A2, ... (company names
read from the reports) and judged on their combined figures.
Before running, the user labels every uploaded report (e.g. "Bidder A" = ABE Daya Bina Enterprise Sdn Bhd);
the label is used as the bidder name and the company name is shown next to it in the results and Excel.
Pass marks and Group/Company basis come from config/asset_test.json. inputs/overrides.csv is NOT used here
(uploads are new bidders); figures for REVIEW rows can be corrected on the page itself.
"""
import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import threading
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

ROOT = Path(__file__).resolve().parent.parent
TOOLS = ROOT / "tools"
PAGE = TOOLS / "web" / "asset_test.html"
JOBS_DIR = ROOT / ".tmp" / "web"
OCR_CACHE = ROOT / ".tmp" / "ocr"
OCR_SCALE = 2.5
MAX_UPLOAD = 150 * 1024 * 1024

jobs: dict[str, dict] = {}
run_lock = threading.Lock()  # one job at a time - OCR already uses every CPU core


def safe_name(name: str) -> str:
    stem = Path(name).stem
    stem = re.sub(r"[^\w\s().&-]", "", stem).strip() or "Bidder"
    return stem[:80] + ".pdf"


def run_tool(job: dict, script: str, *args: str):
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}  # Windows consoles default to cp1252
    proc = subprocess.run([sys.executable, str(TOOLS / script), *args], cwd=ROOT, env=env,
                          capture_output=True, text=True, encoding="utf-8", errors="replace")
    out = "\n".join(l for l in (proc.stdout + proc.stderr).splitlines() if "[INFO]" not in l)
    job["log"].append(f"$ {script}\n{out}".strip())
    if proc.returncode != 0:
        raise RuntimeError(f"{script} failed:\n{out[-1500:]}")


def run_job(job_id: str):
    job = jobs[job_id]
    d = JOBS_DIR / job_id
    with run_lock:
        try:
            job["state"] = "running"
            job["stage"] = "Reading PDFs"
            run_tool(job, "extract_pdf_text.py", "--input", str(d / "bidders"), "--out", str(d / "text"))
            for f in job["files"]:
                doc = json.loads((d / "text" / f"{f['bidder']}.json").read_text())
                f["pages"] = len(doc["pages"])
                f["ocr_pages"] = sum(1 for p in doc["pages"] if not p["text"].strip())
            for f in job["files"]:
                if f["ocr_pages"]:
                    job["stage"] = f"Scanning (OCR) {f['bidder']}"
                    job["current"] = f["bidder"]
                    run_tool(job, "ocr_pdf_pages.py", "--text", str(d / "text"), "--bidder", f["bidder"],
                             "--scale", str(OCR_SCALE))
                f["done"] = True
            job["current"] = None
            job["stage"] = "Finding figures"
            run_tool(job, "parse_financials.py", "--input", str(d / "text"), "--out", str(d / "financials"))
            job["stage"] = "Calculating ratios"
            (d / "names.json").write_text(json.dumps({f["bidder"]: f["company"] for f in job["files"] if f["company"]}))
            groups = {}
            for f in job["files"]:
                if f.get("group"):
                    groups.setdefault(f["group"], []).append(f["bidder"])
            (d / "groups.json").write_text(json.dumps(groups))
            run_tool(job, "evaluate_ratios.py", "--input", str(d / "financials"), "--overrides", str(d / "none.csv"),
                     "--out", str(d / "asset_test_results.xlsx"), "--json", str(d / "results.json"),
                     "--names", str(d / "names.json"), "--groups", str(d / "groups.json"))
            job["results"] = json.loads((d / "results.json").read_text())
            job["state"] = "done"
            job["stage"] = "Done"
        except Exception as e:
            job["state"] = "error"
            job["error"] = str(e)


def ocr_progress(job: dict) -> list[dict]:
    """Pages OCR'd so far per file, from the OCR cache (ocr_pdf_pages.py writes one file per page)."""
    out = []
    for f in job["files"]:
        done = f.get("ocr_pages", 0) if f.get("done") else 0
        if not f.get("done") and f.get("ocr_pages"):
            cache = OCR_CACHE / f["digest"]
            done = len(list(cache.glob(f"p*_s{OCR_SCALE}.json"))) if cache.exists() else 0
        out.append({"bidder": f["bidder"], "pages": f.get("pages"), "ocr_pages": f.get("ocr_pages"),
                    "ocr_done": min(done, f.get("ocr_pages") or 0), "done": f.get("done", False)})
    return out


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send(self, code=200, body=b"", ctype="application/json", headers=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def send_json(self, obj, code=200):
        self.send(code, json.dumps(obj).encode())

    def job_or_404(self, job_id):
        job = jobs.get(job_id)
        if not job:
            self.send_json({"error": "unknown job"}, 404)
        return job

    def do_GET(self):
        url = urlparse(self.path)
        parts = url.path.strip("/").split("/")
        if url.path in ("/", "/index.html"):
            return self.send(body=PAGE.read_bytes(), ctype="text/html; charset=utf-8")
        if url.path == "/favicon.ico":
            return self.send(204)
        if url.path == "/api/config":
            cfg = json.loads((ROOT / "config" / "asset_test.json").read_text())
            return self.send_json({"current_min": cfg["current_ratio"]["minimum"],
                                   "quick_min": cfg["quick_ratio"]["minimum"],
                                   "basis": cfg.get("statement_basis", "company")})
        if len(parts) >= 3 and parts[:2] == ["api", "jobs"]:
            job = self.job_or_404(parts[2])
            if not job:
                return
            d = JOBS_DIR / parts[2]
            if len(parts) == 3:
                return self.send_json({"state": job["state"], "stage": job["stage"], "error": job.get("error"),
                                       "files": ocr_progress(job), "results": job.get("results")})
            if parts[3] == "excel" and (d / "asset_test_results.xlsx").exists():
                return self.send(body=(d / "asset_test_results.xlsx").read_bytes(),
                                 ctype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                                 headers={"Content-Disposition": 'attachment; filename="asset_test_results.xlsx"'})
            if parts[3] == "log":
                return self.send(body="\n\n".join(job["log"]).encode(), ctype="text/plain; charset=utf-8")
            if parts[3] == "page":
                q = parse_qs(url.query)
                bidder, page = q.get("bidder", [""])[0], int(q.get("page", ["1"])[0])
                pdf_path = d / "bidders" / f"{bidder}.pdf"
                if pdf_path.exists():
                    import io
                    import pypdfium2 as pdfium
                    pdf = pdfium.PdfDocument(pdf_path)
                    if 1 <= page <= len(pdf):
                        buf = io.BytesIO()
                        pdf[page - 1].render(scale=2).to_pil().save(buf, "PNG")
                        return self.send(body=buf.getvalue(), ctype="image/png")
        self.send_json({"error": "not found"}, 404)

    def do_POST(self):
        parts = urlparse(self.path).path.strip("/").split("/")
        if parts == ["api", "jobs"]:
            job_id = uuid.uuid4().hex[:10]
            (JOBS_DIR / job_id / "bidders").mkdir(parents=True)
            (JOBS_DIR / job_id / "none.csv").write_text("bidder,year,field,value,reason\n")
            jobs[job_id] = {"state": "uploading", "stage": "Uploading", "files": [], "log": []}
            return self.send_json({"job": job_id})
        if len(parts) == 4 and parts[:2] == ["api", "jobs"]:
            job = self.job_or_404(parts[2])
            if not job:
                return
            if parts[3] == "files":
                length = int(self.headers.get("Content-Length", 0))
                if length <= 0 or length > MAX_UPLOAD:
                    return self.send_json({"error": "file too large or empty"}, 400)
                data = self.rfile.read(length)
                if not data.startswith(b"%PDF"):
                    return self.send_json({"error": "not a PDF file"}, 400)
                # the user's label (e.g. "Bidder A") becomes the bidder name; the company name is shown next to it
                label = unquote(self.headers.get("X-Label", "")).strip()
                company = re.sub(r"\s+", " ", unquote(self.headers.get("X-Company", ""))).strip()[:120]
                # joint venture: several reports share one bid ("Bidder A"); each is tagged A1, A2 (label "Bidder A1")
                # (company names may be left blank for any report - they are then read from the report)
                group = unquote(self.headers.get("X-Group", "")).strip()
                if not label:  # company name is optional - it is read from the report when left blank
                    return self.send_json({"error": "every report needs a bidder label"}, 400)
                name = safe_name(label)
                if any(f["bidder"] == Path(name).stem for f in job["files"]):
                    return self.send_json({"error": f"{Path(name).stem} is used twice - give each report its own label"}, 400)
                (JOBS_DIR / parts[2] / "bidders" / name).write_bytes(data)
                job["files"].append({"bidder": Path(name).stem, "company": company, "group": group,
                                     "original": unquote(self.headers.get("X-Filename", "")),
                                     "digest": hashlib.sha1(data).hexdigest()[:12]})
                return self.send_json({"ok": True, "bidder": Path(name).stem})
            if parts[3] == "run":
                if not job["files"]:
                    return self.send_json({"error": "no files uploaded"}, 400)
                if job["state"] == "uploading":
                    job["state"] = "queued"
                    job["stage"] = "Waiting for another run to finish" if run_lock.locked() else "Starting"
                    threading.Thread(target=run_job, args=(parts[2],), daemon=True).start()
                return self.send_json({"ok": True})
        self.send_json({"error": "not found"}, 404)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, default=int(os.environ.get("PORT", 8765)))
    ap.add_argument("--no-browser", action="store_true", default=True)
    args = ap.parse_args()

    JOBS_DIR.mkdir(parents=True, exist_ok=True)
    for port in range(args.port, args.port + 20):
        try:
            server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
            break
        except OSError:
            continue
    else:
        print("No free port found", file=sys.stderr)
        return 1
    url = f"http://127.0.0.1:{port}/"
    print(f"Asset test app running at {url}\nKeep this window open while using it. Press Ctrl+C to stop.")
    if not args.no_browser:
        threading.Timer(0.8, webbrowser.open, args=(url,)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
