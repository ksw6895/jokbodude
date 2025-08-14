#!/usr/bin/env python3
"""
Poll task status and list/download results from the API using stdlib only.

Usage:
  python scripts/poll_status.py --base http://localhost:8000 --task <TASK_ID> --job <JOB_ID> [--timeout 180] [--download]
"""
import argparse
import json
import sys
import time
import urllib.request
from urllib.error import URLError, HTTPError


def http_get_json(url: str):
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = resp.read()
        return json.loads(data.decode("utf-8"))


def http_get_bytes(url: str):
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=20) as resp:
        return resp.read()


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--base", default="http://localhost:8000")
    p.add_argument("--task", required=True)
    p.add_argument("--job", required=True)
    p.add_argument("--timeout", type=int, default=180)
    p.add_argument("--download", action="store_true")
    args = p.parse_args()

    base = args.base.rstrip("/")
    status_url = f"{base}/status/{args.task}"
    t0 = time.time()

    print(f"Polling {status_url} ...")
    last_status = None
    while True:
        try:
            st = http_get_json(status_url)
        except (URLError, HTTPError) as e:
            print(f"Status check error: {e}")
            st = {"status": "UNKNOWN"}
        status = st.get("status")
        if status != last_status:
            print(f"Status: {status}")
            last_status = status
        if status in ("SUCCESS", "FAILURE"):
            break
        if time.time() - t0 > args.timeout:
            print("Timed out waiting for task to complete.")
            sys.exit(1)
        time.sleep(2)

    # List results
    list_url = f"{base}/results/{args.job}"
    try:
        listing = http_get_json(list_url)
    except (URLError, HTTPError) as e:
        print(f"Failed to list results: {e}")
        sys.exit(1)

    files = listing.get("files", [])
    print(f"Result files: {files}")
    if args.download and files:
        fname = files[0]
        dl_url = f"{base}/result/{args.job}/{fname}"
        content = http_get_bytes(dl_url)
        out_path = f"output/{fname}"
        import os
        os.makedirs("output", exist_ok=True)
        with open(out_path, "wb") as f:
            f.write(content)
        print(f"Downloaded: {out_path}")


if __name__ == "__main__":
    main()

