"""
Table Reader Utility

Re-parses a folder of PaddleOCR-VL JSON output into a flat, in-memory list
of extracted tables (each carrying its page/document metadata). This is
the single source of truth for "what tables came out of this document" —
json_to_csv.py merges these into one CSV, the API returns them as JSON
for the UI, so numbering and metadata never drift between the two.

No CSV/API-specific logic belongs here.
"""

import re
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path

import pandas as pd

from utils.field_validators import normalize_whitespace
from utils.json_utils import (
    get_page_metadata,
    get_table_title,
    get_tables,
    load_json,
)

# split_pdf writes "page_006.png" for the 6th page of the source PDF, and
# PaddleOCR names its result file after it ("page_006_res.json").
_PAGE_NUMBER_PATTERN = re.compile(r"page_(\d+)")

# Where a table's vertical merges ride on its DataFrame. Each entry is
# (first_row, last_row, column) — row numbers into that table, and a column
# index until the header is promoted, its name afterwards.
ROW_MERGES = "row_merges"


def _page_number(data: dict, json_file: Path) -> int | str:
    """
    Resolve the source PDF page number for one OCR result.

    The pipeline OCRs one PNG per page, so PaddleOCR reports no page_index
    (it only sets one for multi-page inputs). Fall back to the page number
    split_pdf encoded in the filename, which is the absolute page number in
    the source PDF even when only a range of pages was processed.
    """

    page_index = data.get("page_index")
    if page_index is not None:
        return page_index + 1

    match = _PAGE_NUMBER_PATTERN.search(json_file.stem)
    if match:
        return int(match.group(1))

    return "-"


# A cell that can never be part of a column header: a price, a percentage, a
# long number, or an ``M`` + digits code.  Header text is words.
_DATA_LIKE_CELL = re.compile(r"^(?:£\s?\d.*|.*\d\s?%|\d{6,}|M\d{3,9})$")


def _looks_like_data_row(row: pd.Series) -> bool:
    """
    True if a row holds values only a DATA row would hold.

    A table fragment that continues on the next page/block has no header, so
    its first row is a real product.  Promoting it to the header (what the
    old code did) both loses that product and names the columns after its
    values - which is how tail rows of split tables vanished (#5).
    """

    return any(
        pd.notna(v) and _DATA_LIKE_CELL.match(str(v).strip())
        for v in row
    )


def _promote_header_row(df: pd.DataFrame) -> pd.DataFrame:
    """
    Use the first row as column names when the table has no real header.

    PaddleOCR-VL emits table cells as <td> throughout, with no <th>, so
    pandas has nothing to read a header from: it numbers the columns 0..n
    and leaves the document's own header text sitting in row 0. Left alone
    that makes every column "0", "1", "2" — which never matches a ground
    truth file's column names, so no table would qualify for the merge.

    A header row may legitimately contain blank cells: the leading
    image/shelf column often has no title of its own. Those become
    positional names rather than blocking the promotion — refusing to
    promote over one empty cell left whole documents unmergeable.
    """

    positional = list(df.columns) == list(range(len(df.columns)))
    if not positional or len(df) < 2:
        return df

    header = df.iloc[0]

    # A header never contains prices / codes: this is a headerless continuation.
    if _looks_like_data_row(header):
        return df

    # Guard against promoting a row that is too empty to be a header at all.
    # A real header names most of its columns.
    if header.notna().sum() < max(2, len(header) // 2):
        return df

    merges = df.attrs.get(ROW_MERGES, [])

    df = df[1:].reset_index(drop=True)
    df.columns = [
        str(value).strip() if pd.notna(value) else f"Unnamed: {position}"
        for position, value in enumerate(header)
    ]

    # Row 0 is gone and the index restarts, so the spans move up one. A span
    # anchored on the header row itself describes the header, not a group of
    # products, and is dropped.
    columns = list(df.columns)
    df.attrs[ROW_MERGES] = [
        (first - 1, last - 1, columns[column])
        for first, last, column in merges
        if first > 0 and column < len(columns)
    ]
    return df


class _TableCellCollector(HTMLParser):
    """
    Collect the raw cells of every <table> in a fragment, row by row.

    We parse the OCR HTML ourselves rather than handing it to
    ``pd.read_html`` for two reasons, both of which cost real rows:

    1. ``pd.read_html`` resolves a ``rowspan`` by copying the spanned value
       into every row it covers. PaddleOCR-VL anchors those spans
       unreliably — it routinely puts the span on Std RSP when the merged
       cell in the document is Consumer Deal — so the copy lands a price in
       a column that did not have one. See ``_build_grid``.

    2. ``pd.read_html`` needs lxml, and falls back to BeautifulSoup +
       html5lib when lxml declines a fragment. PaddleOCR emits attributes
       like ``alt="Image""`` with a doubled quote, and a table holding
       nothing but an image, either of which lxml refuses; html5lib is not
       installed, so the fallback raises ImportError and aborts the whole
       document. The stdlib parser is lenient about both.
    """

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tables: list[list[list[dict]]] = []
        self._rows = None
        self._cells = None
        self._cell = None
        self._text: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self._rows = []
        elif tag == "tr" and self._rows is not None:
            self._cells = []
        elif tag in ("td", "th") and self._cells is not None:
            self._close_cell()
            self._cell = {
                "rowspan": _span(dict(attrs), "rowspan"),
                "colspan": _span(dict(attrs), "colspan"),
            }
            self._text = []

    def handle_startendtag(self, tag, attrs):
        # <img/> and friends carry no text; only container tags matter here.
        if tag not in ("td", "th", "tr", "table"):
            return
        self.handle_starttag(tag, attrs)

    def handle_data(self, data):
        if self._cell is not None:
            self._text.append(data)

    def _close_cell(self):
        if self._cell is None:
            return
        # normalize_whitespace also removes the two-character sequences
        # backslash+'n' / '\\r' / '\\t' that the OCR emits instead of real
        # line breaks (#9).  Plain ``\s+`` cannot match them: they are a
        # backslash followed by a letter, not whitespace.
        self._cell["text"] = normalize_whitespace("".join(self._text))
        self._cells.append(self._cell)
        self._cell = None
        self._text = []

    def handle_endtag(self, tag):
        if tag in ("td", "th"):
            self._close_cell()
        elif tag == "tr" and self._cells is not None:
            self._close_cell()
            self._rows.append(self._cells)
            self._cells = None
        elif tag == "table" and self._rows is not None:
            if self._cells:
                self._close_cell()
                self._rows.append(self._cells)
            self.tables.append(self._rows)
            self._rows = None
            self._cells = None


def _span(attrs: dict, name: str) -> int:
    """Read a colspan/rowspan attribute, tolerating junk values."""

    try:
        value = int(str(attrs.get(name, 1)).strip())
    except (TypeError, ValueError):
        return 1
    return value if value >= 1 else 1


def _build_grid(rows: list[list[dict]]) -> list[list[str]]:
    """
    Lay out one table's cells on a grid, resolving colspan and rowspan.

    A **colspan** repeats its value across the columns it covers, which is
    what ``pd.read_html`` does and what the row logic downstream expects: a
    category banner ('FRESH') is one full-width merged cell, and
    ``_is_banner`` recognises it precisely by the value being identical the
    whole way across.

    A **rowspan** is different. It reserves the column in the rows below —
    so the cells that follow still land where they belong — but those rows
    get a blank here, not a copy. PaddleOCR-VL decides which cell carries
    the span from a layout it has already misread: on a grouped product
    block it hangs the span on the repeated Std RSP instead of the merged
    Consumer Deal sitting beside it. Copying the value down at this point
    would write a standard retail price into Consumer Deal for every row of
    the group — 41 of 72 rows on one five-page document, and 555 cells on a
    longer one.

    The extent of each span is still worth keeping: it is the only record
    of which rows the document grouped together. It comes back as
    ``(first_row, last_row, column)`` so the merged value can be filled
    down later, once the anchor row has been realigned and the value in
    that column can be trusted. See ``fill_merged_groups``.
    """

    grid: dict[tuple[int, int], str] = {}
    merges: list[tuple[int, int, int]] = []
    width = 0

    for row_index, cells in enumerate(rows):
        column = 0
        for cell in cells:
            while (row_index, column) in grid:
                column += 1
            for across in range(cell["colspan"]):
                grid[(row_index, column + across)] = cell["text"]
                for down in range(1, cell["rowspan"]):
                    grid[(row_index + down, column + across)] = ""
                if cell["rowspan"] > 1 and cell["text"]:
                    merges.append(
                        (row_index, row_index + cell["rowspan"] - 1, column + across)
                    )
                width = max(width, column + across + 1)
            column += cell["colspan"]

    if not grid:
        return [], []

    height = max(row for row, _ in grid) + 1
    return [
        [grid.get((row, column), "") for column in range(width)]
        for row in range(height)
    ], merges


def parse_html_tables(html: str) -> list[pd.DataFrame]:
    """
    Parse an OCR HTML fragment into one DataFrame per <table>.

    Empty cells come back as NA rather than "", matching what the rest of
    the pipeline already expects from ``pd.read_html``.
    """

    collector = _TableCellCollector()
    collector.feed(html)
    collector.close()

    frames = []
    for rows in collector.tables:
        grid, merges = _build_grid(rows)
        if grid:
            frame = pd.DataFrame(grid).replace({"": None})
            frame.attrs[ROW_MERGES] = merges
            frames.append(frame)
    return frames


@dataclass
class TableRecord:
    table_index: int
    page_number: int | str
    document_name: str
    document_title: str | None
    header: str | None
    table_heading: str
    dataframe: pd.DataFrame
    # Layout-detected box of this table on the page image, (x0, y0, x1, y1)
    # in pixels - the key that ties this OCR table to the geometry rebuilt
    # from the PDF (see scripts/geometry_pass.py).
    bbox: tuple[float, float, float, float] | None = None
    # Position of this table among the page's tables, in reading order.
    block_index: int = 0


def read_tables(json_folder: str | Path) -> list[TableRecord]:
    """
    Parse every OCR result JSON file in a folder (in page order) and return
    one TableRecord per table found, numbered sequentially starting at 1.
    """

    json_folder = Path(json_folder)
    json_files = sorted(json_folder.glob("*.json"))

    if not json_files:
        raise FileNotFoundError(f"No JSON files found in {json_folder}")

    # The OCR JSON only knows the page image it was given, so take the
    # document name from the folder the pipeline created for this document.
    document_name = json_folder.name

    records: list[TableRecord] = []
    table_counter = 1

    for json_file in json_files:
        data = load_json(json_file)
        metadata = get_page_metadata(data)
        page_number = _page_number(data, json_file)

        for block_index, table in enumerate(get_tables(data)):
            html = table.get("block_content")
            if not html:
                continue

            raw_bbox = table.get("block_bbox")
            bbox = tuple(float(v) for v in raw_bbox) if raw_bbox and len(raw_bbox) == 4 else None

            table_heading = get_table_title(data, table) or "Untitled Table"

            for df in parse_html_tables(html):
                records.append(
                    TableRecord(
                        dataframe=_promote_header_row(df),
                        table_index=table_counter,
                        page_number=page_number,
                        document_name=document_name,
                        document_title=metadata["document_title"],
                        header=metadata["header"],
                        table_heading=table_heading,
                        bbox=bbox,
                        block_index=block_index,
                    )
                )
                table_counter += 1

    return records


def get_table_by_index(json_folder: str | Path, table_index: int) -> TableRecord | None:
    """Convenience lookup used by the per-table preview/compare endpoints."""

    for record in read_tables(json_folder):
        if record.table_index == table_index:
            return record
    return None
