from pathlib import Path
import shutil
import sys

# Allow running directly
project_root = str(Path(__file__).resolve().parent.parent)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from config.settings import (
    PAGES_DIR,
    JSON_DIR,
    OUTPUT_DIR,
)

from scripts.split_pdf import split_pdf
from scripts.process_pages import PageProcessor
from scripts.json_to_csv import convert_json_folder
from scripts.geometry_pass import run_geometry_pass


def extract_document(
    pdf_file: str | Path,
    processing_mode: str = "all",
    max_pages: int | None = None,
    start_page: int | None = None,
    end_page: int | None = None,
):
    """
    Complete extraction workflow.

    PDF
        ↓
    Images
        ↓
    JSON
        ↓
    Excel
    """

    pdf_file = Path(pdf_file)

    if not pdf_file.exists():
        raise FileNotFoundError(pdf_file)

    document_name = pdf_file.stem

    page_folder = PAGES_DIR / document_name
    json_folder = JSON_DIR / document_name
    csv_file = OUTPUT_DIR / f"{document_name}.csv"

    # Clean previous outputs
    if page_folder.exists():
        shutil.rmtree(page_folder)

    if json_folder.exists():
        shutil.rmtree(json_folder)

    page_folder.mkdir(parents=True, exist_ok=True)
    json_folder.mkdir(parents=True, exist_ok=True)

    print("=" * 80)
    print("STEP 1 : Split PDF")
    print("=" * 80)

    split_pdf(
        input_pdf=pdf_file,
        output_dir=page_folder,
        processing_mode=processing_mode,
        max_pages=max_pages,
        start_page=start_page,
        end_page=end_page,
    )

    print("\n")

    print("=" * 80)
    print("STEP 2 : OCR Processing")
    print("=" * 80)

    processor = PageProcessor()

    processor.process_folder(
        image_folder=page_folder,
        output_folder=json_folder,
    )

    print("\n")

    print("=" * 80)
    print("STEP 2b : Geometry verification (PDF text layer, barcodes, borders)")
    print("=" * 80)

    # Rebuilds each table from the PDF itself and decodes barcodes / Leaflet
    # icons.  On scanned PDFs the OCR engine is reused for a second read.
    run_geometry_pass(
        pdf_file=pdf_file,
        pages_folder=page_folder,
        json_folder=json_folder,
        engine=processor.engine,
    )

    print("\n")

    print("=" * 80)
    print("STEP 3 : JSON → CSV")
    print("=" * 80)

    result = convert_json_folder(
        json_folder=json_folder,
        output_csv=csv_file,
    )

    print("\n")

    print("=" * 80)
    print("DOCUMENT EXTRACTION COMPLETED")
    print("=" * 80)

    print(f"CSV : {csv_file}")

    return result


if __name__ == "__main__":

    print("--- Mode: all ---")
    extract_document(
        pdf_file="storage/input/sample.pdf",
        processing_mode="all",
    )
    
    print("--- Mode: first_n ---")
    extract_document(
        pdf_file="storage/input/sample.pdf",
        processing_mode="first_n",
        max_pages=3,
    )
    
    print("--- Mode: page_range ---")
    extract_document(
        pdf_file="storage/input/sample.pdf",
        processing_mode="page_range",
        start_page=2,
        end_page=4,
    )