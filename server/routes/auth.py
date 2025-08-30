import os
from typing import Optional

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response, Form

from ..utils import delete_path_contents
from ..core import celery_app
from ..core import REDIS_URL
from storage_manager import StorageManager  # type: ignore
from ..auth import (
    create_jwt,
    verify_jwt,
    validate_google_id_token,
    is_tester_allowed,
)

router = APIRouter()


def _auth_secret() -> str:
    v = os.getenv("AUTH_SECRET_KEY", "")
    if not v:
        raise HTTPException(status_code=500, detail="AUTH_SECRET_KEY not configured")
    return v


def _issue_session(resp: Response, user_id: str, email: str, name: Optional[str] = None) -> dict:
    payload = {"sub": user_id, "email": email, "name": name or email}
    token = create_jwt(payload, _auth_secret(), expires_in=int(os.getenv("SESSION_EXPIRES_SECONDS", "604800")))
    resp.set_cookie(
        key="session",
        value=token,
        httponly=True,
        secure=os.getenv("COOKIE_SECURE", "false").lower() in ("1", "true", "yes"),
        samesite=os.getenv("COOKIE_SAMESITE", "Lax"),
        max_age=int(os.getenv("SESSION_EXPIRES_SECONDS", "604800")),
        path="/",
    )
    return {"user_id": user_id, "email": email, "name": name or email}


def get_current_user(session: Optional[str] = Cookie(None)) -> Optional[dict]:
    if not session:
        return None
    payload = verify_jwt(session, _auth_secret())
    return payload


def require_user(session: Optional[str] = Cookie(None)) -> dict:
    """FastAPI dependency that enforces an authenticated session.

    Returns the decoded user payload when present, otherwise raises 401.
    """
    payload = get_current_user(session)
    if not payload:
        raise HTTPException(status_code=401, detail="Authentication required")
    return payload


@router.get("/auth/config")
def auth_config():
    try:
        flash_cost = max(0, int(os.getenv("FLASH_TOKENS_PER_CHUNK", "1")))
    except Exception:
        flash_cost = 1
    try:
        pro_cost = max(0, int(os.getenv("PRO_TOKENS_PER_CHUNK", "4")))
    except Exception:
        pro_cost = 4
    try:
        initial_tokens = max(0, int(os.getenv("CBT_TOKENS_INITIAL", "200")))
    except Exception:
        initial_tokens = 200
    return {
        "enabled": True,
        "client_id": os.getenv("GOOGLE_OAUTH_CLIENT_ID", ""),
        "allow_dev_login": os.getenv("ALLOW_DEV_LOGIN", "false").lower() in ("1", "true", "yes"),
        "feedback_form_url": os.getenv("FEEDBACK_FORM_URL", ""),
        "tokens_enabled": True,
        "token_costs": {"flash": flash_cost, "pro": pro_cost},
        "initial_tokens": initial_tokens,
    }


@router.post("/auth/google")
def auth_google(request: Request, response: Response, id_token: str = Form(...)):
    payload = validate_google_id_token(id_token)
    if not payload:
        raise HTTPException(status_code=401, detail="Invalid Google token")
    email = str(payload.get("email", "")).strip()
    sub = str(payload.get("sub", "")).strip() or email
    name = (payload.get("name") or email) if isinstance(payload.get("name"), str) else email
    if not email:
        raise HTTPException(status_code=401, detail="Missing email in token")
    if not is_tester_allowed(email):
        raise HTTPException(status_code=403, detail="Tester not allowed")
    user = _issue_session(response, sub, email, name)
    # Ensure an initial token grant exists
    try:
        sm = request.app.state.storage_manager
        bal = sm.get_user_tokens(sub)
        if bal is None:
            initial = max(0, int(os.getenv("CBT_TOKENS_INITIAL", "200")))
            sm.set_user_tokens(sub, initial)
    except Exception:
        pass
    return {"ok": True, "user": user}


@router.post("/auth/dev-login")
def dev_login(
    request: Request,
    response: Response,
    email: str = Form(...),
    password: Optional[str] = Form(None),
):
    if os.getenv("ALLOW_DEV_LOGIN", "false").lower() not in ("1", "true", "yes"):
        raise HTTPException(status_code=403, detail="Dev login disabled")
    admin_pw = os.getenv("ADMIN_PASSWORD", "")
    if not admin_pw or (password or "") != admin_pw:
        raise HTTPException(status_code=403, detail="Invalid dev password")
    if not is_tester_allowed(email):
        raise HTTPException(status_code=403, detail="Tester not allowed")
    # Use email as user_id surrogate
    user = _issue_session(response, email, email, email)
    try:
        sm = request.app.state.storage_manager
        bal = sm.get_user_tokens(email)
        if bal is None:
            initial = max(0, int(os.getenv("CBT_TOKENS_INITIAL", "200")))
            sm.set_user_tokens(email, initial)
    except Exception:
        pass
    return {"ok": True, "user": user}


@router.post("/auth/logout")
def auth_logout(response: Response):
    response.delete_cookie("session", path="/")
    return {"ok": True}


@router.get("/me")
def me(request: Request, user=Depends(get_current_user)):
    if not user:
        return {"authenticated": False}
    info = {"authenticated": True, "user_id": user.get("sub"), "email": user.get("email"), "name": user.get("name")}
    try:
        sm: StorageManager = request.app.state.storage_manager
        bal = sm.get_user_tokens(user.get("sub"))
        info["tokens"] = bal if bal is not None else None
    except Exception:
        info["tokens"] = None
    return info


@router.post("/admin/users/{user_id}/tokens")
def set_tokens(request: Request, user_id: str, amount: int, password: Optional[str] = None):
    admin_pw = os.getenv("ADMIN_PASSWORD", "")
    if not admin_pw or (password or "") != admin_pw:
        raise HTTPException(status_code=403, detail="Invalid admin password")
    sm: StorageManager = request.app.state.storage_manager
    ok = sm.set_user_tokens(user_id, amount)
    if not ok:
        raise HTTPException(status_code=500, detail="Failed to set tokens")
    return {"user_id": user_id, "tokens": sm.get_user_tokens(user_id)}


@router.post("/admin/users/{user_id}/tokens/add")
def add_tokens(request: Request, user_id: str, delta: int, password: Optional[str] = None):
    admin_pw = os.getenv("ADMIN_PASSWORD", "")
    if not admin_pw or (password or "") != admin_pw:
        raise HTTPException(status_code=403, detail="Invalid admin password")
    sm: StorageManager = request.app.state.storage_manager
    new_val = sm.add_user_tokens(user_id, delta)
    if new_val is None:
        raise HTTPException(status_code=500, detail="Failed to add tokens")
    return {"user_id": user_id, "tokens": new_val}
