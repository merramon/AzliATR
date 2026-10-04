"""Extract page-by-page text from bidder PDFs.

Usage:
    .venv/bin/python tools/extract_pdf_text.py [--input inputs/bidders] [--out .tmp/text]

Writes one JSON per PDF: {"bidder", "source_pdf", "pages": [{"page": n, "text": "..."}], "scanned": bool}
A PDF with (almost) no extractable text is flagged "scanned": true — text parsing cannot read it.
"""
import argparse
import json
import sys
from pathlib import Path

import pdfplumber

MIN_CHARS_PER_PAGE = 30  # below this on average, the PDF is treated as scanned/image-only


def extract(pdf_path: Path) -> dict:
    pages = []
    with pdfplumber.open(pdf_path) as pdf:
        for i, page in enumerate(pdf.pages, start=1):
            pages.append({"page": i, "text": page.extract_text() or ""})
    total_chars = sum(len(p["text"].strip()) for p in pages)
    scanned = total_chars < MIN_CHARS_PER_PAGE * max(len(pages), 1)
    return {"bidder": pdf_path.stem, "source_pdf": str(pdf_path), "pages": pages, "scanned": scanned}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--input", default="inputs/bidders")
    ap.add_argument("--out", default=".tmp/text")
    args = ap.parse_args()

    in_dir, out_dir = Path(args.input), Path(args.out)
    pdfs = sorted(p for p in in_dir.iterdir() if p.suffix.lower() == ".pdf") if in_dir.exists() else []
    if not pdfs:
        print(f"No PDFs found in {in_dir}", file=sys.stderr)
        return 1
    out_dir.mkdir(parents=True, exist_ok=True)
    # drop results for PDFs that were removed or renamed, so they don't show up as extra bidders
    for stale in out_dir.glob("*.json"):
        if stale.stem not in {p.stem for p in pdfs}:
            stale.unlink()
            print(f"REMOVED stale {stale.name} (no matching PDF)")

    failures = 0
    for pdf_path in pdfs:
        try:
            result = extract(pdf_path)
        except Exception as e:  # corrupt / password-protected PDFs
            failures += 1
            print(f"FAIL  {pdf_path.name}: {e}", file=sys.stderr)
            continue
        (out_dir / f"{pdf_path.stem}.json").write_text(json.dumps(result, indent=2))
        flag = "  [SCANNED - no text layer]" if result["scanned"] else ""
        print(f"OK    {pdf_path.name}: {len(result['pages'])} pages{flag}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
