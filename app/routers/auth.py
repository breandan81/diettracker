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
from app.models import PhotoInvite, User

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _settings():
    return get_settings()


class RegisterBody(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=72)
    name: str | None = None


class LoginBody(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=72)


def _user_public(user: User, db: Session | None = None) -> dict:
    settings = _settings()
    photos_allowed = bool(getattr(user, "photos_allowed", False))
    if user.id in settings.admin_ids:
        photos_allowed = True
    out = {
        "id": user.id,
        "email": user.email,
        "name": user.name,
        "is_admin": user.id in settings.admin_ids,
        "photos_allowed": photos_allowed,
        "email_verified": bool(getattr(user, "email_verified", False)),
    }
    if db is not None:
        from app.photos_access import photos_feature_enabled, user_can_upload_photos

        out["photos_feature_enabled"] = photos_feature_enabled(db)
        out["photos_allowed"] = user_can_upload_photos(db, user)
    return out


@router.post("/register")
def register(body: RegisterBody, request: Request, db: Session = Depends(get_db)):
    """Create account and send verification email. Does not start a session until verified."""
    from app.email_verify import issue_and_send_verification
    from app.mail import SmtpNotConfigured, require_smtp, smtp_configured

    settings = _settings()
    try:
        require_smtp(settings)
    except SmtpNotConfigured as e:
        raise HTTPException(status_code=503, detail=str(e)) from e

    email = body.email.lower().strip()
    existing = db.scalar(select(User).where(User.email == email))
    if existing:
        if existing.email_verified:
            raise HTTPException(status_code=400, detail="Email already registered")
        # Allow re-register attempt on unverified account with correct-ish flow: reset password + resend
        existing.password_hash = hash_password(body.password)
        if body.name:
            existing.name = (body.name or "").strip() or existing.name
        db.commit()
        try:
            issue_and_send_verification(db, existing, settings)
        except Exception as e:
            raise HTTPException(
                status_code=502, detail=f"Account saved but verification email failed: {e}"
            ) from e
        return {
            "ok": True,
            "needs_verification": True,
            "message": "Check your email for a verification link (we re-sent it).",
            "smtp_configured": smtp_configured(settings),
        }

    user = User(
        email=email,
        password_hash=hash_password(body.password),
        name=(body.name or "").strip() or None,
        photos_allowed=False,
        email_verified=False,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    try:
        issue_and_send_verification(db, user, settings)
    except Exception as e:
        # Keep account so resend works; surface mail error
        raise HTTPException(
            status_code=502, detail=f"Account created but verification email failed: {e}"
        ) from e
    # Do not auto-login until verified
    request.session.pop("user_id", None)
    return {
        "ok": True,
        "needs_verification": True,
        "message": "Account created. Check your email for a verification link before signing in.",
        "smtp_configured": True,
    }


@router.post("/login")
def login(body: LoginBody, request: Request, db: Session = Depends(get_db)):
    email = body.email.lower().strip()
    user = db.scalar(select(User).where(User.email == email))
    if not user or not user.password_hash or not verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    if not user.is_active:
        raise HTTPException(status_code=403, detail="Account disabled")
    if not user.email_verified:
        raise HTTPException(
            status_code=403,
            detail="Email not verified. Check your inbox, or use Resend verification on the login page.",
        )
    request.session["user_id"] = user.id
    return {"ok": True, "user": _user_public(user, db)}


class ResendBody(BaseModel):
    email: EmailStr


@router.post("/resend-verification")
def resend_verification(body: ResendBody, db: Session = Depends(get_db)):
    from app.email_verify import issue_and_send_verification
    from app.mail import SmtpNotConfigured, require_smtp

    settings = _settings()
    try:
        require_smtp(settings)
    except SmtpNotConfigured as e:
        raise HTTPException(status_code=503, detail=str(e)) from e

    email = body.email.lower().strip()
    user = db.scalar(select(User).where(User.email == email))
    # Same response whether or not the account exists (avoid account enumeration)
    msg = "If that email is registered and unverified, a new link is on its way."
    if user and user.is_active and not user.email_verified and user.password_hash:
        try:
            issue_and_send_verification(db, user, settings)
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"Could not send email: {e}") from e
    return {"ok": True, "message": msg}


@router.get("/verify-email")
def verify_email(token: str, db: Session = Depends(get_db)):
    from app.email_verify import consume_verification_token

    consume_verification_token(db, (token or "").strip())
    # App-relative login so it works behind Caddy
    return RedirectResponse("/login.html?verified=1", status_code=302)


@router.post("/logout")
def logout(request: Request):
    request.session.clear()
    return {"ok": True}


@router.get("/me")
def me(
    user: User | None = Depends(get_optional_user),
    db: Session = Depends(get_db),
):
    if not user:
        return {"user": None}
    return {"user": _user_public(user, db)}


class RedeemInviteBody(BaseModel):
    code: str


@router.post("/redeem-photo-invite")
def redeem_photo_invite(
    body: RedeemInviteBody,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    from app.auth_utils import hash_token
    from app.photos_access import photos_feature_enabled

    if not photos_feature_enabled(db):
        raise HTTPException(status_code=403, detail="Photo uploads are disabled globally")
    if user.photos_allowed or user.id in _settings().admin_ids:
        return {"ok": True, "photos_allowed": True, "message": "Already allowed"}

    code = (body.code or "").strip()
    if len(code) < 8:
        raise HTTPException(status_code=400, detail="Invalid invite code")
    inv = db.scalar(select(PhotoInvite).where(PhotoInvite.code_hash == hash_token(code)))
    if not inv or inv.revoked:
        raise HTTPException(status_code=400, detail="Invalid or revoked invite code")
    if inv.uses >= inv.max_uses:
        raise HTTPException(status_code=400, detail="Invite code already used up")

    inv.uses = int(inv.uses or 0) + 1
    user.photos_allowed = True
    db.commit()
    return {
        "ok": True,
        "photos_allowed": True,
        "message": "Photo uploads unlocked for your account",
    }


@router.get("/google/start")
def google_start(request: Request):
    settings = _settings()
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
    settings = _settings()
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
            user.email_verified = True  # Google already verified the address
        else:
            user = User(
                email=email,
                google_sub=sub,
                name=name,
                password_hash=None,
                email_verified=True,
            )
            db.add(user)
        db.commit()
        db.refresh(user)
    else:
        if not user.email_verified:
            user.email_verified = True
            db.commit()

    if not user.is_active:
        raise HTTPException(status_code=403, detail="Account disabled")

    request.session.pop("oauth_state", None)
    request.session["user_id"] = user.id
    return RedirectResponse("/", status_code=302)
