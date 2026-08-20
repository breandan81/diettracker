"""Auth routes: email/password + Google OAuth stubs."""

from __future__ import annotations

from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth_utils import hash_password, verify_password
from app.config import get_settings
from app.db import get_db
from app.deps import get_current_user, get_optional_user
from app.models import User

router = APIRouter(prefix="/api/auth", tags=["auth"])
settings = get_settings()


class RegisterBody(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=72)
    name: str | None = None


class LoginBody(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=72)


def _user_public(user: User) -> dict:
    return {
        "id": user.id,
        "email": user.email,
        "name": user.name,
        "is_admin": user.id in settings.admin_ids,
    }


@router.post("/register")
def register(body: RegisterBody, request: Request, db: Session = Depends(get_db)):
    email = body.email.lower().strip()
    existing = db.scalar(select(User).where(User.email == email))
    if existing:
        raise HTTPException(status_code=400, detail="Email already registered")
    user = User(
        email=email,
        password_hash=hash_password(body.password),
        name=(body.name or "").strip() or None,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    request.session["user_id"] = user.id
    return {"ok": True, "user": _user_public(user)}


@router.post("/login")
def login(body: LoginBody, request: Request, db: Session = Depends(get_db)):
    email = body.email.lower().strip()
    user = db.scalar(select(User).where(User.email == email))
    if not user or not user.password_hash or not verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    if not user.is_active:
        raise HTTPException(status_code=403, detail="Account disabled")
    request.session["user_id"] = user.id
    return {"ok": True, "user": _user_public(user)}


@router.post("/logout")
def logout(request: Request):
    request.session.clear()
    return {"ok": True}


@router.get("/me")
def me(user: User | None = Depends(get_optional_user)):
    if not user:
        return {"user": None}
    return {"user": _user_public(user)}


@router.get("/google/start")
def google_start(request: Request):
    if not settings.google_client_id or not settings.google_client_secret:
        raise HTTPException(
            status_code=501,
            detail="Google OAuth not configured (set GOOGLE_CLIENT_ID/SECRET)",
        )
    # CSRF state
    import secrets

    state = secrets.token_urlsafe(16)
    request.session["oauth_state"] = state
    params = {
        "client_id": settings.google_client_id,
        "redirect_uri": settings.google_redirect_uri,
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        "access_type": "online",
        "prompt": "select_account",
    }
    url = "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode(params)
    return RedirectResponse(url)


@router.get("/google/callback")
async def google_callback(request: Request, db: Session = Depends(get_db)):
    if not settings.google_client_id or not settings.google_client_secret:
        raise HTTPException(status_code=501, detail="Google OAuth not configured")
    q = request.query_params
    if q.get("error"):
        raise HTTPException(status_code=400, detail=q.get("error"))
    state = q.get("state")
    if not state or state != request.session.get("oauth_state"):
        raise HTTPException(status_code=400, detail="Invalid OAuth state")
    code = q.get("code")
    if not code:
        raise HTTPException(status_code=400, detail="Missing code")

    async with httpx.AsyncClient(timeout=30) as client:
        tok = await client.post(
            "https://oauth2.googleapis.com/token",
            data={
                "code": code,
                "client_id": settings.google_client_id,
                "client_secret": settings.google_client_secret,
                "redirect_uri": settings.google_redirect_uri,
                "grant_type": "authorization_code",
            },
        )
        if tok.status_code >= 400:
            raise HTTPException(status_code=400, detail=f"Token exchange failed: {tok.text[:200]}")
        tokens = tok.json()
        access = tokens.get("access_token")
        info = await client.get(
            "https://www.googleapis.com/oauth2/v3/userinfo",
            headers={"Authorization": f"Bearer {access}"},
        )
        if info.status_code >= 400:
            raise HTTPException(status_code=400, detail="Failed to fetch Google profile")
        profile = info.json()

    sub = profile.get("sub")
    email = (profile.get("email") or "").lower().strip()
    name = profile.get("name")
    if not sub or not email:
        raise HTTPException(status_code=400, detail="Google profile incomplete")

    user = db.scalar(select(User).where(User.google_sub == sub))
    if not user:
        user = db.scalar(select(User).where(User.email == email))
        if user:
            user.google_sub = sub
            if name and not user.name:
                user.name = name
        else:
            user = User(email=email, google_sub=sub, name=name, password_hash=None)
            db.add(user)
        db.commit()
        db.refresh(user)

    if not user.is_active:
        raise HTTPException(status_code=403, detail="Account disabled")

    request.session.pop("oauth_state", None)
    request.session["user_id"] = user.id
    return RedirectResponse("/", status_code=302)
