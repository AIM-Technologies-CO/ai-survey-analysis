"""FastAPI server for Survey Intelligence.

Two features behind one tabbed UI:
  - Synthetic Data: AI answers extra questions as real respondents (raw Anthropic API).
  - Segmentation: an agent writes/runs pandas to build audience personas (Agent SDK).

Run from inside ``src/`` so bare absolute imports resolve.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from config import settings  # imported first: loads .env before predictor/data read env

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from routes.health import router as health_router
from routes.surveys import router as surveys_router
from routes.synth import router as synth_router
from routes.segmentation import router as segmentation_router
from utils.logging_config.logger import get_logger

logger = get_logger()

STATIC_DIR = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings.ensure_dirs()
    logger.info("Survey Intelligence starting on port %s", settings.port)
    if not settings.mongo_url:
        logger.warning("SURVEY_MONGO_URL not set — database features will error until configured")
    yield


app = FastAPI(title="Survey Intelligence", version="1.0.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

app.include_router(health_router)
app.include_router(surveys_router)
app.include_router(synth_router)
app.include_router(segmentation_router)

if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/")
def root():
    idx = STATIC_DIR / "index.html"
    if idx.exists():
        return FileResponse(str(idx))
    return {"ok": True, "hint": "static UI not found"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=settings.port)
