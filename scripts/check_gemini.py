#!/usr/bin/env python3
"""
Simple local Gemini API health check.

Loads GEMINI_API_KEY or GEMINI_API_KEYS from .env and for each key tries a
lightweight metadata request (list_models). Prints pass/fail per key and exits
non-zero if any key fails.

Usage:
  python scripts/check_gemini.py

Optional:
  python scripts/check_gemini.py --verbose
"""

import os
import sys
import argparse
from typing import List

from dotenv import load_dotenv
from google import genai  # google-genai unified SDK


def mask_key(key: str) -> str:
    if not key:
        return "<empty>"
    return f"{key[:4]}...{key[-4:]}" if len(key) > 8 else "****"


def get_keys_from_env() -> List[str]:
    keys_env = os.getenv("GEMINI_API_KEYS", "").strip()
    if keys_env:
        return [k.strip() for k in keys_env.split(",") if k.strip()]
    single = os.getenv("GEMINI_API_KEY", "").strip()
    return [single] if single else []


def check_key(key: str, verbose: bool = False) -> tuple[bool, str]:
    try:
        client = genai.Client(api_key=key)
        models = list(client.models.list())
        if verbose:
            names = ", ".join(getattr(m, 'name', '<unknown>') for m in models[:5])
            if len(models) > 5:
                names += ", ..."
            print(f"  models returned: {len(models)} [sample: {names}]")
        return (len(models) > 0), "ok"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


def main() -> int:
    parser = argparse.ArgumentParser(description="Check Gemini API connectivity using local .env")
    parser.add_argument("--verbose", action="store_true", help="Print extra details")
    args = parser.parse_args()

    load_dotenv()  # load .env from current directory if present

    keys = get_keys_from_env()
    if not keys:
        print("No GEMINI_API_KEY(S) found in environment (.env).", file=sys.stderr)
        return 2

    all_ok = True
    print("Gemini API health check:\n")
    for idx, key in enumerate(keys, start=1):
        print(f"[{idx}/{len(keys)}] Key {mask_key(key)} -> ", end="", flush=True)
        ok, msg = check_key(key, verbose=args.verbose)
        if ok:
            print("PASS")
        else:
            all_ok = False
            print(f"FAIL ({msg})")

    print("\nSummary: " + ("ALL PASS" if all_ok else "SOME FAILURES"))
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
