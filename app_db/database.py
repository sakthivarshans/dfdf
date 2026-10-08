"""
SQLite Database

Backs the extraction-history UI: who can log in, what got extracted,
which tables came out of each job, and how those tables scored against
any ground truth the user uploaded for comparison.

No OCR/extraction logic belongs here.
"""

import hashlib
import sqlite3

from config.settings import (
    DB_PATH,
    DEFAULT_ADMIN_PASSWORD,
    DEFAULT_ADMIN_USERNAME,
)


def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def hash_password(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()


def init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)

    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            username      TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS extraction_records (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            filename          TEXT NOT NULL,
            document_folder   TEXT NOT NULL,
            merged_csv_path   TEXT,
            rejected_csv_path TEXT,
            processing_mode   TEXT DEFAULT 'all',
            max_pages         INTEGER,
            start_page        INTEGER,
            end_page          INTEGER,
            pages_processed   INTEGER DEFAULT 0,
            tables_extracted  INTEGER DEFAULT 0,
            tables_merged     INTEGER DEFAULT 0,
            rows_extracted    INTEGER DEFAULT 0,
            rows_repaired     INTEGER DEFAULT 0,
            rows_rejected     INTEGER DEFAULT 0,
            status            TEXT DEFAULT 'completed',
            created_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # add the merge columns to a database created before CSV output existed
    existing = {row[1] for row in cur.execute("PRAGMA table_info(extraction_records)")}
    for column, ddl in {
        "merged_csv_path": "TEXT",
        "rejected_csv_path": "TEXT",
        "tables_merged": "INTEGER DEFAULT 0",
        "rows_extracted": "INTEGER DEFAULT 0",
        "rows_repaired": "INTEGER DEFAULT 0",
        "rows_rejected": "INTEGER DEFAULT 0",
    }.items():
        if column not in existing:
            cur.execute(f"ALTER TABLE extraction_records ADD COLUMN {column} {ddl}")

    cur.execute("""
        CREATE TABLE IF NOT EXISTS extracted_tables (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            extraction_id  INTEGER NOT NULL,
            table_index    INTEGER NOT NULL,
            page_number    TEXT,
            document_title TEXT,
            header         TEXT,
            table_heading  TEXT,
            row_count      INTEGER DEFAULT 0,
            col_count      INTEGER DEFAULT 0,
            FOREIGN KEY (extraction_id) REFERENCES extraction_records(id)
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS comparison_records (
            id                    INTEGER PRIMARY KEY AUTOINCREMENT,
            extraction_id         INTEGER,
            table_index           INTEGER,
            ground_truth_filename TEXT NOT NULL,
            total_rows_extracted  INTEGER DEFAULT 0,
            total_rows_ground     INTEGER DEFAULT 0,
            matching_rows         INTEGER DEFAULT 0,
            mismatched_rows       INTEGER DEFAULT 0,
            accuracy_percent      REAL DEFAULT 0.0,
            comparison_details    TEXT,
            existing_report_path  TEXT,
            extracted_only_path   TEXT,
            created_at            TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (extraction_id) REFERENCES extraction_records(id)
        )
    """)

    # add the report columns to a database created before the comparison
    # produced downloadable spreadsheets
    existing_comparison_cols = {
        row[1] for row in cur.execute("PRAGMA table_info(comparison_records)")
    }
    for column in ("existing_report_path", "extracted_only_path"):
        if column not in existing_comparison_cols:
            cur.execute(f"ALTER TABLE comparison_records ADD COLUMN {column} TEXT")

    # seed default admin user
    cur.execute(
        "INSERT OR IGNORE INTO users (username, password_hash) VALUES (?, ?)",
        (DEFAULT_ADMIN_USERNAME, hash_password(DEFAULT_ADMIN_PASSWORD)),
    )

    conn.commit()
    conn.close()
    print(f"Database ready: {DB_PATH}")


# ── auth ─────────────────────────────────────────────────────────────────

def verify_user(username: str, password: str) -> bool:
    conn = get_db()
    row = conn.execute(
        "SELECT id FROM users WHERE username=? AND password_hash=?",
        (username, hash_password(password)),
    ).fetchone()
    conn.close()
    return row is not None


# ── extraction records ──────────────────────────────────────────────────

def add_extraction_record(
    filename: str,
    document_folder: str,
    merged_csv_path: str | None = None,
    rejected_csv_path: str | None = None,
    processing_mode: str = "all",
    max_pages: int | None = None,
    start_page: int | None = None,
    end_page: int | None = None,
    pages_processed: int = 0,
    tables_extracted: int = 0,
    tables_merged: int = 0,
    rows_extracted: int = 0,
    rows_repaired: int = 0,
    rows_rejected: int = 0,
    status: str = "completed",
) -> int:
    conn = get_db()
    cur = conn.execute(
        """
        INSERT INTO extraction_records
            (filename, document_folder, merged_csv_path, rejected_csv_path,
             processing_mode, max_pages, start_page, end_page, pages_processed,
             tables_extracted, tables_merged, rows_extracted, rows_repaired,
             rows_rejected, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (filename, document_folder, merged_csv_path, rejected_csv_path,
         processing_mode, max_pages, start_page, end_page, pages_processed,
         tables_extracted, tables_merged, rows_extracted, rows_repaired,
         rows_rejected, status),
    )
    record_id = cur.lastrowid
    conn.commit()
    conn.close()
    return record_id


def get_all_extraction_records() -> list[dict]:
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM extraction_records ORDER BY created_at DESC"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_extraction_record(record_id: int) -> dict | None:
    conn = get_db()
    row = conn.execute(
        "SELECT * FROM extraction_records WHERE id=?", (record_id,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def delete_extraction_record(record_id: int) -> bool:
    conn = get_db()
    # comparisons reference the record too — leaving them behind would keep
    # them in the history list pointing at a table that can no longer be read
    conn.execute("DELETE FROM comparison_records WHERE extraction_id=?", (record_id,))
    conn.execute("DELETE FROM extracted_tables WHERE extraction_id=?", (record_id,))
    cur = conn.execute("DELETE FROM extraction_records WHERE id=?", (record_id,))
    deleted = cur.rowcount > 0
    conn.commit()
    conn.close()
    return deleted


# ── extracted tables ─────────────────────────────────────────────────────

def add_extracted_table(
    extraction_id: int,
    table_index: int,
    page_number: str,
    document_title: str | None,
    header: str | None,
    table_heading: str,
    row_count: int,
    col_count: int,
) -> int:
    conn = get_db()
    cur = conn.execute(
        """
        INSERT INTO extracted_tables
            (extraction_id, table_index, page_number, document_title,
             header, table_heading, row_count, col_count)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (extraction_id, table_index, page_number, document_title,
         header, table_heading, row_count, col_count),
    )
    table_id = cur.lastrowid
    conn.commit()
    conn.close()
    return table_id


def get_extracted_tables(extraction_id: int) -> list[dict]:
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM extracted_tables WHERE extraction_id=? ORDER BY table_index",
        (extraction_id,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ── comparison records ───────────────────────────────────────────────────

def add_comparison_record(
    extraction_id: int,
    table_index: int,
    ground_truth_filename: str,
    total_rows_extracted: int,
    total_rows_ground: int,
    matching_rows: int,
    mismatched_rows: int,
    accuracy_percent: float,
    comparison_details: str,
) -> int:
    conn = get_db()
    cur = conn.execute(
        """
        INSERT INTO comparison_records
            (extraction_id, table_index, ground_truth_filename, total_rows_extracted,
             total_rows_ground, matching_rows, mismatched_rows,
             accuracy_percent, comparison_details)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (extraction_id, table_index, ground_truth_filename, total_rows_extracted,
         total_rows_ground, matching_rows, mismatched_rows,
         accuracy_percent, comparison_details),
    )
    record_id = cur.lastrowid
    conn.commit()
    conn.close()
    return record_id


def set_comparison_reports(
    comparison_id: int,
    existing_report_path: str | None,
    extracted_only_path: str | None,
) -> None:
    """
    Record where a comparison's two spreadsheets were written.

    Separate from the insert because both files are named after the
    comparison id, which only exists once the row does.
    """
    conn = get_db()
    conn.execute(
        """
        UPDATE comparison_records
           SET existing_report_path = ?, extracted_only_path = ?
         WHERE id = ?
        """,
        (existing_report_path, extracted_only_path, comparison_id),
    )
    conn.commit()
    conn.close()


def get_comparison_record(comparison_id: int) -> dict | None:
    conn = get_db()
    row = conn.execute(
        "SELECT * FROM comparison_records WHERE id=?", (comparison_id,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def get_comparison_records() -> list[dict]:
    conn = get_db()
    rows = conn.execute(
        """
        SELECT c.*, e.filename AS extraction_filename
        FROM comparison_records c
        LEFT JOIN extraction_records e ON c.extraction_id = e.id
        ORDER BY c.created_at DESC
        """
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]
