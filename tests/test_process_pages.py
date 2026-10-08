from pathlib import Path
from services.ocr_service import ocr_service
from scripts.process_pages import PageProcessor


def test_process_pages():

    # Initialize the OCR engine once
    ocr_service.initialize()

    processor = PageProcessor()

    processor.process_folder(
        image_folder="storage/pages/sample",
        output_folder="storage/json/test",
    )

    json_files = list(Path("storage/json/test").glob("*.json"))

    assert len(json_files) == 5

if __name__ == "__main__":
    test_process_pages()