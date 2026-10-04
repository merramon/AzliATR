"""Render PDF pages to PNG images so scanned pages can be read visually.

Usage:
    .venv/bin/python tools/render_pdf_pages.py "inputs/bidders/Bidder A.pdf" [--pages 14-17,20] [--scale 1.5] [--out .tmp/pages]

Writes .tmp/pages/<bidder>/p<N>.png. Omit --pages to render every page.
"""
import argparse
import sys
from pathlib import Path

import pypdfium2 as pdfium


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
    ap.add_argument("pdf")
    ap.add_argument("--pages", default="")
    ap.add_argument("--scale", type=float, default=1.5, help="1.0 = 72 dpi; 1.5 is readable, 2.5 for small print")
    ap.add_argument("--out", default=".tmp/pages")
    args = ap.parse_args()

    pdf_path = Path(args.pdf)
    pdf = pdfium.PdfDocument(pdf_path)
    out_dir = Path(args.out) / pdf_path.stem
    out_dir.mkdir(parents=True, exist_ok=True)
    for n in parse_pages(args.pages, len(pdf)):
        img = pdf[n - 1].render(scale=args.scale).to_pil()
        path = out_dir / f"p{n}.png"
        img.save(path)
        print(path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
