"""τrend multi-user FastAPI entrypoint."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from app.config import get_settings
from app.db import init_db
from app.routers import admin as admin_router
from app.routers import auth as auth_router
from app.routers import data as data_router

settings = get_settings()

app = FastAPI(title=settings.app_name, version="0.2.0-multi")
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.session_secret,
    session_cookie="trend_session",
    same_site="lax",
    https_only=False,
)

app.include_router(auth_router.router)
app.include_router(data_router.router)
app.include_router(admin_router.router)

# Files to omit from the public ESP firmware zip (secrets / build junk)
_ESP_ZIP_SKIP = {
    "config.h",
    "test_scale_session",
}
_ESP_ZIP_SKIP_SUFFIXES = {".o", ".elf", ".bin", ".pyc"}


def _build_esp_firmware_zip() -> bytes:
    root = Path(__file__).resolve().parents[1]
    sketch = root / "esp32" / "renpho_to_diettracker"
    readme = root / "esp32" / "README.md"
    if not sketch.is_dir():
        raise FileNotFoundError("esp32/renpho_to_diettracker missing")

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        if readme.is_file():
            zf.write(readme, arcname="esp32/README.md")
        for path in sorted(sketch.rglob("*")):
            if not path.is_file():
                continue
            if path.name in _ESP_ZIP_SKIP or path.suffix in _ESP_ZIP_SKIP_SUFFIXES:
                continue
            if path.name.endswith("~") or path.name.startswith("."):
                continue
            rel = path.relative_to(sketch.parent)  # renpho_to_diettracker/...
            zf.write(path, arcname=f"esp32/{rel.as_posix()}")
    return buf.getvalue()


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
            "auth": "session",
            "google_oauth": bool(settings.google_client_id and settings.google_client_secret),
            "xai_configured": bool(settings.xai_api_key),
            "smtp_configured": bool(
                (settings.smtp_host or "").strip()
                and (settings.smtp_from or "").strip()
                and (settings.public_base_url or "").strip()
            ),
            "public_base_url": (settings.public_base_url or "").rstrip("/") or None,
        }
    )


@app.get("/api/esp/firmware.zip")
def esp_firmware_zip():
    """Download Renpho BLE → τrend ESP32 sketch (no secrets)."""
    try:
        data = _build_esp_firmware_zip()
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    return StreamingResponse(
        io.BytesIO(data),
        media_type="application/zip",
        headers={
            "Content-Disposition": 'attachment; filename="trend-esp32-renpho.zip"',
        },
    )


@app.exception_handler(HTTPException)
async def http_exc_handler(request: Request, exc: HTTPException):
    if exc.status_code == 401:
        accept = request.headers.get("accept", "")
        if "text/html" in accept and not request.url.path.startswith("/api/"):
            return RedirectResponse("/login.html")
        return JSONResponse({"error": exc.detail, "detail": exc.detail}, status_code=401)
    return JSONResponse({"error": exc.detail, "detail": exc.detail}, status_code=exc.status_code)


if settings.public_dir.is_dir():
    app.mount("/img", StaticFiles(directory=str(settings.public_dir / "img")), name="img")
    # Serve remaining public assets under /static and individual known paths
    app.mount("/static", StaticFiles(directory=str(settings.public_dir)), name="static")

    @app.get("/")
    def index(request: Request):
        if not request.session.get("user_id"):
            return RedirectResponse("/login.html")
        return FileResponse(
            settings.public_dir / "index.html",
            headers={"Cache-Control": "no-cache, must-revalidate"},
        )

    @app.get("/login.html")
    def login_page():
        return FileResponse(settings.public_dir / "login.html")

    @app.get("/about")
    @app.get("/about.html")
    def about_page():
        return FileResponse(settings.public_dir / "about.html")

    @app.get("/admin")
    @app.get("/admin.html")
    def admin_page(request: Request):
        uid = request.session.get("user_id")
        if not uid:
            return RedirectResponse("/login.html")
        # HTML shell is admin-only — APIs already enforce require_admin
        try:
            if int(uid) not in settings.admin_ids:
                return RedirectResponse("/")
        except (TypeError, ValueError):
            return RedirectResponse("/login.html")
        return FileResponse(settings.public_dir / "admin.html")

    @app.get("/favicon.ico")
    def favicon_ico():
        return FileResponse(settings.public_dir / "favicon.ico")

    @app.get("/favicon.svg")
    def favicon_svg():
        return FileResponse(settings.public_dir / "favicon.svg")

    @app.get("/favicon-32.png")
    def favicon_32():
        return FileResponse(settings.public_dir / "favicon-32.png")

    @app.get("/favicon-48.png")
    def favicon_48():
        return FileResponse(settings.public_dir / "favicon-48.png")

    @app.get("/apple-touch-icon.png")
    def apple_touch():
        return FileResponse(settings.public_dir / "apple-touch-icon.png")

    _no_cache = {"Cache-Control": "no-cache, must-revalidate"}

    @app.get("/style.css")
    def style_css():
        return FileResponse(settings.public_dir / "style.css", headers=_no_cache)

    @app.get("/app.js")
    def app_js():
        return FileResponse(settings.public_dir / "app.js", headers=_no_cache)

    @app.get("/bf_axis.js")
    def bf_axis_js():
        return FileResponse(settings.public_dir / "bf_axis.js", headers=_no_cache)

    @app.get("/photos-ui.js")
    def photos_ui_js():
        return FileResponse(settings.public_dir / "photos-ui.js", headers=_no_cache)
