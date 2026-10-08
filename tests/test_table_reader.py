import json

import pytest

from utils.table_reader import get_table_by_index, read_tables

TABLE_HTML = "<table><tr><th>A</th><th>B</th></tr><tr><td>1</td><td>2</td></tr></table>"

# what PaddleOCR-VL actually emits: every cell a <td>, no header markup
TD_ONLY_HTML = (
    "<table>"
    "<tr><td>Name</td><td>Salary</td></tr>"
    "<tr><td>John</td><td>5000</td></tr>"
    "<tr><td>Mary</td><td>4200</td></tr>"
    "</table>"
)


def _write_page_json(folder, filename, page_index, table_html=TABLE_HTML):
    data = {
        "input_path": "sample.pdf",
        "page_index": page_index,
        "parsing_res_list": [
            {"block_label": "doc_title", "block_content": "My Document", "block_order": 0},
            {"block_label": "header", "block_content": "Header text", "block_order": 1},
            {"block_label": "text", "block_content": "Employee Salary Details", "block_order": 2},
            {"block_label": "table", "block_content": table_html, "block_order": 3},
        ],
    }
    (folder / filename).write_text(json.dumps(data), encoding="utf-8")
    return data


def test_read_tables_empty_folder_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        read_tables(tmp_path)


def test_read_tables_parses_metadata_and_dataframe(tmp_path):
    json_folder = tmp_path / "sample"
    json_folder.mkdir()
    _write_page_json(json_folder, "page_001_res.json", page_index=0)

    tables = read_tables(json_folder)

    assert len(tables) == 1
    table = tables[0]
    assert table.table_index == 1
    assert table.page_number == 1  # page_index 0 -> displayed as page 1
    assert table.document_name == "sample"  # taken from the document folder
    assert table.document_title == "My Document"
    assert table.header == "Header text"
    assert table.table_heading == "Employee Salary Details"
    assert list(table.dataframe.columns) == ["A", "B"]
    # Cells stay as text. We read the OCR HTML ourselves rather than through
    # pd.read_html, which guessed a dtype per column; the merge stringifies
    # everything anyway, and guessing turns a long EAN barcode into a float.
    assert table.dataframe.iloc[0].tolist() == ["1", "2"]


def test_read_tables_numbers_sequentially_across_pages(tmp_path):
    _write_page_json(tmp_path, "page_001_res.json", page_index=0)
    _write_page_json(tmp_path, "page_002_res.json", page_index=1)

    tables = read_tables(tmp_path)

    assert [t.table_index for t in tables] == [1, 2]
    assert [t.page_number for t in tables] == [1, 2]


def test_page_number_falls_back_to_filename(tmp_path):
    """
    The pipeline OCRs one PNG per page, so PaddleOCR sets no page_index —
    the page number has to come from the filename split_pdf produced.
    """
    _write_page_json(tmp_path, "page_006_res.json", page_index=None)
    _write_page_json(tmp_path, "page_007_res.json", page_index=None)

    tables = read_tables(tmp_path)

    assert [t.page_number for t in tables] == [6, 7]


def test_page_number_unknown_when_filename_has_no_page(tmp_path):
    _write_page_json(tmp_path, "result.json", page_index=None)

    assert read_tables(tmp_path)[0].page_number == "-"


def test_read_tables_skips_blocks_without_html(tmp_path):
    data = {
        "input_path": "sample.pdf",
        "page_index": 0,
        "parsing_res_list": [
            {"block_label": "table", "block_content": "", "block_order": 0},
        ],
    }
    (tmp_path / "page_001_res.json").write_text(json.dumps(data), encoding="utf-8")

    assert read_tables(tmp_path) == []


def test_td_only_table_promotes_first_row_to_header(tmp_path):
    """
    PaddleOCR emits no <th>, so pandas numbers the columns 0..n and leaves
    the real header in row 0 — which never matches a ground truth file's
    column names. The first row has to become the header.
    """
    _write_page_json(tmp_path, "page_001_res.json", page_index=0, table_html=TD_ONLY_HTML)

    df = read_tables(tmp_path)[0].dataframe

    assert list(df.columns) == ["Name", "Salary"]
    assert len(df) == 2
    # values stay as text — re-inferring types would turn codes like "007" into 7
    assert df.iloc[0].tolist() == ["John", "5000"]


def test_real_header_is_left_alone(tmp_path):
    _write_page_json(tmp_path, "page_001_res.json", page_index=0, table_html=TABLE_HTML)

    df = read_tables(tmp_path)[0].dataframe

    assert list(df.columns) == ["A", "B"]
    assert len(df) == 1


def test_single_row_table_keeps_its_only_row(tmp_path):
    """Promoting the header here would leave a table with no data at all."""
    html = "<table><tr><td>Only</td><td>Row</td></tr></table>"
    _write_page_json(tmp_path, "page_001_res.json", page_index=0, table_html=html)

    df = read_tables(tmp_path)[0].dataframe

    assert len(df) == 1


def test_get_table_by_index(tmp_path):
    _write_page_json(tmp_path, "page_001_res.json", page_index=0)

    assert get_table_by_index(tmp_path, 1) is not None
    assert get_table_by_index(tmp_path, 2) is None


# a header row whose leading cell is blank — the unlabelled shelf/image
# column. Refusing to promote over this left whole documents unmergeable.
BLANK_LEAD_HEADER_HTML = (
    "<table>"
    "<tr><td></td><td>Product Description</td><td>Midas Code</td></tr>"
    "<tr><td>1</td><td>ECHO FALLS</td><td>M317988</td></tr>"
    "<tr><td>1</td><td>JACOBS CREEK</td><td>M317997</td></tr>"
    "</table>"
)


def test_header_with_a_blank_cell_is_still_promoted(tmp_path):
    _write_page_json(tmp_path, "page_001_res.json", page_index=0,
                     table_html=BLANK_LEAD_HEADER_HTML)

    df = read_tables(tmp_path)[0].dataframe

    assert "Product Description" in df.columns
    assert "Midas Code" in df.columns
    assert df.columns[0] == "Unnamed: 0"      # blank cell named positionally
    assert len(df) == 2                        # header consumed, data kept
    assert df.iloc[0]["Midas Code"] == "M317988"


def test_row_too_empty_to_be_a_header_is_left_alone(tmp_path):
    html = (
        "<table>"
        "<tr><td></td><td></td><td>x</td></tr>"
        "<tr><td>a</td><td>b</td><td>c</td></tr>"
        "</table>"
    )
    _write_page_json(tmp_path, "page_001_res.json", page_index=0, table_html=html)

    df = read_tables(tmp_path)[0].dataframe

    assert list(df.columns) == [0, 1, 2]       # not promoted
    assert len(df) == 2
