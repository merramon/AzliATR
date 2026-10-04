"""Find Current Assets, Current Liabilities and Inventory in extracted PDF text.

Usage:
    .venv/bin/python tools/parse_financials.py [--input .tmp/text] [--out .tmp/financials]

Reads the JSON produced by extract_pdf_text.py and writes one JSON per bidder:
    {"bidder", "source_pdf", "years": [...], "fields": {field: {"values": {year: number},
      "page", "line", "confidence": "high"|"low", "note"}}, "issues": [...]}

Matching is deliberately strict: a line only counts if, apart from the label, it contains
nothing but numbers (so "Non-current assets", "Net current assets", "Increase in inventories"
are ignored). Anything uncertain is marked confidence "low" so the evaluator flags it for review.
"""
import argparse
import json
import re
import sys
from pathlib import Path

FIELDS = {
    # English, then Malay (Bahasa Malaysia) labels
    "current_assets": [r"total\s+current\s+assets", r"current\s+assets",
                       r"jumlah\s+aset\s+semasa", r"aset\s+semasa"],
    "current_liabilities": [r"total\s+current\s+liabilities", r"current\s+liabilities",
                            r"jumlah\s+liabiliti\s+semasa", r"liabiliti\s+semasa"],
    "inventory": [r"inventories", r"inventory", r"stocks?(?:[\s-]+in[\s-]+trade)?", r"merchandise\s+inventor(?:y|ies)",
                  r"inventori", r"stok"],
}
# Words that may appear on a matching line besides the label and numbers
ALLOWED_EXTRA_WORDS = {"note", "notes", "rm", "usd", "sgd", "aed", "sar", "inr", "gbp", "eur", "idr", "php", "total"}

NUMBER_RE = re.compile(r"\(?-?\d[\d,]*(?:\.\d+)?\)?|(?<![\w])[-–—](?![\w])")
YEAR_RE = re.compile(r"\b(19\d{2}|20\d{2})\b")


def parse_number(tok: str) -> float:
    if tok in {"-", "–", "—"}:
        return 0.0
    neg = tok.startswith("(") and tok.endswith(")")
    val = float(tok.strip("()").replace(",", ""))
    return -val if neg else val


def match_label(line: str, patterns: list[str]):
    """Return (label_end_index) if the line starts with one of the labels and is otherwise numbers-only."""
    low = line.lower().strip()
    low = re.sub(r"^[\s\-•*·:]+", "", low)
    for pat in patterns:
        m = re.match(pat + r"\b", low)
        if not m:
            continue
        rest = low[m.end():]
        words = re.findall(r"[a-z]+", NUMBER_RE.sub(" ", rest))
        if all(w in ALLOWED_EXTRA_WORDS for w in words):
            return rest
    return None


def numbers_in(text: str) -> list[float]:
    toks = [t for t in NUMBER_RE.findall(text) if t.strip()]
    out = []
    for t in toks:
        try:
            out.append(parse_number(t))
        except ValueError:
            pass
    return out


def find_year_header(lines: list[str], upto: int) -> list[str]:
    """Nearest line above `upto` that is mostly year columns, e.g. '2024 2023' or 'Group 2024 2023 Company 2024 2023'."""
    for i in range(upto - 1, -1, -1):
        years = YEAR_RE.findall(lines[i])
        if not years:
            continue
        other = re.sub(YEAR_RE, "", lines[i])
        other_words = re.findall(r"[A-Za-z]+", other)
        if len(other_words) <= 6:
            return merge_split_header(lines, i, upto) or years
    return []


def merge_split_header(lines: list[str], i: int, upto: int) -> list[str]:
    """A tilted scan can split one year-header row over two lines, e.g. OCR rows
    '- 2024 2023 - -' and '- - - 2024 2023'. Overlay them by column position -> 2024 2023 2024 2023."""
    def cells(s):
        return [t for t in NUMBER_RE.findall(s) if t.strip()]
    base = cells(lines[i])
    for k in (i - 1, i + 1):
        if not (0 <= k < upto) or not YEAR_RE.search(lines[k]):
            continue
        other = cells(lines[k])
        if len(other) != len(base) or len(base) < 3:
            continue
        merged = [a if YEAR_RE.fullmatch(a) else b for a, b in zip(base, other)]
        if all(YEAR_RE.fullmatch(t) or t in "-–—" for t in merged):
            years = [t for t in merged if YEAR_RE.fullmatch(t)]
            if len(years) > len(YEAR_RE.findall(lines[i])):
                return years
    return []


def assign_columns(values: list[float], years: list[str], basis: str = "company"):
    """Map numeric columns to years. Extra leading numbers are treated as note references.

    When years repeat (e.g. 'Group 2024 2023 Company 2024 2023'), basis picks the first set
    ("group") or the later set ("company").
    """
    note = ""
    if years:
        if len(values) > len(years):
            note = f"dropped {len(values) - len(years)} leading number(s) as note refs"
            values = values[len(values) - len(years):]
        repeated = len(set(years)) < len(years)
        use_later = repeated and basis == "company"
        if repeated:
            which = "second set (Company)" if use_later else "first set (Group)"
            note = (note + "; " if note else "") + f"repeated year columns (Group/Company) - used {which}"
        mapped = {}
        for y, v in zip(years, values):
            if use_later:
                mapped[y] = v
            else:
                mapped.setdefault(y, v)
        low_conf = len(values) < len(years)
        return mapped, note, low_conf
    # No year header: assume first numeric column is the latest year
    if len(values) > 1 and float(values[0]).is_integer() and abs(values[0]) < 100:
        values = values[1:]
        note = "dropped leading small number as note ref"
    return {f"col{i + 1}": v for i, v in enumerate(values)}, (note + "; " if note else "") + "no year header found", True


def is_balance_sheet(text: str) -> bool:
    t = text.lower()
    return ("current assets" in t and "current liabilities" in t) or ("aset semasa" in t and "liabiliti semasa" in t)


# Lines that end a section when looking for its unlabeled subtotal (matched at line start)
SECTION_END = {
    "current_assets": ("total assets", "jumlah aset", "equity", "ekuiti", "current liabilities", "liabiliti semasa",
                       "non-current liabilities", "liabiliti bukan semasa", "capital and reserves", "modal dan rezab"),
    "current_liabilities": ("total liabilities", "jumlah liabiliti", "total equity", "equity and liabilities",
                            "ekuiti dan liabiliti", "non-current liabilities", "liabiliti bukan semasa"),
}


def find_subtotal_candidates(lines, start, field):
    """For layouts where 'Current assets' is a heading and the total is an unlabeled numbers-only line.

    Returns every numbers-only line in the section; the caller picks the one the items add up to.
    """
    found = []
    for j in range(start + 1, min(start + 40, len(lines))):
        s = lines[j].strip()
        if not s:
            continue
        if s.lower().startswith(SECTION_END[field]):
            break
        if not re.search(r"[A-Za-z]", s) and numbers_in(s):
            found.append(j)
    return found


HEADINGS = {
    "current_assets": {"current assets", "aset semasa"},
    "current_liabilities": {"current liabilities", "liabiliti semasa"},
}


def reconcile(lines, field, label_idx, total_idx, years, basis, total_values):
    """Check that the line items between the section heading and the total add up to the total.

    Returns (heading_idx, True/False), or (None, False) if no heading was found.
    """
    heading = None
    for h in range(label_idx, max(label_idx - 40, -1), -1):
        if re.sub(r"[^a-z ]", "", lines[h].lower()).strip() in HEADINGS[field]:
            heading = h
            break
    if heading is None:
        return None, False
    sums = {}
    for k in range(heading + 1, total_idx):
        vals = numbers_in(lines[k])
        if not vals or match_label(lines[k], FIELDS[field]) is not None:
            continue
        mapped, _, _ = assign_columns(vals, years, basis)
        for y, v in mapped.items():
            sums[y] = sums.get(y, 0.0) + v
    ok = bool(sums) and all(abs(sums.get(y, 0.0) - v) <= 1 for y, v in total_values.items())
    return heading, ok


COMPANY_SUFFIX = re.compile(r"\b(SDN\.?\s*BHD|BERHAD|BHD|PLT|LTD|LIMITED|CORPORATION|CORP|INC|LLC|PTE)\b\.?", re.I)
NOT_COMPANY = re.compile(r"&\s*CO\b|CHARTERED|AUDIT|ACCOUNTANT|BANK|\bTO THE MEMBER", re.I)


def detect_company(doc: dict) -> str:
    """Company name = the most frequent company-like line among page headers (it repeats on every page)."""
    counts = {}
    for p in doc["pages"]:
        for line in p["text"].splitlines()[:12]:
            line = line.strip()
            if len(line) < 70 and COMPANY_SUFFIX.search(line) and not NOT_COMPANY.search(line):
                key = re.sub(r"\s*\(CONT.D\)", "", re.sub(r"\s+", " ", line.upper())).strip(" .,")
                counts[key] = counts.get(key, 0) + 1
    if not counts:
        return ""
    name = max(counts, key=counts.get)
    name = re.sub(r"\bSDN\.?\s*BHD\b\.?", "SDN BHD", name)
    return name  # as printed (upper case) - title-casing would mangle acronyms like VSD


def parse_bidder(doc: dict, basis: str = "company") -> dict:
    result = {"bidder": doc["bidder"], "source_pdf": doc["source_pdf"], "company_name": detect_company(doc),
              "years": [], "fields": {}, "issues": []}
    if doc.get("scanned"):
        result["issues"].append("PDF has no text layer (scanned) - enter figures manually in inputs/overrides.csv")
        return result

    pages = sorted(doc["pages"], key=lambda p: (not is_balance_sheet(p["text"]), p["page"]))
    years_seen = []
    section_spans = {}  # field -> (page, heading_idx, total_idx) of a reconciled section
    for field, patterns in FIELDS.items():
        candidates = []
        for p in pages:
            lines = p["text"].splitlines()
            for i, line in enumerate(lines):
                rest = match_label(line, patterns)
                if rest is None:
                    continue
                vals = numbers_in(rest)
                years = find_year_header(lines, i)
                is_ocr = bool(p.get("ocr"))
                if vals:
                    options = [(i, vals, "")]
                elif field == "inventory":
                    continue
                else:
                    options = [(j, numbers_in(lines[j]), "unlabeled subtotal under heading")
                               for j in find_subtotal_candidates(lines, i, field)]
                    if not options:
                        continue
                chosen = None
                for total_idx, opt_vals, opt_note in options:
                    mapped, col_note, col_low = assign_columns(opt_vals, years, basis)
                    note = "; ".join(x for x in [opt_note, col_note] if x)
                    low = bool(opt_note) or col_low or is_ocr or not is_balance_sheet(p["text"])
                    reconciled = False
                    if field in HEADINGS and years:
                        heading, reconciled = reconcile(lines, field, i, total_idx, years, basis, mapped)
                        if reconciled:
                            low = False
                            note = "; ".join(x for x in [note, "line items add up to total"] if x)
                            section_spans[field] = (p["page"], heading, total_idx)
                        elif heading is not None:
                            low = True
                            note = "; ".join(x for x in [note, "line items do NOT add up to total"] if x)
                    elif field == "inventory":
                        span = section_spans.get("current_assets")
                        if span and span[0] == p["page"] and span[1] < i < span[2]:
                            low = False  # inside a current-assets section that reconciled
                    if chosen is None or reconciled:
                        chosen = (total_idx, mapped, note, low, reconciled)
                    if reconciled:
                        break
                total_idx, mapped, note, low, reconciled = chosen
                src_line = line.strip() if total_idx == i else f"{line.strip()} ... {lines[total_idx].strip()}"
                if is_ocr:
                    note = "; ".join(x for x in ["OCR", note] if x)
                candidates.append({
                    "values": mapped, "page": p["page"], "line": src_line,
                    "confidence": "low" if low else "high", "note": note, "reconciled": reconciled,
                })
                years_seen.extend(y for y in mapped if not y.startswith("col"))
        if not candidates:
            if field == "inventory":
                result["issues"].append("inventory not found - quick ratio assumes 0 inventory")
            else:
                result["issues"].append(f"{field} not found")
            continue
        best = next((c for c in candidates if c["reconciled"]), candidates[0])
        others = {json.dumps(c["values"], sort_keys=True) for c in candidates[1:]}
        others.discard(json.dumps(best["values"], sort_keys=True))
        if others:
            best["note"] = "; ".join(x for x in [best["note"], f"{len(others)} other differing match(es) - used first"] if x)
        result["fields"][field] = best

    result["years"] = sorted(set(years_seen), reverse=True)
    return result


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--input", default=".tmp/text")
    ap.add_argument("--out", default=".tmp/financials")
    ap.add_argument("--basis", choices=["company", "group"],
                    help="Which column to use when Group and Company are both shown (default: config/asset_test.json)")
    args = ap.parse_args()
    basis = args.basis or json.loads(Path("config/asset_test.json").read_text()).get("statement_basis", "company")

    files = sorted(Path(args.input).glob("*.json"))
    if not files:
        print(f"No extracted text in {args.input} - run extract_pdf_text.py first", file=sys.stderr)
        return 1
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    for stale in out_dir.glob("*.json"):
        if stale.stem not in {f.stem for f in files}:
            stale.unlink()
    for f in files:
        res = parse_bidder(json.loads(f.read_text()), basis)
        (out_dir / f.name).write_text(json.dumps(res, indent=2))
        found = ", ".join(f"{k}({v['confidence']})" for k, v in res["fields"].items()) or "nothing"
        print(f"{res['bidder']}: found {found}; years {res['years'] or '?'}")
        for issue in res["issues"]:
            print(f"    ! {issue}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
