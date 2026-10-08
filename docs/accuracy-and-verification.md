# Accuracy and verification

## The principle

A row reaches `<doc>.csv` only if **every field** is a valid member of the
document grammar (`config/document_profile.py`). Everything else goes to
`<doc>-rejected.csv` (the needs-review list) with the exact reason. Nothing is
dropped silently and nothing is guessed.

## Pipeline

    PDF -> page PNGs -> PaddleOCR-VL JSON  (pass 1, as before)
                     -> geometry pass       (scripts/geometry_pass.py)
                     -> CSV                 (scripts/json_to_csv.py)

The geometry pass rebuilds each table from the PDF itself:

| Source | When | Trust |
|---|---|---|
| PDF text layer | digital PDFs | exact characters - OCR is not involved |
| classic OCR words + VLM crop re-read | scanned PDFs | accepted only if both reads agree (or exactly one is valid) |
| page-level VLM table | regions geometry could not rebuild | strict validation, plus audit cross-check |

## Output files

* `<doc>.csv` - validated rows only
* `<doc>-rejected.csv` - needs review: `reason`, `issue_codes`
* `<doc>-audit.csv` - every cell where the page-level OCR disagreed with the PDF
  geometry, and every completeness warning
* `<doc>/individual/*.csv` - raw OCR tables

`midas_codes_on_pages` (in `CsvResult`) must equal rows kept + needs review.

## Adapting to a new deal wording

Unknown deal text is sent to review, never guessed. Add a regex to
`deal_patterns` in `config/document_profile.py`.

## Limits (be aware)

* Digital PDFs with a real text layer: text fields are exact.
* Scans: accuracy depends on OCR; disagreements go to review instead of the CSV.
* Without drawn borders (or on a scan the line detector cannot read), merged
  Consumer Deal groups cannot be proven; affected rows go to review.
* Barcodes: EAN-13/UPC-A decoded by a built-in scan-line decoder; EAN-8 only via
  the OpenCV/pyzbar fallback.
* Leaflet is output as `Yes`/`No` (change in the profile if your target CSV differs).
