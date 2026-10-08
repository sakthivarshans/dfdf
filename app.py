from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from api.records_routes import router as records_router
from api.routes import router
from app_db.database import init_db
from config.settings import API_DESCRIPTION, API_TITLE, API_VERSION
from services.ocr_service import ocr_service


@asynccontextmanager
async def lifespan(app: FastAPI):

    print("\n" + "=" * 80)
    print("APPLICATION STARTING")
    print("=" * 80)

    init_db()
    ocr_service.initialize()

    print("Application Ready")
    print("=" * 80 + "\n")

    yield

    print("\n" + "=" * 80)
    print("APPLICATION SHUTDOWN")
    print("=" * 80)


app = FastAPI(
    title=API_TITLE,
    description=API_DESCRIPTION,
    version=API_VERSION,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)
app.include_router(records_router)


@app.get("/")
async def root():

    return {
        "message": API_TITLE,
        "docs": "/docs",
    }


# ── frontend ──────────────────────────────────────────────────────────────────

_frontend_html = Path(__file__).resolve().parent / "frontend" / "index.html"


@app.get("/ui")
async def serve_ui():
    if _frontend_html.exists():
        return FileResponse(str(_frontend_html), media_type="text/html")
    raise HTTPException(status_code=404, detail="Frontend not found")