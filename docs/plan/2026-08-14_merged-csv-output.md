# Plan: Merge all tables into one CSV

**Date:** 2026-08-14
**Status:** Proposed — not started
**Goal:** Replace the per-document Excel workbook with a single merged CSV,
matching the post-processing in `data_extraction_automation`.

---

## 1. What `data_extraction_automation` actually does

Its post-processing lives in two files and runs after every table is parsed.

### `app/merge_tables.py` — the normalisation vocabulary

| Piece | Purpose |
| --- | --- |
| `CANONICAL` | maps header spellings onto one canonical name (`"midascode"`, `"midas code"` → `Midas Code`; `"barcode"`, `"eanbarcode"` → `EAN Barcode`) |
| `normalize_col()` | collapse whitespace → split camelCase → look up `CANONICAL` → else Title Case |
| `REQUIRED_COLS` | `{"Midas Code", "Consumer Deal"}` — the gate for whether a table is a product table at all |
| `_fix_split_case_size()` | repairs the OCR splitting `Case Size` into separate `Size` + `Case` columns, moving Midas-looking values back into `Midas Code` |
| `merge()` | `pd.concat` of qualifying frames, then **drops rows with a blank `Midas Code`** (section-header rows) |

### `app/extraction_common.py` — the per-table gate

`normalize_table()` runs on each table: stringify → map NA-ish strings
(`"nan"`, `"None"`, `"<NA>"`, `""`) to `pd.NA` → drop `Unnamed:`/numeric/blank
columns → `normalize_col()` every header → drop duplicate headers → repair
split Case Size → **return `None` unless both required columns are present**.
Qualifying frames get a `source_table` column so every row is traceable.

### The loop (`app/ppstructure_extractor.py`)

For each table found:
1. **always** save it individually to `individual/<doc>-table-N.csv` (+ `.html`)
2. attempt `normalize_table()`; if it qualifies, add to the merge list
3. after each chunk, rewrite the merged CSV — so progress survives a crash

Output: one `<doc>-merged.csv` plus an `individual/` folder of every raw table.

---

## 2. Does this apply to our documents? — Yes, verified

Run against the real OCR output already on the deployed box:

| Document | Pages | Tables found | Qualified | Merged rows |
| --- | --- | --- | --- | --- |
| BUD NP10 Trading Pack | 68 | 55 | 40 | **703** |
| test1.pdf | 5 | 6 | 4 | **74** |

Headers PaddleOCR-VL produces:

```
BUD NP10 : Shelf, Product Description, Midas Code, EAN Barcode,
           Prom WSP, Std RSP, Consumer Deal, Promo POR
test1    : Image, Product Description, Case Size, Midas Code, EAN Barcode,
           Prom WSP, Std RSP, Consumer Deal, Promo POR, Leaflet
```

Both carry `Midas Code` + `Consumer Deal`, so the existing gate works unchanged.
The tables it rejects are the right ones — e.g. test1 page 1's 1×2 banner
tables (`EDLP 1`, `NP1 - NP3`).

**Merged cells are already handled.** `Consumer Deal` is a vertically merged
cell spanning several product rows, and the merged output has **0 blank
`Consumer Deal` values** in 703 rows — `pd.read_html` replicates rowspan into
every covered row. The `MERGED_CELL_FIX_PLAN.md` problem from the other repo
does not reproduce here.

---

## 3. Two real problems the probe exposed

These are the reason this is not a straight copy-paste.

### 3a. Header variants that `CANONICAL` does not cover

Raw headers seen across BUD NP10:

```
CASE SIZE, Case Size, PACK SIZE, pack size, POR, POR%, Prom POR, Promo POR
```

`normalize_col` lower-cases before lookup, so `CASE SIZE` → `Case Size` ✅.
But `PACK SIZE`, `POR`, `POR%`, and `Prom POR` are **not** in `CANONICAL`, so
they each become their own column. That is why the merged frame came out with
**16 columns** including a junk `Prom Por` sitting beside the real `Promo POR`,
plus OCR debris (`Sin`, `Age`).

**Fix:** extend `CANONICAL` with the observed variants, and — more
importantly — **log every header that falls through to the Title-Case
fallback**, so new variants surface instead of silently fragmenting a column.

### 3b. Rows whose `Midas Code` is not a Midas code

| Document | Rows | Bad `Midas Code` |
| --- | --- | --- |
| BUD NP10 | 703 | **20** |
| test1 | 74 | **6** |

Two distinct causes:

* **Category banner rows** — `FRESH`, `GROCERY`, `BWS`, `IMPLUSE` (sic),
  `SOFT DRINKS`. Noise; should not be product rows.
* **Column-shifted rows** — `15 x 90G`, `10 x 4 x 30G`, `30 x 60G`. These are
  *Case Size* values that landed in the `Midas Code` column, i.e. the row is
  misaligned by one column. **This is real data being corrupted, not noise.**

`merge()` only drops *blank* Midas codes, so all 26 of these survive into the
CSV today.

**Fix:** validate against `MIDAS_PATTERN` (`^M\d+$`) and route failures to a
sibling `<doc>-rejected.csv` with a reason column — **not** a silent drop.
Silently dropping the shifted rows would hide genuine data loss; the reject
file makes the miss auditable and countable.

---

## 4. Pipeline

```
                         PDF
                          │
                    split_pdf.py                     (unchanged)
                          │
                   page_NNN.png
                          │
                  process_pages.py                   (unchanged)
                          │
                 page_NNN_res.json
                          │
              utils/table_reader.py                  (unchanged)
                          │
                   [TableRecord] × N
                          │
        ┌─────────────────┼──────────────────┐
        ▼                 ▼
  individual CSVs   normalize + gate
  every table       Midas + Consumer
  written raw       Deal required
                          │
                    ┌─────┴─────┐
                 passes       fails
                    │            │
                    ▼            ▼
              merge + row   skipped
              validation    (still on disk
                    │        individually)
              ┌─────┴─────┐
              ▼           ▼
       <doc>.csv   <doc>-rejected.csv
       (merged)    (bad rows + reason)
```

**New module boundary:** `table_reader.py` keeps producing generic
`TableRecord`s and stays schema-agnostic. All trading-pack knowledge lands in
one new module, so a different document type only needs a different normaliser.

---

## 5. Changes required

### New

| File | Contents |
| --- | --- |
| `utils/table_normalizer.py` | `CANONICAL`, `normalize_col()`, `REQUIRED_COLS`, `MIDAS_PATTERN`, `_fix_split_case_size()`, `normalize_table()`, `merge()`, `validate_rows()` — port of `merge_tables.py` + `extraction_common.py`, with §3a variants added |
| `scripts/json_to_csv.py` | JSON folder → `<doc>.csv`, `<doc>-rejected.csv`, `individual/*.csv`; replaces `json_to_excel.py` |
| `tests/test_table_normalizer.py` | column canonicalisation, the required-cols gate, banner detection, repair-and-verify (incl. that a repair which lowers the validator score is refused), split Case Size |

### Removed

`scripts/json_to_excel.py`, `utils/excel_utils.py`, `tests/test_json_to_excel.py`, and the `openpyxl` dependency.

### Modified

| File | Change |
| --- | --- |
| `scripts/extract_document.py` | STEP 3 calls `json_to_csv` instead of `json_to_excel`; returns the merged CSV path |
| `app_db/database.py` | `extraction_records` += `merged_csv_path`, `tables_total`, `tables_merged`, `rows_extracted`, `rows_rejected`; `extracted_tables` += `qualified` flag |
| `services/extraction_service.py` | record merge stats; return merged CSV path |
| `api/records_routes.py` | `/api/records/{id}/preview` → merged CSV (per-table preview stays); `/download/{id}` → merged CSV; `/api/compare` targets the merged CSV, `table_index` becomes optional |
| `api/routes.py` | `POST /extract/excel` becomes `POST /extract/csv`, returning the merged CSV |
| `frontend/index.html` | results show merged preview + `tables_merged/tables_total`, `rows_extracted`, `rows_rejected`; compare runs against the merged CSV; table list demoted to a secondary "all detected tables" panel |
| `README.md` | document the merge, the gate, and the reject file |

---

## 6. Procedure

Each step is independently verifiable against the OCR JSON already on disk —
**no re-OCR needed**, which makes this fast to iterate on.

1. **`utils/table_normalizer.py` + unit tests.** Pure functions, no I/O.
2. **Replay against real output.** Run over `storage/json/BUD NP10 …` and
   `2b7cf9ac_test1` and assert: 703 and 74 rows, and that the 26 bad rows now
   land in the reject file. This is the regression baseline.
3. **Extend `CANONICAL`** for `PACK SIZE`/`POR`/`POR%`/`Prom POR`; re-run step 2
   and confirm the merged frame drops from 16 columns to the expected ~11.
4. **`scripts/json_to_csv.py`** + wire into `extract_document.py`.
5. **DB columns + `extraction_service`** stats.
6. **API**: preview/download/compare.
7. **Frontend**: merged-first results view.
8. **Deploy and verify on GPU** with BUD NP10 end to end.

---

## 7. Decisions taken (2026-08-14)

1. **Excel is dropped.** CSV is the only output. `json_to_excel.py` and the
   `/extract/excel` endpoint go; `/extract/csv` replaces it. The per-table
   metadata the workbook carried (page, heading, document title) survives in
   the `extracted_tables` DB rows and the `source_table` column.

2. **UI becomes merged-first** — accepted reversal of the earlier
   "generic multi-table" decision, which was made before we knew these were
   trading packs. Comparison targets the merged CSV (a ground truth file is
   per-document, not per-table). The per-table list stays as a debug panel.

3. **Column-shifted rows: attempt repair** — but see §7a, because the obvious
   implementation corrupts data.

### 7a. Repair must be verified, not assumed

Anchoring on the cell matching `^M\d+$` and shifting the whole row classifies
cleanly — of 26 bad rows, 19 are banners, 7 are shifted, 0 ambiguous — but the
resulting repair is **only sometimes correct**:

```
test1  before: ProdDesc=<NA>  CaseSize='AERO GIANT MILK'  Midas='15 x 90G'  EAN='M304095'
       after : ProdDesc='AERO GIANT MILK'  CaseSize='15 x 90G'  Midas='M304095'      CORRECT

BUD    before: ProdDesc='CARLSBERG PILSNER PM525'  CaseSize='[F87]'  Midas='6 x 4 x 440ML'  Sin='M302447'
       after : Image='CARLSBERG PILSNER PM525'  ProdDesc='[F87]'  Midas='M302447'    WRONG
```

In the BUD rows the displacement does not start at column 0 — a spurious
`[F87]` cell is inserted *mid-row*, so `Product Description` is already in the
right place. Shifting the whole row fixes `Midas Code` while pushing the
product name into `Image`. A naive whole-row shift would therefore **silently
corrupt 3 rows in order to fix 4**.

**Design: repair, then verify; accept only if strictly better.**

Per-column validators (regex/type):

| Column | Accepts |
| --- | --- |
| `Midas Code` | `^M\d+$` |
| `Case Size` | `N x N … G/ML/KG/L` |
| `Prom WSP`, `Std RSP` | currency |
| `Promo POR` | percentage |
| `EAN Barcode` | digits |
| `Image` | empty or a small integer |

Procedure for a row failing the Midas check:

1. All non-NA cells identical → **banner** → drop (counted, not written).
2. Otherwise generate candidate repairs: whole-row shift by `k`, and
   *segment* shifts that delete one spurious cell at position `p ≤ anchor` and
   left-shift only `p..anchor`.
3. Score each candidate by how many columns pass their validator.
4. Accept the best candidate **only if it scores strictly higher than the
   original row**; write it with `repaired = True` so every touched row is
   auditable. Otherwise send the row to `<doc>-rejected.csv` untouched.

This recovers the test1 rows, and — because the segment-shift candidate keeps
`Product Description` in place — should also recover the BUD rows. Whatever it
cannot verify is rejected rather than guessed at.

**Regression baseline for the implementation:** 703 rows (BUD NP10) and 74
(test1); 19 banners dropped; 7 rows repaired or rejected with *zero* rows
where a repair lowers the validator score.

---

## 8. Risks

* **Silent data loss** is the main one. Every row that leaves the merge must be
  counted and written to the reject file; the UI should surface
  `rows_rejected` next to `rows_extracted`, not hide it.
* **The gate is schema-specific.** Any document without `Midas Code` +
  `Consumer Deal` merges to an empty CSV. The UI must say "0 of N tables
  qualified" rather than appearing to succeed with nothing.
* **`CANONICAL` drift** — a new OCR spelling silently forks a column. The
  fallback-logging in §3a is what keeps this visible.
* Unbounded `storage/` growth and the missing API auth are unchanged and out of
  scope here.
