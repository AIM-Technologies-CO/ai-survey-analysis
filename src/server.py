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

from mcp_app import mcp, build_mcp_asgi_app
from routes.health import router as health_router
from routes.surveys import router as surveys_router
from routes.synth import router as synth_router
from routes.segmentation import router as segmentation_router
from routes.views import router as views_router
from utils.logging_config.logger import get_logger

logger = get_logger()

STATIC_DIR = Path(__file__).parent / "static"

mcp_asgi = build_mcp_asgi_app()  # import time: creates the session manager run below


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings.ensure_dirs()
    logger.info("Survey Intelligence starting on port %s", settings.port)
    if not settings.mongo_url:
        logger.warning("SURVEY_MONGO_URL not set — database features will error until configured")
    if not settings.mcp_auth_token:
        logger.warning("MCP_AUTH_TOKEN not set — /mcp/ endpoint is unauthenticated")
    # a mounted sub-app's lifespan never runs, so drive the MCP session manager here
    async with mcp.session_manager.run():
        yield


app = FastAPI(title="Survey Intelligence", version="1.0.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

app.include_router(health_router)
app.include_router(surveys_router)
app.include_router(synth_router)
app.include_router(segmentation_router)
app.include_router(views_router)

app.mount("/mcp", mcp_asgi)  # canonical endpoint /mcp/ (trailing slash)

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
