"""
OCR Service

Responsible for managing a single instance
of PaddleEngine throughout the application.

No business logic belongs here.
"""

from engine.paddle_engine import PaddleEngine


class OCRService:
    """
    Singleton service for PaddleEngine.
    """

    _instance = None
    _engine = None

    def __new__(cls):

        if cls._instance is None:

            cls._instance = super().__new__(cls)

        return cls._instance

    def initialize(self):
        """
        Initialize PaddleOCR engine only once.
        """

        if self._engine is None:

            print("=" * 80)
            print("Initializing PaddleOCR Engine...")
            print("=" * 80)

            self._engine = PaddleEngine()

            print("PaddleOCR Engine Ready.\n")

        return self._engine

    def get_engine(self):
        """
        Return the existing PaddleEngine.

        Raises
        ------
        RuntimeError
            If initialize() has not been called.
        """

        if self._engine is None:

            raise RuntimeError(
                "OCR Engine has not been initialized."
            )

        return self._engine

    def predict_image(self, image_path):
        """
        Predict a single image.
        """

        return self.get_engine().predict_image(
            image_path
        )

    def predict_images(self, image_paths):
        """
        Predict multiple images.
        """

        results = []

        for image in image_paths:

            results.append(
                self.predict_image(image)
            )

        return results


# Singleton object
ocr_service = OCRService()