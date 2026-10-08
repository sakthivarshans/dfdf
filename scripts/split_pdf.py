from pathlib import Path
import pymupdf as fitz

def split_pdf(
    input_pdf: str | Path,
    output_dir: str | Path,
    processing_mode: str = "all",
    max_pages: int | None = None,
    start_page: int | None = None,
    end_page: int | None = None,
    dpi: int = 300,
) -> list[Path]:
    """
    Split a PDF into PNG images.

    Args:
        input_pdf: Path to PDF
        output_dir: Output directory
        processing_mode: 'all', 'first_n', or 'page_range'
        max_pages: Process only first N pages (for 'first_n' mode)
        start_page: Start page inclusive (for 'page_range' mode)
        end_page: End page inclusive (for 'page_range' mode)
        dpi: Image DPI

    Returns:
        List of generated image paths
    """

    input_pdf = Path(input_pdf)

    if not input_pdf.exists():
        raise FileNotFoundError(f"PDF not found: {input_pdf}")

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    doc = fitz.open(input_pdf)
    total_pages = len(doc)

    # Determine pages to process
    page_indices = []

    if processing_mode == "all":
        page_indices = list(range(total_pages))
    elif processing_mode == "first_n":
        if not max_pages or max_pages <= 0:
            raise ValueError("max_pages must be > 0 for first_n mode")
        pages_to_process = min(max_pages, total_pages)
        page_indices = list(range(pages_to_process))
    elif processing_mode == "page_range":
        if not start_page or not end_page:
            raise ValueError("start_page and end_page are required for page_range mode")
        if start_page < 1:
            raise ValueError("start_page must be >= 1")
        if end_page < start_page:
            raise ValueError("end_page must be >= start_page")
            
        start_idx = max(0, start_page - 1)
        end_idx = min(total_pages - 1, end_page - 1)
        
        if start_idx <= end_idx:
            page_indices = list(range(start_idx, end_idx + 1))
    else:
        raise ValueError(f"Invalid processing_mode: {processing_mode}")

    zoom = dpi / 72
    matrix = fitz.Matrix(zoom, zoom)

    image_paths = []

    print(f"PDF : {input_pdf.name}")
    print(f"Total Pages : {total_pages}")
    print(f"Mode : {processing_mode}")
    print(f"Processing {len(page_indices)} pages...")

    for page_index in page_indices:
        page = doc.load_page(page_index)
        pix = page.get_pixmap(matrix=matrix)
        image_path = output_dir / f"page_{page_index + 1:03d}.png"
        pix.save(image_path)
        image_paths.append(image_path)
        print(f"Saved : {image_path.name}")

    doc.close()

    return image_paths


if __name__ == "__main__":
    
    print("--- Mode: all ---")
    images = split_pdf(
        input_pdf="storage/input/sample.pdf",
        output_dir="storage/pages/sample_all",
        processing_mode="all",
        dpi=300,
    )
    print(f"Generated {len(images)} pages.\n")
    
    print("--- Mode: first_n ---")
    images = split_pdf(
        input_pdf="storage/input/sample.pdf",
        output_dir="storage/pages/sample_first_n",
        processing_mode="first_n",
        max_pages=3,
        dpi=300,
    )
    print(f"Generated {len(images)} pages.\n")
    
    print("--- Mode: page_range ---")
    images = split_pdf(
        input_pdf="storage/input/sample.pdf",
        output_dir="storage/pages/sample_range",
        processing_mode="page_range",
        start_page=2,
        end_page=4,
        dpi=300,
    )
    print(f"Generated {len(images)} pages.\n")