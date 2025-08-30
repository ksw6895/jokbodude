import base64
import hmac
import hashlib
import json
import os
import time
from typing import Any, Dict, Optional, Tuple


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _b64url_decode(data: str) -> bytes:
    pad = 4 - (len(data) % 4)
    if pad and pad < 4:
        data += "=" * pad
    return base64.urlsafe_b64decode(data.encode())


def create_jwt(payload: Dict[str, Any], secret: str, expires_in: int = 86400) -> str:
    header = {"alg": "HS256", "typ": "JWT"}
    now = int(time.time())
    body = dict(payload)
    body.setdefault("iat", now)
    body.setdefault("exp", now + int(expires_in))
    h = _b64url_encode(json.dumps(header, separators=(",", ":")).encode())
    p = _b64url_encode(json.dumps(body, separators=(",", ":")).encode())
    signing_input = f"{h}.{p}".encode()
    sig = hmac.new(secret.encode(), signing_input, hashlib.sha256).digest()
    s = _b64url_encode(sig)
    return f"{h}.{p}.{s}"


def verify_jwt(token: str, secret: str) -> Optional[Dict[str, Any]]:
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return None
        h_b, p_b, s_b = parts
        signing_input = f"{h_b}.{p_b}".encode()
        expected = hmac.new(secret.encode(), signing_input, hashlib.sha256).digest()
        sig = _b64url_decode(s_b)
        # constant-time compare
        if not hmac.compare_digest(expected, sig):
            return None
        payload = json.loads(_b64url_decode(p_b))
        exp = int(payload.get("exp", 0))
        if exp and int(time.time()) > exp:
            return None
        return payload
    except Exception:
        return None


def parse_unverified_jwt(token: str) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    try:
        h_b, p_b, *_ = token.split(".")
        header = json.loads(_b64url_decode(h_b))
        payload = json.loads(_b64url_decode(p_b))
        return header, payload
    except Exception:
        return None, None


def validate_google_id_token(id_token: str) -> Optional[Dict[str, Any]]:
    """Best-effort validation for Google ID token without external deps.

    In production, you should verify the signature using Google's JWKS.
    For CBT/dev, we validate claims only when ALLOW_UNVERIFIED_GOOGLE_TOKENS is true
    or when strict verification is disabled.
    """
    _, payload = parse_unverified_jwt(id_token)
    if not payload:
        return None
    client_id = os.getenv("GOOGLE_OAUTH_CLIENT_ID", "").strip()
    allow_unverified = os.getenv("ALLOW_UNVERIFIED_GOOGLE_TOKENS", "false").lower() in ("1", "true", "yes")
    iss_ok = str(payload.get("iss", "")) in ("https://accounts.google.com", "accounts.google.com")
    aud_ok = (not client_id) or (payload.get("aud") == client_id)
    email_verified = bool(payload.get("email_verified", False))
    if not allow_unverified:
        # Without network key verification, we require at least client_id configured and claims ok
        if not client_id:
            return None
        if not (iss_ok and aud_ok and email_verified):
            return None
    return payload


def testers_allowlist() -> Optional[set[str]]:
    raw = os.getenv("ALLOWED_TESTERS", "").strip()
    if not raw:
        return None
    return {e.strip().lower() for e in raw.split(",") if e.strip()}


def is_tester_allowed(email: str) -> bool:
    allow = testers_allowlist()
    if not allow:
        return True
    return email.lower() in allow

