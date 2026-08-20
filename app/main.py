"""τrend multi-user FastAPI entrypoint."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.config import get_settings
from app.db import init_db

settings = get_settings()

app = FastAPI(title=settings.app_name, version="0.2.0-multi")


@app.on_event("startup")
def _startup() -> None:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    (settings.data_dir / "photos").mkdir(parents=True, exist_ok=True)
    init_db()


@app.get("/api/health")
def health() -> JSONResponse:
    return JSONResponse(
        {
            "ok": True,
            "service": "trend-multi",
            "app": settings.app_name,
            "database": settings.database_url.split("://", 1)[0],
            "port": settings.port,
            "auth": "pending",  # B2
            "xai_configured": bool(settings.xai_api_key),
        }
    )


# Static UI (single-user frontend for now; auth gates come in B2)
if settings.public_dir.is_dir():
    app.mount("/img", StaticFiles(directory=str(settings.public_dir / "img")), name="img")

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(settings.public_dir / "index.html")

    # Common root static files
    @app.get("/favicon.ico")
    def favicon_ico() -> FileResponse:
        return FileResponse(settings.public_dir / "favicon.ico")

    @app.get("/favicon.svg")
    def favicon_svg() -> FileResponse:
        return FileResponse(settings.public_dir / "favicon.svg")

    @app.get("/favicon-32.png")
    def favicon_32() -> FileResponse:
        return FileResponse(settings.public_dir / "favicon-32.png")

    @app.get("/favicon-48.png")
    def favicon_48() -> FileResponse:
        return FileResponse(settings.public_dir / "favicon-48.png")

    @app.get("/apple-touch-icon.png")
    def apple_touch() -> FileResponse:
        return FileResponse(settings.public_dir / "apple-touch-icon.png")

    @app.get("/style.css")
    def style_css() -> FileResponse:
        return FileResponse(settings.public_dir / "style.css")

    @app.get("/app.js")
    def app_js() -> FileResponse:
        return FileResponse(settings.public_dir / "app.js")

    @app.get("/bf_axis.js")
    def bf_axis_js() -> FileResponse:
        return FileResponse(settings.public_dir / "bf_axis.js")

    @app.get("/photos-ui.js")
    def photos_ui_js() -> FileResponse:
        return FileResponse(settings.public_dir / "photos-ui.js")
