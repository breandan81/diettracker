"""τrend multi-user FastAPI entrypoint."""

from __future__ import annotations

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
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
        }
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
        return FileResponse(settings.public_dir / "index.html")

    @app.get("/login.html")
    def login_page():
        return FileResponse(settings.public_dir / "login.html")

    @app.get("/admin")
    @app.get("/admin.html")
    def admin_page(request: Request):
        if not request.session.get("user_id"):
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

    @app.get("/style.css")
    def style_css():
        return FileResponse(settings.public_dir / "style.css")

    @app.get("/app.js")
    def app_js():
        return FileResponse(settings.public_dir / "app.js")

    @app.get("/bf_axis.js")
    def bf_axis_js():
        return FileResponse(settings.public_dir / "bf_axis.js")

    @app.get("/photos-ui.js")
    def photos_ui_js():
        return FileResponse(settings.public_dir / "photos-ui.js")
