"""
Turn one document's OCR JSON (+ geometry sidecars) into CSV.

Writes these files:

  <doc>.csv               ONLY rows in which every field passed strict
                          validation.  This is the file to trust.
  <doc>-rejected.csv      rows that did not pass, each with the exact reason
                          (the "needs review" list - nothing is silently lost)
  <doc>-audit.csv         where the page-level OCR disagreed with the PDF
                          geometry, plus completeness warnings
  individual/*.csv        every OCR table exactly as it was read

Where each row comes from
-------------------------
For every page the converter prefers the **geometry sidecar** written by
``scripts/geometry_pass.py`` (exact text from the PDF, structure from the
drawn borders, barcodes decoded from bars, Leaflet icon from pixels).  Only
the parts of a page the geometry pass could not rebuild fall back to the
OCR HTML tables, which then go through the same strict validation.

Completeness proof
------------------
``rows_kept + rows_needs_review`` is compared with the number of Midas codes
that exist on the pages.  If they differ, the audit file says so.
"""

from dataclasses import dataclass, field
from pathlib import Path
import sys

import pandas as pd

project_root = str(Path(__file__).resolve().parent.parent)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from config.settings import JSON_DIR, OUTPUT_DIR
from utils.reconcile import (
    covered_by_geometry,
    cross_check,
    geometry_to_rows,
    integrity_report,
    load_sidecars,
)
from utils.table_normalizer import (
    _kept_columns,
    _review_columns,
    ACTIVE_PROFILE,
    clean_rows,
    finalize_rows,
    normalize_table,
    _contains_midas_like,
)
from utils.table_reader import read_tables

CSV_ENCODING = "utf-8-sig"  # keeps Excel happy with £ and accented characters


@dataclass
class CsvResult:
    merged_csv_path: Path
    rejected_csv_path: Path | None
    individual_dir: Path
    tables_total: int
    tables_merged: int
    rows_extracted: int
    rows_repaired: int
    rows_rejected: int
    columns: list[str] = field(default_factory=list)
    # --- added by the accuracy rework (all optional, so old callers work) ---
    rows_needs_review: int = 0
    audit_csv_path: Path | None = None
    geometry_pages: int = 0
    midas_codes_on_pages: int = 0
    # --- extraction integrity (source rows vs produced rows) ---
    extraction_status: str = "COMPLETE"      # COMPLETE | NEEDS_REVIEW | INCOMPLETE
    expected_rows: int = 0
    missing_midas: dict = field(default_factory=dict)   # {page: [codes lost]}
    integrity_warnings: list = field(default_factory=list)


def _sort_key(frame: pd.DataFrame) -> pd.DataFrame:
    """Order rows by page, keeping each source's own row order (stable sort)."""

    if frame.empty:
        return frame
    page = pd.to_numeric(frame["page"], errors="coerce").fillna(10 ** 6)
    return frame.assign(_p=page).sort_values("_p", kind="stable").drop(columns="_p").reset_index(drop=True)


def convert_json_folder(
    json_folder: str | Path,
    output_csv: str | Path,
) -> CsvResult:
    """Convert one folder of PaddleOCR JSON (+ sidecars) into a validated CSV."""

    json_folder = Path(json_folder)
    output_csv = Path(output_csv)
    output_csv.parent.mkdir(parents=True, exist_ok=True)

    individual_dir = output_csv.parent / output_csv.stem / "individual"
    individual_dir.mkdir(parents=True, exist_ok=True)

    tables = read_tables(json_folder)
    sidecars = load_sidecars(json_folder)

    # ---- 1. OCR tables: normalise; those geometry covers become audit-only ----
    vlm_frames, vlm_audit_rows = [], []
    inherit_schema = None
    page_of: dict[str, object] = {}
    skipped_with_data: list[dict] = []

    for record in tables:
        name = f"table-{record.table_index}"
        page_of[name] = record.page_number
        record.dataframe.to_csv(individual_dir / f"{name}.csv", index=False, encoding=CSV_ENCODING)

        normalized = normalize_table(record.dataframe, name, inherit_schema)
        if normalized is None:
            print(f"  skip {name} - no Midas Code / Consumer Deal columns")
            # A skipped table that holds Midas-looking values means product
            # rows may be missing - never let that pass silently.
            if _contains_midas_like(record.dataframe):
                skipped_with_data.append({
                    "page": record.page_number, "midas": "", "column": "(table)",
                    "geometry": "",
                    "vlm": f"{name} skipped (no usable header) but contains Midas-like values: rows may be missing"})
            continue
        inherit_schema = normalized.attrs.get("raw_schema", inherit_schema)

        page = record.page_number
        sidecar = sidecars.get(page) if isinstance(page, int) else None
        if covered_by_geometry(sidecar, record.bbox):
            # keep for cross-checking only
            for _, row in normalized.iterrows():
                vlm_audit_rows.append({k: row.get(k) for k in normalized.columns} | {"_page": page})
        else:
            normalized["page"] = page
            vlm_frames.append(normalized)

    # ---- 2. OCR path (only for regions geometry did not cover) ---------------
    kept_parts, review_parts = [], []
    stats = {"repaired": 0, "rejected": 0, "needs_review": 0, "filled": 0, "banners": 0, "blank_midas": 0}
    corrections: list[dict] = []
    for frame in vlm_frames:
        kept, review, s = clean_rows(frame)
        corrections.extend(s.get("corrections", []))
        for key in stats:
            stats[key] += s.get(key, 0)
        page = frame["page"].iloc[0] if "page" in frame else ""
        for part in (kept, review):
            if not part.empty:
                part["page"] = page
        kept_parts.append(kept)
        review_parts.append(review)

    # ---- 3. Geometry path ----------------------------------------------------
    geometry_rows: list[dict] = []
    audit: list[dict] = list(skipped_with_data)
    anchors_total = 0
    for page, sidecar in sorted(sidecars.items()):
        rows, counters = geometry_to_rows(sidecar)
        geometry_rows.extend(rows)
        stats["banners"] += counters["banners"]
        anchors_total += sidecar.get("anchors_on_page", 0)
        for warning in sidecar.get("warnings", []):
            audit.append({"page": page, "midas": "", "column": "(page)", "geometry": "", "vlm": warning})
        for t in sidecar.get("tables", []):
            for warning in t.get("warnings", []):
                audit.append({"page": page, "midas": "", "column": "(table)", "geometry": "", "vlm": warning})

    g_kept, g_review, g_stats = finalize_rows(geometry_rows)
    kept_parts.append(g_kept)
    review_parts.append(g_review)
    corrections.extend(g_stats.get("corrections", []))
    for c in corrections:                       # every automatic correction is auditable
        audit.append({"page": c["page"], "midas": c["midas"], "column": c["column"],
                      "geometry": "", "vlm": f"corrected by document profile: {c['change']}"})
    stats["needs_review"] += g_stats["needs_review"]
    audit.extend(cross_check(geometry_rows, vlm_audit_rows))

    # ---- 4. Assemble ------------------------------------------------------------
    nonempty = lambda parts, cols: [p for p in parts if not p.empty] or [pd.DataFrame(columns=cols)]
    merged = _sort_key(pd.concat(nonempty(kept_parts, _kept_columns(ACTIVE_PROFILE)), ignore_index=True))
    rejected = _sort_key(pd.concat(nonempty(review_parts, _review_columns(ACTIVE_PROFILE)), ignore_index=True))

    # Integrity check: every product the SOURCE contains (PDF geometry and raw
    # OCR JSON) must be in the CSV or in needs-review.  Otherwise the run is
    # INCOMPLETE - it is never reported as a success.
    integrity = integrity_report(sidecars, json_folder, merged, rejected)
    for page, codes in integrity["missing_midas"].items():
        audit.append({"page": page, "midas": ",".join(codes), "column": "(MISSING ROWS)", "geometry": "",
                      "vlm": f"{len(codes)} product(s) present in the source are absent from the output"})
    for warning in integrity["warnings"]:
        audit.append({"page": "", "midas": "", "column": "(integrity)", "geometry": "", "vlm": warning})

    merged.to_csv(output_csv, index=False, encoding=CSV_ENCODING)

    rejected_path = None
    if not rejected.empty:
        rejected_path = output_csv.with_name(f"{output_csv.stem}-rejected.csv")
        rejected.to_csv(rejected_path, index=False, encoding=CSV_ENCODING)

    audit_path = None
    if audit:
        audit_path = output_csv.with_name(f"{output_csv.stem}-audit.csv")
        pd.DataFrame(audit).rename(columns={"geometry": "pdf_geometry_value", "vlm": "ocr_value_or_note"}) \
            .to_csv(audit_path, index=False, encoding=CSV_ENCODING)

    bar = "=" * 70
    print(f"\n{bar}\n  EXTRACTION STATUS : {integrity['status']}\n"
          f"  expected rows     : {integrity['expected_rows']}\n"
          f"  extracted rows    : {integrity['extracted_rows']}  "
          f"(kept {integrity['kept_rows']}, needs review {integrity['review_rows']})\n"
          f"  missing rows      : {integrity['missing_rows']}")
    for page, codes in integrity["missing_midas"].items():
        print(f"    page {page}: {', '.join(codes)}")
    for warning in integrity["warnings"]:
        print(f"  WARNING: {warning}")
    print(bar)
    print(
        f"\n  tables       : {len(tables)} OCR tables, {len(sidecars)} geometry page(s)"
        f"\n  rows kept    : {len(merged)}   (every field validated)"
        f"\n  needs review : {len(rejected)}"
        f"\n  midas in PDF : {anchors_total}"
        f"\n  banners      : {stats['banners']}"
        f"\nCSV saved     : {output_csv}"
    )
    if rejected_path:
        print(f"Needs review  : {rejected_path}")
    if audit_path:
        print(f"Audit         : {audit_path}")

    return CsvResult(
        merged_csv_path=output_csv,
        rejected_csv_path=rejected_path,
        individual_dir=individual_dir,
        tables_total=len(tables),
        tables_merged=len(vlm_frames) + len(sidecars),
        rows_extracted=len(merged),
        rows_repaired=stats["repaired"],
        rows_rejected=len(rejected),
        columns=list(merged.columns) if not merged.empty else [],
        rows_needs_review=len(rejected),
        audit_csv_path=audit_path,
        geometry_pages=len(sidecars),
        midas_codes_on_pages=anchors_total,
        extraction_status=integrity["status"],
        expected_rows=integrity["expected_rows"],
        missing_midas=integrity["missing_midas"],
        integrity_warnings=integrity["warnings"],
    )


if __name__ == "__main__":

    convert_json_folder(
        json_folder=JSON_DIR / "sample",
        output_csv=OUTPUT_DIR / "sample.csv",
    )
