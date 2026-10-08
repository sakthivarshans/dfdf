from pathlib import Path
import shutil
import uuid

from fastapi import (
    APIRouter,
    File,
    Form,
    HTTPException,
    UploadFile,
)
from fastapi.responses import FileResponse

from config.settings import INPUT_DIR
from models.enums import ProcessingMode
from scripts.extract_document import extract_document

router = APIRouter()


@router.get("/health")
def health():
    return {
        "status": "healthy",
        "service": "Document Extraction API",
    }


@router.post(
    "/extract/csv",
    summary="Extract Tables from PDF",
    description="""
Upload a PDF document and merge all detected product tables into a single CSV.

### Processing Modes

- **all**
    - Process every page in the PDF.

- **first_n**
    - Process only the first N pages.
    - Requires **max_pages**.

- **page_range**
    - Process pages between **start_page** and **end_page**.
""",
)
async def extract_csv(

    file: UploadFile = File(
        ...,
        description="PDF document",
    ),

    processing_mode: ProcessingMode = Form(
        default=ProcessingMode.ALL,
        description="Select the page processing mode.",
    ),

    max_pages: int | None = Form(
        default=None,
        description="Required only for 'first_n' mode.",
        ge=1,
        example=[5],
    ),

    start_page: int | None = Form(
        default=None,
        description="Required only for 'page_range' mode.",
        ge=1,
        example=[6],
    ),

    end_page: int | None = Form(
        default=None,
        description="Required only for 'page_range' mode.",
        ge=1,
        example=[8],
    ),
):

    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(
            status_code=400,
            detail="Only PDF files are supported.",
        )

    # Validation

    if processing_mode == ProcessingMode.FIRST_N:

        if max_pages is None:

            raise HTTPException(
                status_code=400,
                detail="max_pages is required when processing_mode='first_n'.",
            )

    elif processing_mode == ProcessingMode.PAGE_RANGE:

        if start_page is None or end_page is None:

            raise HTTPException(
                status_code=400,
                detail="start_page and end_page are required when processing_mode='page_range'.",
            )

        if end_page < start_page:

            raise HTTPException(
                status_code=400,
                detail="end_page must be greater than or equal to start_page.",
            )

    # Namespace each run by a unique id. The pipeline clears and rewrites
    # storage/{pages,json}/<pdf stem> on every call, so reusing the uploaded
    # filename let two concurrent uploads of the same name wipe each other's
    # working directories — and let any upload clobber the stored data of a
    # recorded extraction that happened to share that name. Path() also
    # strips any directory part, which would otherwise escape INPUT_DIR.
    original_name = Path(file.filename).name
    input_pdf = INPUT_DIR / f"{uuid.uuid4().hex[:8]}_{original_name}"

    INPUT_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    with open(input_pdf, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    # synchronous on purpose — see the note in api/records_routes.py: Paddle's
    # GPU inference hangs if it runs on anything but this thread
    result = extract_document(
        pdf_file=input_pdf,
        processing_mode=processing_mode.value,
        max_pages=max_pages,
        start_page=start_page,
        end_page=end_page,
    )

    return FileResponse(
        path=result.merged_csv_path,
        # the on-disk name carries the uid; hand the caller back their own name
        filename=f"{Path(original_name).stem}.csv",
        media_type="text/csv",
    )