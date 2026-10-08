from pathlib import Path
import shutil
import pytest

from scripts.split_pdf import split_pdf

@pytest.fixture
def clean_test_dirs():
    output_dirs = ["storage/pages/test_all", "storage/pages/test_first_n", "storage/pages/test_range"]
    for d in output_dirs:
        path = Path(d)
        if path.exists():
            shutil.rmtree(path)
    yield
    for d in output_dirs:
        path = Path(d)
        if path.exists():
            shutil.rmtree(path)

def test_split_pdf_all(clean_test_dirs):
    images = split_pdf(
        input_pdf="storage/input/sample.pdf",
        output_dir="storage/pages/test_all",
        processing_mode="all"
    )
    # The actual number of pages depends on the sample.pdf, but we ensure some were created
    assert len(images) > 0
    for image in images:
        assert Path(image).exists()

def test_split_pdf_first_n(clean_test_dirs):
    images = split_pdf(
        input_pdf="storage/input/sample.pdf",
        output_dir="storage/pages/test_first_n",
        processing_mode="first_n",
        max_pages=2,
    )
    assert len(images) == 2
    for image in images:
        assert Path(image).exists()

def test_split_pdf_page_range(clean_test_dirs):
    images = split_pdf(
        input_pdf="storage/input/sample.pdf",
        output_dir="storage/pages/test_range",
        processing_mode="page_range",
        start_page=3,
        end_page=5,
    )
    # Pages 3, 4, 5 (inclusive) is 3 pages
    assert len(images) == 3
    for image in images:
        assert Path(image).exists()