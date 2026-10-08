from pathlib import Path
import sys

# Allow running directly
project_root = Path(__file__).resolve().parents[2]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from scripts.extract_document import extract_document

def main():

    PDF_FILE = "storage/input/sample.pdf"

    START_PAGE = 6
    END_PAGE = 8

    print("=" * 80)
    print(f"Testing Page Range : {START_PAGE} - {END_PAGE}")
    print("=" * 80)

    output = extract_document(
        pdf_file=PDF_FILE,
        processing_mode="page_range",
        start_page=START_PAGE,
        end_page=END_PAGE,
    )

    print("\n" + "=" * 80)
    print("Extraction Completed Successfully")
    print("=" * 80)
    print(f"Output File : {output}")


if __name__ == "__main__":
    main()