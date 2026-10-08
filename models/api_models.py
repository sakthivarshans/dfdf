"""
Pydantic response models for the extraction/records/compare API.
"""

from pydantic import BaseModel


class TableSummary(BaseModel):
    table_index: int
    page_number: str
    document_title: str | None = None
    header: str | None = None
    table_heading: str
    row_count: int
    col_count: int


class ExtractionResponse(BaseModel):
    success: bool
    record_id: int
    message: str
    document_name: str
    pages_processed: int
    tables_extracted: int
    tables_merged: int
    rows_extracted: int
    rows_repaired: int
    rows_rejected: int
    columns: list[str]
    data_preview: list[dict]
    tables: list[TableSummary]


class TablePreviewResponse(BaseModel):
    columns: list[str]
    data: list[dict]
    total_rows: int


class ErrorResponse(BaseModel):
    error: str
    detail: str | None = None
