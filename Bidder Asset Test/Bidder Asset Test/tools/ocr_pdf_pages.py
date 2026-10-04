"""OCR scanned pages locally (RapidOCR, no API / no cost) and add the text to extracted-text JSON.

Usage:
    .venv/bin/python tools/ocr_pdf_pages.py [--text .tmp/text] [--bidder "BIDDER E"] [--pages 10-20] [--scale 2.5]

Run after extract_pdf_text.py. For every page with no text layer, renders the page, OCRs it,
and rebuilds table rows so the output looks like a text PDF:
  - words on the same visual row are joined into one line (with a small vertical tolerance)
  - numbers are placed into the page's numeric columns by horizontal position; an empty cell
    becomes "-" so year columns never shift (OCR does not see the dashes printed for zero)
OCR'd pages are marked "ocr": true. Per-page OCR results are cached in .tmp/ocr/<file hash>/ so re-runs (even after renaming) are fast.
"""
import argparse
import hashlib
import json
import re
import statistics
import sys
from pathlib import Path

import pypdfium2 as pdfium

NUM_BOX_RE = re.compile(r"\(?-?\d[\d,]*(?:\.\d+)?\)?|[-–—]")
CACHE_DIR = Path(".tmp/ocr")


def normalize_number(t: str) -> str:
    t = t.replace(" ", "")
    # OCR sometimes reads thousands separators as dots: 4.310.978 -> 4,310,978
    if re.fullmatch(r"\(?\d{1,3}([.,]\d{3})+\)?", t):
        t = t.replace(".", ",")
    return t


def ocr_page(engine, pdf, n: int, scale: float, cache: Path) -> list[dict]:
    if cache.exists():
        return json.loads(cache.read_text())
    img = pdf[n - 1].render(scale=scale).to_pil()
    res = engine(img)
    boxes = []
    if res.boxes is not None:
        for b, t, s in zip(res.boxes, res.txts, res.scores):
            xs, ys = [p[0] for p in b], [p[1] for p in b]
            boxes.append({"x0": float(min(xs)), "x1": float(max(xs)), "y0": float(min(ys)), "y1": float(max(ys)), "text": t, "score": float(s)})
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(boxes))
    return boxes


def cluster(values: list[float], gap: float) -> list[tuple[float, float]]:
    out = []
    for v in sorted(values):
        if out and v - out[-1][1] <= gap:
            out[-1][1] = v
            out[-1][2] += 1
        else:
            out.append([v, v, 1])
    return [(lo, hi) for lo, hi, count in out if count >= 3]


def boxes_to_text(boxes: list[dict], page_width: float) -> str:
    if not boxes:
        return ""
    for b in boxes:
        b["cy"] = (b["y0"] + b["y1"]) / 2
        nt = normalize_number(b["text"].strip())
        b["num"] = nt if NUM_BOX_RE.fullmatch(nt) else None
    tol = 0.5 * statistics.median(b["y1"] - b["y0"] for b in boxes)

    rows = []
    for b in sorted(boxes, key=lambda b: b["cy"]):
        if rows and b["cy"] - statistics.mean(x["cy"] for x in rows[-1]) <= tol:
            rows[-1].append(b)
        else:
            rows.append([b])

    # numeric columns = clusters of right edges (figures are right-aligned)
    col_gap = 0.03 * page_width
    columns = cluster([b["x1"] for b in boxes if b["num"]], col_gap)

    lines = []
    for row in rows:
        row.sort(key=lambda b: b["x0"])
        words = [b["text"] for b in row if not b["num"]]
        nums = [b for b in row if b["num"]]
        in_cols = [(ci, b) for b in nums for ci, (lo, hi) in enumerate(columns) if lo - col_gap <= b["x1"] <= hi + col_gap]
        if in_cols:
            # a skewed scan can merge two table rows; a second number in the same column goes to an extra line
            grid = [["-"] * len(columns)]
            for ci, b in sorted(in_cols, key=lambda cb: cb[1]["cy"]):
                r = next((r for r in grid if r[ci] == "-"), None)
                if r is None:
                    r = ["-"] * len(columns)
                    grid.append(r)
                r[ci] = b["num"]
            lines.append(" ".join(words + grid[0]))
            lines.extend(" ".join(r) for r in grid[1:])
        else:
            lines.append(" ".join(b["text"] for b in row))
    return "\n".join(lines)


def parse_pages(spec: str, total: int) -> list[int]:
    if not spec:
        return list(range(1, total + 1))
    pages = []
    for part in spec.split(","):
        a, _, b = part.strip().partition("-")
        pages.extend(range(int(a), int(b or a) + 1))
    return [p for p in pages if 1 <= p <= total]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--text", default=".tmp/text")
    ap.add_argument("--bidder", help="only this bidder (file name without .pdf)")
    ap.add_argument("--pages", default="", help="only these pages, e.g. 10-20")
    ap.add_argument("--scale", type=float, default=2.5)
    args = ap.parse_args()

    files = sorted(Path(args.text).glob("*.json"))
    if args.bidder:
        files = [f for f in files if f.stem == args.bidder]
    if not files:
        print(f"No extracted text in {args.text} - run extract_pdf_text.py first", file=sys.stderr)
        return 1

    import logging
    from rapidocr import RapidOCR
    logging.getLogger("RapidOCR").setLevel(logging.WARNING)
    engine = RapidOCR()

    for f in files:
        doc = json.loads(f.read_text())
        pdf = pdfium.PdfDocument(doc["source_pdf"])
        # cache by file content, so a renamed PDF reuses its OCR and two different PDFs never collide
        digest = hashlib.sha1(Path(doc["source_pdf"]).read_bytes()).hexdigest()[:12]
        wanted = set(parse_pages(args.pages, len(pdf)))
        done = 0
        for page in doc["pages"]:
            n = page["page"]
            if n not in wanted or (page["text"].strip() and not page.get("ocr")):
                continue
            boxes = ocr_page(engine, pdf, n, args.scale, CACHE_DIR / digest / f"p{n}_s{args.scale}.json")
            width = pdf[n - 1].get_width() * args.scale
            page["text"] = boxes_to_text(boxes, width)
            page["ocr"] = True
            done += 1
        doc["scanned"] = not any(p["text"].strip() for p in doc["pages"])
        f.write_text(json.dumps(doc, indent=2))
        print(f"OK    {doc['bidder']}: OCR'd {done} page(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
