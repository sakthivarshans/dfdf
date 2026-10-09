"""
Extraction Service

Orchestrates the existing PDF -> images -> OCR -> merged CSV pipeline
(scripts.extract_document), packages the result for the API/UI, and records
the outcome in the database.
"""

import time
from dataclasses import dataclass
from pathlib import Path

from app_db import database
from config.settings import JSON_DIR, PAGES_DIR
from scripts.extract_document import extract_document
from utils.table_reader import TableRecord, read_tables


@dataclass
class ExtractionResult:
    record_id: int
    document_name: str
    merged_csv_path: Path
    rejected_csv_path: Path | None
    tables: list[TableRecord]
    tables_merged: int
    rows_extracted: int
    rows_repaired: int
    rows_rejected: int
    pages_processed: int
    elapsed_seconds: float
    # Integrity of the run: did every product in the source reach the output?
    extraction_status: str = "COMPLETE"      # COMPLETE | NEEDS_REVIEW | INCOMPLETE
    expected_rows: int = 0
    missing_midas: dict | None = None        # {page: [Midas codes lost]}
    integrity_warnings: list | None = None


def run_extraction(
    pdf_path: str | Path,
    original_filename: str,
    processing_mode: str = "all",
    max_pages: int | None = None,
    start_page: int | None = None,
    end_page: int | None = None,
) -> ExtractionResult:
    """
    Run the full extraction pipeline for one uploaded PDF and record the
    outcome (job summary + one row per table found) in the database.
    """

    pdf_path = Path(pdf_path)
    start_time = time.time()

    csv_result = extract_document(
        pdf_file=pdf_path,
        processing_mode=processing_mode,
        max_pages=max_pages,
        start_page=start_page,
        end_page=end_page,
    )

    document_name = pdf_path.stem
    json_folder = JSON_DIR / document_name
    page_folder = PAGES_DIR / document_name

    tables = read_tables(json_folder)
    pages_processed = len(list(page_folder.glob("*.png")))
    elapsed = time.time() - start_time

    record_id = database.add_extraction_record(
        filename=original_filename,
        document_folder=document_name,
        merged_csv_path=str(csv_result.merged_csv_path),
        rejected_csv_path=(
            str(csv_result.rejected_csv_path) if csv_result.rejected_csv_path else None
        ),
        processing_mode=processing_mode,
        max_pages=max_pages,
        start_page=start_page,
        end_page=end_page,
        pages_processed=pages_processed,
        tables_extracted=csv_result.tables_total,
        tables_merged=csv_result.tables_merged,
        rows_extracted=csv_result.rows_extracted,
        rows_repaired=csv_result.rows_repaired,
        rows_rejected=csv_result.rows_rejected,
        # "completed" keeps the UI's green badge; anything else is shown as a warning
        status={"COMPLETE": "completed", "NEEDS_REVIEW": "needs_review",
                "INCOMPLETE": "incomplete"}.get(csv_result.extraction_status, "completed"),
    )

    for table in tables:
        database.add_extracted_table(
            extraction_id=record_id,
            table_index=table.table_index,
            page_number=str(table.page_number),
            document_title=table.document_title,
            header=table.header,
            table_heading=table.table_heading,
            row_count=len(table.dataframe),
            col_count=len(table.dataframe.columns),
        )

    return ExtractionResult(
        record_id=record_id,
        document_name=document_name,
        merged_csv_path=csv_result.merged_csv_path,
        rejected_csv_path=csv_result.rejected_csv_path,
        tables=tables,
        tables_merged=csv_result.tables_merged,
        rows_extracted=csv_result.rows_extracted,
        rows_repaired=csv_result.rows_repaired,
        rows_rejected=csv_result.rows_rejected,
        pages_processed=pages_processed,
        elapsed_seconds=elapsed,
        extraction_status=csv_result.extraction_status,
        expected_rows=csv_result.expected_rows,
        missing_midas=csv_result.missing_midas,
        integrity_warnings=csv_result.integrity_warnings,
    )
