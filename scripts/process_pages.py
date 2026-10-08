from pathlib import Path

from config.settings import (
    PAGES_DIR,
    JSON_DIR,
    SAVE_JSON,
    SAVE_MARKDOWN,
)
from services.ocr_service import ocr_service

class PageProcessor:
    """
    Process page images using PaddleOCR-VL.
    """

    def __init__(self):

        self.engine = ocr_service.get_engine()

    def process_folder(
        self,
        image_folder: str | Path,
        output_folder: str | Path,
    ):

        image_folder = Path(image_folder)
        output_folder = Path(output_folder)

        output_folder.mkdir(parents=True, exist_ok=True)

        image_files = sorted(image_folder.glob("*.png"))

        print(f"\nFound {len(image_files)} page(s).\n")

        for image_path in image_files:

            print("=" * 80)
            print(f"Processing {image_path.name}")

            results = self.engine.predict_image(image_path)

            for result in results:

                if SAVE_JSON:
                    result.save_to_json(save_path=output_folder)

                if SAVE_MARKDOWN:
                    result.save_to_markdown(save_path=output_folder)

            print(f"Finished {image_path.name}")

        print("\nAll pages processed successfully.")


if __name__ == "__main__":

    processor = PageProcessor()

    processor.process_folder(
        image_folder=PAGES_DIR / "sample",
        output_folder=JSON_DIR / "sample",
    )