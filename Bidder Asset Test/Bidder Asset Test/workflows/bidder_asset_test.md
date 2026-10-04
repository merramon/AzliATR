# Bidder Asset Test Evaluation

## Objective
Evaluate each bidder's submitted financial report (PDF) and produce the asset test ratio results — **Current Ratio** and **Quick Ratio** — with a PASS / FAIL / REVIEW result per bidder, in an Excel file.

## Required inputs
- Bidder financial reports as PDFs in `inputs/bidders/`, **one PDF per bidder**, named after the bidder (the file name becomes the bidder name in the results, e.g. `Alpha Construction Sdn Bhd.pdf`).
- Pass thresholds and Group/Company basis live in `config/asset_test.json` (currently: Current ≥ 1.0 AND Quick ≥ 1.0, basis = company — confirmed by user 2026-10-01). The evaluator refuses to run if a minimum is null.
- Optional: `inputs/overrides.csv` for manually confirmed figures (see step 4).

## Two ways to run it
- **Web app (for users)** — double-click `Asset Test.command` in the project folder (or `.venv/bin/python tools/asset_test_app.py`). A page opens in the browser at `http://127.0.0.1:8765`: drop in PDFs → **Run asset test** → results table, source-page viewer, Excel download, print/save as PDF. Runs only on this computer; nothing is uploaded to the internet. Keep the Terminal window open while using it.
  - Each batch is a separate job in `.tmp/web/<job id>/` and uses the same tools as the steps below; `inputs/overrides.csv` is **not** used (uploads are new bidders).
  - REVIEW rows can be corrected on the page after checking the source page; the result is recalculated in the page. Those manual corrections are **not** written into the downloaded Excel — use Print / Save as PDF to keep them.
  - One job runs at a time (OCR already uses every CPU core); a second batch waits.
  - **Labeling (required before Run)**: after adding PDFs the user gives each report a bidder label (Bidder A, B, … — auto-suggested in the order added, editable) and types the company name (e.g. ABE Daya Bina Enterprise Sdn Bhd → Bidder A). Run stays disabled until every report has a company name and each label is used once. The label is the bidder name in results; the company name is shown under it and in Excel as "Bidder A – Company".
  - **Joint ventures**: when two (or more) reports get the same bidder letter they form one JV bid. Members are tagged A1, A2, … in list order (uploaded as "Bidder A1", "Bidder A2"); their company names are optional on the page and are read from the reports (`parse_financials.py` → `company_name`: the most frequent company-like page-header line). `evaluate_ratios.py --groups` combines the members' **latest-year** figures: Current = ΣCA ÷ ΣCL, Quick = (ΣCA − ΣInventory) ÷ ΣCL. If any member is REVIEW the JV is REVIEW; if members' year-ends differ it is noted. Member ratios are shown for information only.
- **On another computer (e.g. a friend's laptop)** — run `.venv/bin/python tools/build_portable.py` → `outputs/Bidder Asset Test.zip` (app only: no bidder PDFs, results, .tmp, .venv or .env). Copy, unzip, double-click `Asset Test.command` (Mac) or `Asset Test (Windows).bat`. First run installs uv + Python 3.14 + packages + OCR models into the folder (internet, ~5 min, no admin); later runs work offline. Plain-language instructions are in `HOW TO USE.txt`.
- **Steps below (agent / CLI)** — for files in `inputs/bidders/`, with the overrides file and the full audit trail.

## Formulas
- Current Ratio = Current Assets ÷ Current Liabilities
- Quick Ratio = (Current Assets − Inventory) ÷ Current Liabilities
- Units (RM, RM'000, USD…) don't matter — ratios are unitless, as long as both figures come from the same statement.

## Steps

1. **Extract text** — `.venv/bin/python tools/extract_pdf_text.py`
   - Output: `.tmp/text/<bidder>.json`
   - PDFs reported as `[SCANNED - no text layer]` (and PDFs where only some pages have text) go through step 1b.

1b. **OCR scanned pages** — `.venv/bin/python tools/ocr_pdf_pages.py` (local RapidOCR — free, no API)
   - OCRs every page that has no text layer and writes the text back into `.tmp/text/<bidder>.json` (pages marked `"ocr": true`).
   - Rebuilds table rows and places figures into columns by position, so empty cells become `-` and year columns don't shift.
   - Speed: ~3–5 s per page (a 93-page report takes several minutes). Results are cached in `.tmp/ocr/<file hash>/`, so re-runs are instant — even if the file was renamed or uploaded via the web app. To save time on long reports, `--bidder "<name>" --pages 10-20` limits the run.
   - Any figure the parser takes from an OCR'd page is **only trusted if its section reconciles** (see step 2); otherwise it's REVIEW.

2. **Parse figures** — `.venv/bin/python tools/parse_financials.py`
   - Output: `.tmp/financials/<bidder>.json` with each figure's value per year, source page/line, and confidence.
   - Looks for lines labelled "Total current assets" / "Current assets", "Total current liabilities" / "Current liabilities", "Inventories" / "Inventory" / "Stocks". Balance-sheet pages are searched first.
   - **Reconciliation check**: for current assets and current liabilities, the line items between the section heading and the total are summed per year column. If they add up, the figure is high confidence (even when OCR'd); if not, it's marked low confidence with "line items do NOT add up" → REVIEW. Inventory is trusted when it sits inside a reconciled current-assets section.

3. **Evaluate** — `.venv/bin/python tools/evaluate_ratios.py` (CLI `--current-min/--quick-min` override the config)
   - Output: `outputs/asset_test_results.xlsx`
     - *Summary* sheet: latest year per bidder, ratios, result, notes.
     - *All Years* sheet: every year found, plus the source page and line for every figure (audit trail).

4. **Resolve REVIEW items (agent's job)** — for every bidder marked REVIEW:
   - Text PDFs: open `.tmp/text/<bidder>.json` at the page cited and verify the figure.
   - OCR'd pages where the reconciliation failed, or the balance sheet wasn't found — verify visually:
     1. Find the balance sheet: render thumbnails of the likely range (it usually follows the auditors' report, ~p.10–20) and look for "Statement of Financial Position" / "Penyata Kedudukan Kewangan":
        `.venv/bin/python tools/render_pdf_pages.py "inputs/bidders/<bidder>.pdf" --pages 10-20 --scale 0.45`
        (a contact sheet of many pages in one image saves time on long reports).
     2. Render that page at `--scale 2.5`–`3` and crop into halves so small print is legible.
     3. Read the current assets / current liabilities subtotals and inventories for every year column.
     4. **Cross-check before recording**: line items must add up to each subtotal, and Total liabilities + Equity must equal Total assets. If a column doesn't reconcile, re-read at higher scale; if still unclear, ask the user.
     5. With Group and Company columns, record the column set by `statement_basis` in the config and put the other basis's figure in the `reason` for reference.
   - Add the confirmed figure to `inputs/overrides.csv`:
     ```
     bidder,year,field,value,reason
     Gamma Trading,2024,current_assets,"1,200,000",scanned p.4 balance sheet
     ```
     `bidder` must match the PDF file name (without `.pdf`); `field` is `current_assets`, `current_liabilities` or `inventory`.
   - If the figure can't be determined (scanned PDF with no readable image, missing balance sheet), ask the user rather than guessing.
   - Re-run step 3 only (steps 1–2 don't need repeating for overrides).

5. **Report** — give the user a short summary: each bidder's result and ratios, what was overridden and why, and anything still unresolved.

## Edge cases & known behaviour
- **Scanned PDFs**: handled by OCR (step 1b). Only if OCR'd figures fail reconciliation do they go to REVIEW / overrides.
- **Unlabeled subtotals**: when "Current assets" is only a heading and the total is a bare number line, the parser picks the numbers-only line the items add up to; high confidence only if it reconciles.
- **Group vs Company columns**: when years repeat (e.g. `Group 2024 2023 Company 2024 2023`), the set chosen by `statement_basis` in `config/asset_test.json` is used (default **company** — the bidding entity itself; `--basis group` to switch) and noted.
- **Note-reference columns**: extra leading numbers on a line (e.g. `Inventories 5 300,000 250,000`) are dropped as note refs.
- **No inventory line** (common for service companies): inventory assumed 0, so Quick = Current ratio; noted in results.
- **No year header**: columns labelled `col1, col2…` and marked low confidence; first column assumed latest.
- **Multiple matches** (e.g. restated figures, or notes pages): first balance-sheet match used, noted in results — check if it matters.
- **Current liabilities ≤ 0**: ratio not computed → REVIEW.
- Negative numbers in brackets `(100,000)` and dashes `-` (= 0) are handled.

## Learnings log
_Add rate limits, layout quirks, and fixes discovered on real bidder files here._
- 2026-09-30: Tools built and tested on synthetic PDFs (note column, Group/Company layout, scanned).
- 2026-10-01: First real run (Bidder A, BIDDER E, BIDDER IY). **All three financial statements were scanned images** — two PDFs had no text layer at all; Bidder A had text only on the auditors' report pages. Text parsing found nothing; all figures came from visual reading (step 4) with cross-checks. Expect this to be the norm for tender submissions.
- Malaysian reports may be in Malay: Aset Semasa = current assets, Liabiliti Semasa = current liabilities, Jumlah = total, Inventori/Stok = inventory, Penyata Kedudukan Kewangan = balance sheet. Parser now recognises these labels.
- The Read tool can't open PDFs directly on this machine (no poppler); use `tools/render_pdf_pages.py` (pypdfium2) instead.
- Scanned subtotal lines can sit a row off from their labels; rely on subtotals that reconcile, not on visual row alignment.
- 2026-10-01: Added local OCR (`tools/ocr_pdf_pages.py`, RapidOCR + onnxruntime, pip-only, works on Python 3.14). Re-ran all three bidders with overrides disabled: OCR + parser reproduced every hand-verified figure (Company and Group basis) and identical ratios. Full OCR of 151 pages took ~4 min.
- OCR quirks handled in the tools: printed dashes for zero are not detected (fixed by placing numbers into columns by x-position); tilted scans merge adjacent rows (second number in the same column goes to an extra line) and split the year header over two lines (parser overlays them by column position).
- Parser bug fixed: the unlabeled-subtotal search used to stop at any line containing "liabilities" (e.g. "Contract liabilities"); now it stops only at real section boundaries and picks the numbers-only line the items add up to.
- The manual overrides from the first run are still in `inputs/overrides.csv` (they take precedence). They match the OCR figures exactly; clear them for a new tender.
- 2026-10-01 (run 2): Bidder F added (Rino Technology, scanned) — OCR + parser reconciled on first try, PASS. "BIDDER IY.pdf" had been renamed "Bidder IY.pdf": stale `.tmp` results would have shown IY twice. extract_pdf_text.py / parse_financials.py now delete results for PDFs that no longer exist. (OCR cache is now keyed by file content, so renames no longer trigger re-OCR.)
- Bidder F lists deferred tax liabilities under current liabilities — taken as reported (makes the ratio slightly more conservative).
- 2026-10-01: Added the local web app (`tools/asset_test_app.py` + `tools/web/asset_test.html`, Python standard library only — no new dependencies). Tested end-to-end in Chrome with all four bidders (same results as CLI), the REVIEW correction flow, dark mode and phone width. `evaluate_ratios.py` gained `--json`.
- 2026-10-02: Added bidder labeling to the web app and portable packaging. Tested in Chrome (label defaults, duplicate-label and missing-name blocking, results/Excel show "Bidder A – Company", phone width). Bug caught in testing: re-rendering the list on blur wiped the box being typed in — validation now updates in place. Portable zip tested on Mac by unzipping into an empty folder: first-run setup took ~1.5 min (uv already cached on this Mac; a truly new machine downloads more) and a scanned report was OCR'd and evaluated correctly. **The Windows launcher has not been tested on a real Windows PC yet.** Unsigned launchers trigger Gatekeeper / SmartScreen warnings — workarounds are in HOW TO USE.txt.
- 2026-10-02: Added joint-venture bids. Company-name detection checked on all four real reports (100% correct, kept upper case as printed — title-casing mangled acronyms like VSD). Tested in Chrome: JV of Bidder A + Bidder IY gave combined 1.90 / 1.76 matching hand calculation; JV with an unreconciled member showed REVIEW, and correcting that member recalculated the combined result correctly. CLI runs without `--groups` are unchanged.
- 2026-10-02: Company names are now read automatically for **every** report (not only JV partners); the name box is optional and a typed name overrides the detected one. Results table and Excel have separate **Bidder** and **Company** columns (Excel: JV row "Bidder A (JV)" | "COMPANY 1 + COMPANY 2", members "A1" | "COMPANY 1"). This also applies to the CLI Excel (names come from `company_name` in the parsed JSON). Tested in Chrome with no names typed: all four names detected correctly, typed name kept, Excel columns verified.

