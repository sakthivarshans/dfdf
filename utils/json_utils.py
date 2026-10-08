"""
JSON Utility Functions

These functions work with PaddleOCR-VL JSON output.

Every exporter (Excel, CSV, API, Database)
should use these helpers instead of reading
the JSON directly.
"""

import json
from pathlib import Path


def load_json(json_file: str | Path) -> dict:
    """
    Load PaddleOCR JSON file.
    """

    json_file = Path(json_file)

    with open(json_file, "r", encoding="utf-8") as f:
        return json.load(f)


def get_document_name(data: dict) -> str:
    """
    Return original document name.
    """

    return Path(
        data.get("input_path", "")
    ).stem


def get_page_number(data: dict) -> int | None:
    """
    Return page number if available.
    """

    return data.get("page_index")


def get_blocks(data: dict) -> list:
    """
    Return layout blocks.
    """

    return data.get("parsing_res_list", [])


def get_tables(data: dict) -> list:
    """
    Return only table blocks.
    """

    return [
        block
        for block in get_blocks(data)
        if block.get("block_label") == "table"
    ]


def get_text_blocks(data: dict) -> list:
    """
    Return only text blocks.
    """

    return [
        block
        for block in get_blocks(data)
        if block.get("block_label") == "text"
    ]


def find_html_tables(data: dict) -> list[str]:
    """
    Return HTML tables from PaddleOCR JSON.
    """

    html_tables = []

    for table in get_tables(data):

        html = table.get("block_content")

        if html and "<table" in html.lower():

            html_tables.append(html)

    return html_tables

def get_table_title(data: dict, table_block: dict) -> str:
    """
    Return the text immediately before a table.

    If no suitable heading exists,
    return "Untitled Table".
    """

    blocks = get_blocks(data)

    table_order = table_block.get("block_order")
    
    if table_order is None:
        return "Untitled Table"

    title = None

    for block in blocks:

        if block.get("block_order") is None:
            continue

        if block.get("block_order") >= table_order:
            break

        if block.get("block_label") == "text":

            text = block.get("block_content", "").strip()

            if text:
                title = text

    return title or "Untitled Table"

def get_page_header(data: dict) -> str | None:
    """
    Return page header.
    """

    headers = []

    for block in get_blocks(data):

        if block.get("block_label") == "header":

            text = block.get("block_content", "").strip()

            if text:
                headers.append(text)

    if headers:
        return " | ".join(headers)

    return None

def get_document_title(data: dict) -> str | None:
    """
    Return the document title detected by PaddleOCR.
    """

    for block in get_blocks(data):

        if block.get("block_label") == "doc_title":
            return block.get("block_content", "").strip()

    return None

def get_page_metadata(data: dict) -> dict:
    """
    Return page-level metadata.

    Returns
    -------
    {
        "document_name": "...",
        "page_number": 1,
        "document_title": "...",
        "header": "...",
    }
    """

    return {
        "document_name": get_document_name(data),
        "page_number": get_page_number(data),
        "document_title": get_document_title(data),
        "header": get_page_header(data),
    }

if __name__ == "__main__":

    json_file = (
        "storage/json/sample/page_006_res.json"
    )

    data = load_json(json_file)

    print("=" * 80)

    print("Document :", get_document_name(data))

    print("Page :", get_page_number(data))

    print("Blocks :", len(get_blocks(data)))

    print("Tables :", len(get_tables(data)))

    print("Text Blocks :", len(get_text_blocks(data)))

    print("HTML Tables :", len(find_html_tables(data)))

    print("=" * 80)