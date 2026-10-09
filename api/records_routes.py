"""
Records, extraction-history, preview, compare, and auth routes.

Kept separate from api/routes.py (which owns the original stateless
POST /extract/csv contract) because everything here reads/writes the
SQLite history database that backs the UI.
"""

import json
import re
import uuid
from pathlib import Path

import pandas as pd
from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse

from app_db import database
from config.settings import INPUT_DIR, JSON_DIR, MAX_FILE_SIZE, OUTPUT_DIR, TMP_DIR
from models.api_models import ExtractionResponse, TablePreviewResponse, TableSummary
from models.enums import ProcessingMode
from services.compare_service import (
    MappedComparison,
    compare_dataframes,
    compare_mapped,
    dataframe_columns,
    excel_safe,
    load_dataframe,
)
from services.extraction_service import run_extraction
from utils.table_reader import get_table_by_index

router = APIRouter()


def _safe_name(filename: str) -> str:
    """
    Strip any directory part off an uploaded filename.

    Browsers send a bare name, but a direct API client can send
    "../../x.pdf", which would otherwise write outside storage/input and
    make the pipeline clear directories elsewhere on disk.
    """

    return Path(filename or "").name


def _table_dataframe(record: dict, table_index: int) -> pd.DataFrame:
    json_folder = JSON_DIR / record["document_folder"]
    if not json_folder.exists():
        raise HTTPException(status_code=404, detail="Extraction data no longer on disk")

    table = get_table_by_index(json_folder, table_index)
    if table is None:
        raise HTTPException(status_code=404, detail="Table not found")
    return table.dataframe


def _merged_dataframe(record: dict) -> pd.DataFrame:
    csv_path = record.get("merged_csv_path")
    if not csv_path or not Path(csv_path).exists():
        raise HTTPException(status_code=404, detail="Merged CSV not found on disk")
    return pd.read_csv(csv_path, dtype=str, encoding="utf-8-sig")


async def _load_uploaded_table(file: UploadFile) -> pd.DataFrame:
    """Read an uploaded CSV/Excel into a DataFrame, without keeping the file."""

    ext = Path(file.filename or "").suffix.lower()
    if ext not in (".csv", ".xlsx", ".xls"):
        raise HTTPException(
            status_code=400,
            detail="Existing data must be a CSV or Excel file (.csv, .xlsx, .xls)",
        )

    TMP_DIR.mkdir(parents=True, exist_ok=True)
    path = TMP_DIR / f"existing_{uuid.uuid4().hex[:8]}{ext}"
    try:
        path.write_bytes(await file.read())
        return load_dataframe(str(path))
    except Exception as exc:
        # an unreadable spreadsheet is the user's file, not a server fault
        raise HTTPException(
            status_code=400,
            detail=f"Could not read {_safe_name(file.filename)}: {exc}",
        )
    finally:
        path.unlink(missing_ok=True)


XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _file_stem(name: str) -> str:
    """A filename fragment safe to build a path out of."""
    return re.sub(r"[^A-Za-z0-9._-]+", "_", Path(_safe_name(name)).stem).strip("_") or "document"


def _write_comparison_reports(
    comparison_id: int, record: dict, mapped: MappedComparison
) -> tuple[Path, Path]:
    """
    Write the annotated existing data and the extracted-only rows.

    Named after the comparison id, which is why this runs after the row is
    inserted rather than alongside the comparison itself.
    """
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    base = f"comparison_{comparison_id}_{_file_stem(record['filename'])}"

    existing_path = OUTPUT_DIR / f"{base}_existing-data.xlsx"
    extracted_only_path = OUTPUT_DIR / f"{base}_only-in-extracted.xlsx"

    excel_safe(mapped.existing_report).to_excel(existing_path, index=False)
    excel_safe(mapped.extracted_only).to_excel(extracted_only_path, index=False)
    return existing_path, extracted_only_path


def _comparison_report_file(comparison_id: int, column: str, suffix: str) -> FileResponse:
    comparison = database.get_comparison_record(comparison_id)
    if not comparison:
        raise HTTPException(status_code=404, detail="Comparison not found")

    path = comparison.get(column)
    if not path or not Path(path).exists():
        raise HTTPException(
            status_code=404,
            detail="No spreadsheet for this comparison — it predates the reports, "
                   "or the file is no longer on disk",
        )

    stem = _file_stem(comparison.get("ground_truth_filename") or "comparison")
    return FileResponse(
        path,
        media_type=XLSX_MEDIA_TYPE,
        filename=f"{stem}_{suffix}.xlsx",
    )


def _parse_pair(raw: str, label: str) -> dict:
    try:
        pair = json.loads(raw)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail=f"{label} must be JSON")
    if not isinstance(pair, dict) or not pair.get("extracted") or not pair.get("existing"):
        raise HTTPException(
            status_code=400,
            detail=f"{label} needs both an extracted and an existing column",
        )
    return {"extracted": str(pair["extracted"]), "existing": str(pair["existing"])}


def _parse_pairs(raw: str, label: str) -> list[dict]:
    try:
        pairs = json.loads(raw)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail=f"{label} must be JSON")
    if not isinstance(pairs, list) or not pairs:
        raise HTTPException(status_code=400, detail=f"{label} must be a non-empty list")
    return [_parse_pair(json.dumps(pair), label) for pair in pairs]


# ── auth ─────────────────────────────────────────────────────────────────

@router.post("/api/login")
async def login(username: str = Form(...), password: str = Form(...)):
    if database.verify_user(username, password):
        return {"success": True, "message": "Login successful", "username": username}
    raise HTTPException(status_code=401, detail="Invalid username or password")


# ── extraction (UI) ──────────────────────────────────────────────────────

@router.post("/api/extract", response_model=ExtractionResponse)
async def extract(
    file: UploadFile = File(..., description="PDF document"),
    processing_mode: ProcessingMode = Form(default=ProcessingMode.ALL),
    max_pages: int | None = Form(default=None, ge=1),
    start_page: int | None = Form(default=None, ge=1),
    end_page: int | None = Form(default=None, ge=1),
):
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are supported.")

    if processing_mode == ProcessingMode.FIRST_N and max_pages is None:
        raise HTTPException(
            status_code=400,
            detail="max_pages is required when processing_mode='first_n'.",
        )

    if processing_mode == ProcessingMode.PAGE_RANGE:
        if start_page is None or end_page is None:
            raise HTTPException(
                status_code=400,
                detail="start_page and end_page are required when processing_mode='page_range'.",
            )
        if end_page < start_page:
            raise HTTPException(
                status_code=400,
                detail="end_page must be greater than or equal to start_page.",
            )

    INPUT_DIR.mkdir(parents=True, exist_ok=True)

    content = await file.read()
    if len(content) > MAX_FILE_SIZE:
        raise HTTPException(
            status_code=400,
            detail=f"File exceeds {MAX_FILE_SIZE // (1024 * 1024)} MB limit",
        )

    uid = uuid.uuid4().hex[:8]
    pdf_path = INPUT_DIR / f"{uid}_{_safe_name(file.filename)}"
    pdf_path.write_bytes(content)

    try:
        # Deliberately synchronous, on the event loop thread. PaddleOCR-VL's
        # GPU inference hangs when driven from any other thread — the GPU
        # keeps its memory allocated but sits at 0% while a core spins — so
        # this cannot be moved to a threadpool. It does mean a running
        # extraction blocks the rest of the API until it finishes.
        result = run_extraction(
            pdf_path=pdf_path,
            original_filename=file.filename,
            processing_mode=processing_mode.value,
            max_pages=max_pages,
            start_page=start_page,
            end_page=end_page,
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Extraction failed: {e}")

    if not result.tables:
        # the run happened but found nothing — don't leave an empty record behind
        database.delete_extraction_record(result.record_id)
        raise HTTPException(status_code=422, detail="No tables were found in this PDF")

    if result.rows_extracted == 0:
        database.delete_extraction_record(result.record_id)
        raise HTTPException(
            status_code=422,
            detail=(
                f"Found {len(result.tables)} table(s), but none had the required "
                "'Midas Code' and 'Consumer Deal' columns, so there is nothing to merge"
            ),
        )

    merged = _merged_dataframe(database.get_extraction_record(result.record_id))

    tables_summary = [
        TableSummary(
            table_index=t.table_index,
            page_number=str(t.page_number),
            document_title=t.document_title,
            header=t.header,
            table_heading=t.table_heading,
            row_count=len(t.dataframe),
            col_count=len(t.dataframe.columns),
        )
        for t in result.tables
    ]

    message = (
        f"Merged {result.rows_extracted} rows from "
        f"{result.tables_merged}/{len(result.tables)} tables "
        f"across {result.pages_processed} page(s) in {result.elapsed_seconds:.1f}s"
    )
    if result.rows_repaired or result.rows_rejected:
        message += (
            f" — {result.rows_repaired} row(s) realigned, "
            f"{result.rows_rejected} rejected"
        )

    status = result.extraction_status
    missing = {str(p): c for p, c in (result.missing_midas or {}).items()}
    if status == "INCOMPLETE":
        # Fail loudly: say exactly how many products are missing and where.
        lost = sum(len(c) for c in missing.values())
        message = (
            f"INCOMPLETE EXTRACTION: expected {result.expected_rows} rows, "
            f"{lost} missing from the output (pages: {', '.join(sorted(missing)) or 'see warnings'}). "
            + " ".join(result.integrity_warnings or []) + " | " + message
        )
    elif status == "NEEDS_REVIEW":
        message = f"NEEDS REVIEW: {result.rows_rejected} row(s) need checking. " + message

    return ExtractionResponse(
        success=status != "INCOMPLETE",
        record_id=result.record_id,
        message=message,
        document_name=result.document_name,
        pages_processed=result.pages_processed,
        tables_extracted=len(result.tables),
        tables_merged=result.tables_merged,
        rows_extracted=result.rows_extracted,
        rows_repaired=result.rows_repaired,
        rows_rejected=result.rows_rejected,
        extraction_status=status,
        expected_rows=result.expected_rows,
        missing_midas=missing,
        integrity_warnings=result.integrity_warnings or [],
        columns=[str(c) for c in merged.columns],
        data_preview=merged.head(50).fillna("").to_dict(orient="records"),
        tables=tables_summary,
    )


# ── records ──────────────────────────────────────────────────────────────

@router.get("/api/records")
async def list_records():
    return {"records": database.get_all_extraction_records()}


@router.get("/api/records/{record_id}")
async def get_record(record_id: int):
    record = database.get_extraction_record(record_id)
    if not record:
        raise HTTPException(status_code=404, detail="Record not found")
    record["tables"] = database.get_extracted_tables(record_id)
    return record


@router.delete("/api/records/{record_id}")
async def delete_record(record_id: int):
    if not database.delete_extraction_record(record_id):
        raise HTTPException(status_code=404, detail="Record not found")
    return {"message": "Record deleted"}


@router.get("/api/records/{record_id}/preview", response_model=TablePreviewResponse)
async def preview_merged(record_id: int):
    """The merged product table — what the document actually produced."""

    record = database.get_extraction_record(record_id)
    if not record:
        raise HTTPException(status_code=404, detail="Record not found")

    df = _merged_dataframe(record)
    return TablePreviewResponse(
        columns=[str(c) for c in df.columns],
        data=df.head(100).fillna("").to_dict(orient="records"),
        total_rows=len(df),
    )


@router.get(
    "/api/records/{record_id}/tables/{table_index}/preview",
    response_model=TablePreviewResponse,
)
async def preview_table(record_id: int, table_index: int):
    """One table as it was read, before normalisation — for checking the merge."""

    record = database.get_extraction_record(record_id)
    if not record:
        raise HTTPException(status_code=404, detail="Record not found")

    df = _table_dataframe(record, table_index)
    return TablePreviewResponse(
        columns=[str(c) for c in df.columns],
        data=df.head(100).fillna("").to_dict(orient="records"),
        total_rows=len(df),
    )


# ── download ─────────────────────────────────────────────────────────────

@router.get("/download/{record_id}")
async def download_merged_csv(record_id: int):
    record = database.get_extraction_record(record_id)
    if not record:
        raise HTTPException(status_code=404, detail="Record not found")

    csv_path = record.get("merged_csv_path")
    if not csv_path or not Path(csv_path).exists():
        raise HTTPException(status_code=404, detail="Merged CSV not found on disk")

    return FileResponse(path=csv_path, filename=Path(csv_path).name, media_type="text/csv")


@router.get("/download/{record_id}/rejected")
async def download_rejected_csv(record_id: int):
    """Rows the merge could not trust, with the reason for each."""

    record = database.get_extraction_record(record_id)
    if not record:
        raise HTTPException(status_code=404, detail="Record not found")

    csv_path = record.get("rejected_csv_path")
    if not csv_path or not Path(csv_path).exists():
        raise HTTPException(status_code=404, detail="No rejected rows for this extraction")

    return FileResponse(path=csv_path, filename=Path(csv_path).name, media_type="text/csv")


# ── compare ──────────────────────────────────────────────────────────────

@router.post("/api/existing-data/columns")
async def existing_data_columns(file: UploadFile = File(..., description="CSV or Excel")):
    """
    The column names in an uploaded existing-data file.

    The UI needs these before the comparison runs: the unique key and the
    fields to compare are chosen by mapping these names against the
    extracted table's own.
    """

    df = await _load_uploaded_table(file)
    return {
        "filename": _safe_name(file.filename),
        "columns": dataframe_columns(df),
        "total_rows": len(df),
        "preview": df.head(5).to_dict(orient="records"),
    }


@router.post("/api/compare")
async def compare(
    extraction_id: int = Form(...),
    file: UploadFile = File(..., description="Existing data CSV or Excel"),
    table_index: int | None = Form(
        default=None,
        description="Compare one raw table instead of the merged CSV",
    ),
    unique_key: str | None = Form(
        default=None,
        description='JSON {"extracted": column, "existing": column} to match rows on',
    ),
    compare_fields: str | None = Form(
        default=None,
        description='JSON [{"extracted": column, "existing": column}, ...] to compare',
    ),
):
    record = database.get_extraction_record(extraction_id)
    if not record:
        raise HTTPException(status_code=404, detail="Extraction record not found")

    # a ground truth file describes the whole document, so the merged CSV is
    # the default target; a single table is available for narrowing down a
    # discrepancy once one shows up
    extracted_df = (
        _merged_dataframe(record)
        if table_index is None
        else _table_dataframe(record, table_index)
    )

    key_pair = _parse_pair(unique_key, "unique_key") if unique_key else None
    field_pairs = _parse_pairs(compare_fields, "compare_fields") if compare_fields else None

    if key_pair and not field_pairs:
        raise HTTPException(
            status_code=400,
            detail="Select at least one field to compare alongside the unique key",
        )
    if field_pairs and not key_pair:
        # otherwise the fields would be silently dropped and a positional
        # comparison returned as though it were the mapped one asked for
        raise HTTPException(
            status_code=400,
            detail="compare_fields needs a unique_key to pair rows on",
        )

    ground_df = await _load_uploaded_table(file)

    try:
        # With a mapping the two sides are joined on the key and only the
        # chosen fields are compared; without one it falls back to the
        # original positional comparison over identically named columns.
        mapped = (
            compare_mapped(extracted_df, ground_df, key_pair, field_pairs)
            if key_pair
            else None
        )
        comparison = mapped.summary if mapped else compare_dataframes(extracted_df, ground_df)

        comp_id = database.add_comparison_record(
            extraction_id=extraction_id,
            table_index=table_index if table_index is not None else -1,
            ground_truth_filename=file.filename,
            total_rows_extracted=comparison["total_rows_extracted"],
            total_rows_ground=comparison["total_rows_ground"],
            matching_rows=comparison["matching_rows"],
            mismatched_rows=comparison["mismatched_rows"],
            accuracy_percent=comparison["accuracy_percent"],
            comparison_details=json.dumps(comparison),
        )
        comparison["comparison_id"] = comp_id
        comparison["success"] = True

        if mapped is not None:
            existing_path, extracted_only_path = _write_comparison_reports(
                comp_id, record, mapped
            )
            database.set_comparison_reports(
                comp_id, str(existing_path), str(extracted_only_path)
            )
            comparison["downloads"] = {
                "existing_report": f"/download/comparison/{comp_id}/existing-data",
                "extracted_only": f"/download/comparison/{comp_id}/extracted-only",
            }

        return comparison

    except HTTPException:
        raise
    except ValueError as e:
        # a column named in the mapping that is not in the file
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Comparison failed: {e}")


@router.get("/download/comparison/{comparison_id}/existing-data")
async def download_comparison_existing(comparison_id: int):
    """The uploaded existing data, with the PDF's verdict on every row."""

    return _comparison_report_file(
        comparison_id, "existing_report_path", "existing-data"
    )


@router.get("/download/comparison/{comparison_id}/extracted-only")
async def download_comparison_extracted_only(comparison_id: int):
    """The extracted rows whose key never appears in the existing data."""

    return _comparison_report_file(
        comparison_id, "extracted_only_path", "only-in-extracted"
    )


@router.get("/api/comparisons")
async def list_comparisons():
    return {"comparisons": database.get_comparison_records()}
