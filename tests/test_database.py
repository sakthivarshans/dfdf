import pytest

from app_db import database


@pytest.fixture
def db(tmp_path, monkeypatch):
    """Point the database module at a throwaway SQLite file for this test."""
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "test.db")
    database.init_db()
    return database


def test_init_db_seeds_default_admin(db):
    assert db.verify_user(db.DEFAULT_ADMIN_USERNAME, db.DEFAULT_ADMIN_PASSWORD)
    assert not db.verify_user(db.DEFAULT_ADMIN_USERNAME, "wrong-password")
    assert not db.verify_user("nobody", "whatever")


def test_extraction_record_roundtrip(db):
    record_id = db.add_extraction_record(
        filename="sample.pdf",
        document_folder="sample",
        merged_csv_path="/tmp/sample.csv",
        processing_mode="page_range",
        start_page=2,
        end_page=4,
        pages_processed=3,
        tables_extracted=5,
        tables_merged=2,
        rows_extracted=40,
        rows_repaired=3,
        rows_rejected=1,
    )

    record = db.get_extraction_record(record_id)
    assert record is not None
    assert record["filename"] == "sample.pdf"
    assert record["processing_mode"] == "page_range"
    assert record["merged_csv_path"] == "/tmp/sample.csv"
    assert record["tables_merged"] == 2
    assert record["rows_extracted"] == 40
    assert record["rows_repaired"] == 3
    assert record["rows_rejected"] == 1

    all_records = db.get_all_extraction_records()
    assert len(all_records) == 1
    assert all_records[0]["id"] == record_id

    assert db.get_extraction_record(record_id + 1) is None


def test_extracted_tables_roundtrip(db):
    record_id = db.add_extraction_record(filename="sample.pdf", document_folder="sample")

    db.add_extracted_table(
        extraction_id=record_id,
        table_index=1,
        page_number="1",
        document_title="Title",
        header="Header",
        table_heading="Employee Salary Details",
        row_count=5,
        col_count=3,
    )
    db.add_extracted_table(
        extraction_id=record_id,
        table_index=2,
        page_number="2",
        document_title="Title",
        header="Header",
        table_heading="Untitled Table",
        row_count=1,
        col_count=2,
    )

    tables = db.get_extracted_tables(record_id)
    assert [t["table_index"] for t in tables] == [1, 2]
    assert tables[0]["table_heading"] == "Employee Salary Details"


def test_delete_extraction_record_cascades(db):
    record_id = db.add_extraction_record(filename="sample.pdf", document_folder="sample")
    db.add_extracted_table(
        extraction_id=record_id,
        table_index=1,
        page_number="1",
        document_title=None,
        header=None,
        table_heading="Untitled Table",
        row_count=1,
        col_count=1,
    )

    assert db.delete_extraction_record(record_id) is True
    assert db.get_extraction_record(record_id) is None
    assert db.get_extracted_tables(record_id) == []
    assert db.delete_extraction_record(record_id) is False


def test_delete_extraction_record_removes_its_comparisons(db):
    """
    A left-behind comparison stays in the history list forever, pointing at a
    table that can no longer be previewed.
    """
    record_id = db.add_extraction_record(filename="sample.pdf", document_folder="sample")
    db.add_comparison_record(
        extraction_id=record_id,
        table_index=1,
        ground_truth_filename="truth.csv",
        total_rows_extracted=4,
        total_rows_ground=4,
        matching_rows=4,
        mismatched_rows=0,
        accuracy_percent=100.0,
        comparison_details="{}",
    )

    db.delete_extraction_record(record_id)

    assert db.get_comparison_records() == []


def test_comparison_records_roundtrip(db):
    record_id = db.add_extraction_record(filename="sample.pdf", document_folder="sample")

    comp_id = db.add_comparison_record(
        extraction_id=record_id,
        table_index=1,
        ground_truth_filename="truth.csv",
        total_rows_extracted=10,
        total_rows_ground=10,
        matching_rows=9,
        mismatched_rows=1,
        accuracy_percent=90.0,
        comparison_details="{}",
    )

    comparisons = db.get_comparison_records()
    assert len(comparisons) == 1
    assert comparisons[0]["id"] == comp_id
    assert comparisons[0]["extraction_filename"] == "sample.pdf"
