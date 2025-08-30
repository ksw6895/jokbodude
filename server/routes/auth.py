import os
from typing import Optional

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response, Form, Query

from ..utils import delete_path_contents
from ..core import celery_app
from ..core import REDIS_URL
from storage_manager import StorageManager  # type: ignore
from ..auth import (
    create_jwt,
    verify_jwt,
    validate_google_id_token,
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


def _admin_emails() -> set[str]:
    raw = os.getenv("ADMIN_EMAILS", "").strip()
    if not raw:
        return set()
    return {e.strip().lower() for e in raw.split(",") if e.strip()}


def _is_admin_email(email: Optional[str]) -> bool:
    return (email or "").strip().lower() in _admin_emails()


def require_admin(user: dict = Depends(lambda session=Cookie(None): get_current_user(session))):  # type: ignore
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")
    if not _is_admin_email(user.get("email")):
        raise HTTPException(status_code=403, detail="Admin privileges required")
    return user


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
        "admin_login_via_google": bool(_admin_emails()),
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
    # Allow admins regardless of tester allowlist
    is_admin = _is_admin_email(email)
    # Tester allowlist: union of env ALLOWED_TESTERS and Redis-managed list
    allowed_by_env = {e.strip().lower() for e in os.getenv("ALLOWED_TESTERS", "").split(",") if e.strip()}
    try:
        sm: StorageManager = request.app.state.storage_manager
        allowed_by_dyn = set(sm.list_testers())
    except Exception:
        allowed_by_dyn = set()
    if not (is_admin or (email.lower() in allowed_by_env) or (email.lower() in allowed_by_dyn)):
        raise HTTPException(status_code=403, detail="Tester not allowed")
    user = _issue_session(response, sub, email, name)
    # Ensure an initial token grant exists
    try:
        sm: StorageManager = request.app.state.storage_manager
        # Save/update profile for admin management
        try:
            sm.save_user_profile(sub, email, name)
        except Exception:
            pass
        bal = sm.get_user_tokens(sub)
        if bal is None:
            initial = max(0, int(os.getenv("CBT_TOKENS_INITIAL", "200")))
            sm.set_user_tokens(sub, initial)
    except Exception:
        pass
    return {"ok": True, "user": {**user, "admin": is_admin}}


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
    # Dev login respects env allowlist only (for safety)
    allowed_by_env = {e.strip().lower() for e in os.getenv("ALLOWED_TESTERS", "").split(",") if e.strip()}
    if email.strip().lower() not in allowed_by_env:
        raise HTTPException(status_code=403, detail="Tester not allowed (dev)")
    # Use email as user_id surrogate
    user = _issue_session(response, email, email, email)
    try:
        sm: StorageManager = request.app.state.storage_manager
        try:
            sm.save_user_profile(email, email, email)
        except Exception:
            pass
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
    info = {"authenticated": True, "user_id": user.get("sub"), "email": user.get("email"), "name": user.get("name"), "admin": _is_admin_email(user.get("email"))}
    try:
        sm: StorageManager = request.app.state.storage_manager
        bal = sm.get_user_tokens(user.get("sub"))
        info["tokens"] = bal if bal is not None else None
    except Exception:
        info["tokens"] = None
    return info


def _is_admin_or_password(user: Optional[dict], password: Optional[str]) -> bool:
    if user and _is_admin_email(user.get("email")):
        return True
    admin_pw = os.getenv("ADMIN_PASSWORD", "")
    if admin_pw and (password or "") == admin_pw:
        return True
    return False


@router.post("/admin/users/{user_id}/tokens")
def set_tokens(request: Request, user_id: str, amount: int, password: Optional[str] = None, user=Depends(get_current_user)):
    if not _is_admin_or_password(user, password):
        raise HTTPException(status_code=403, detail="Admin required")
    sm: StorageManager = request.app.state.storage_manager
    ok = sm.set_user_tokens(user_id, amount)
    if not ok:
        raise HTTPException(status_code=500, detail="Failed to set tokens")
    return {"user_id": user_id, "tokens": sm.get_user_tokens(user_id)}


@router.post("/admin/users/{user_id}/tokens/add")
def add_tokens(request: Request, user_id: str, delta: int, password: Optional[str] = None, user=Depends(get_current_user)):
    if not _is_admin_or_password(user, password):
        raise HTTPException(status_code=403, detail="Admin required")
    sm: StorageManager = request.app.state.storage_manager
    new_val = sm.add_user_tokens(user_id, delta)
    if new_val is None:
        raise HTTPException(status_code=500, detail="Failed to add tokens")
    return {"user_id": user_id, "tokens": new_val}


# --- Admin: testers allowlist management ---

@router.get("/admin/testers")
def list_testers(request: Request, user=Depends(require_admin)):
    sm: StorageManager = request.app.state.storage_manager
    dynamic = sm.list_testers()
    env_list = [e.strip().lower() for e in os.getenv("ALLOWED_TESTERS", "").split(",") if e.strip()]
    return {"dynamic": dynamic, "env": sorted(set(env_list)), "effective": sorted(set(dynamic) | set(env_list))}


@router.post("/admin/testers")
def add_tester(request: Request, email: str = Form(...), user=Depends(require_admin)):
    sm: StorageManager = request.app.state.storage_manager
    if not sm.add_tester(email):
        raise HTTPException(status_code=400, detail="Failed to add tester")
    return {"ok": True, "testers": sm.list_testers()}


@router.delete("/admin/testers/{email}")
def delete_tester(request: Request, email: str, user=Depends(require_admin)):
    sm: StorageManager = request.app.state.storage_manager
    if not sm.remove_tester(email):
        raise HTTPException(status_code=400, detail="Failed to remove tester")
    return {"ok": True, "testers": sm.list_testers()}


# --- Admin: users listing ---

@router.get("/admin/users")
def list_users(request: Request, q: Optional[str] = Query(None), limit: int = Query(200, ge=1, le=1000), user=Depends(require_admin)):
    sm: StorageManager = request.app.state.storage_manager
    if q:
        ids = sm.find_user_ids_by_email(q)
        out = []
        for uid in ids:
            prof = sm.get_user_profile(uid) or {"user_id": uid, "email": q}
            prof["tokens"] = sm.get_user_tokens(uid)
            out.append(prof)
        return {"users": out}
    return {"users": sm.list_users(limit=limit)}
