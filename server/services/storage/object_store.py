from __future__ import annotations

"""
S3-compatible object storage wrapper used for Cloudflare R2 integration.

Provides helpers to upload/download, generate presigned URLs, and delete by
prefix. This module is intentionally small and dependency-free beyond boto3.
"""

from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Iterable, List, Optional

import boto3
from botocore.client import Config
from botocore.exceptions import ClientError


def make_s3_client(endpoint: str, key: str, secret: str, region: str = "auto"):
    """Create a low-level S3 client configured for virtual-host style v4 signing.

    For Cloudflare R2, endpoint should be like:
        https://<ACCOUNT_ID>.r2.cloudflarestorage.com
    """
    return boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=key,
        aws_secret_access_key=secret,
        region_name=region,
        config=Config(signature_version="s3v4", s3={"addressing_style": "virtual"}),
    )


@dataclass
class S3ObjectStore:
    bucket: str
    endpoint_url: str
    access_key: str
    secret_key: str
    region: str = "auto"
    presign_expires_seconds: int = 600

    def __post_init__(self) -> None:
        self._client = make_s3_client(self.endpoint_url, self.access_key, self.secret_key, self.region)

    # --- Upload / Download ---
    def upload_file(self, local_path: Path | str, key: str) -> None:
        p = Path(local_path)
        self._client.upload_file(Filename=str(p), Bucket=self.bucket, Key=key)

    def download_bytes(self, key: str) -> bytes:
        resp = self._client.get_object(Bucket=self.bucket, Key=key)
        body = resp.get("Body")
        return body.read() if body is not None else b""

    def download_file(self, key: str, dest_path: Path | str) -> None:
        p = Path(dest_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        self._client.download_file(Bucket=self.bucket, Key=key, Filename=str(p))

    # --- URL generation ---
    def generate_presigned_url(
        self,
        key: str,
        *,
        expires_in: Optional[int] = None,
        response_content_type: Optional[str] = None,
        response_content_disposition: Optional[str] = None,
    ) -> str:
        params = {"Bucket": self.bucket, "Key": key}
        if response_content_type:
            params["ResponseContentType"] = response_content_type
        if response_content_disposition:
            params["ResponseContentDisposition"] = response_content_disposition
        return self._client.generate_presigned_url(
            ClientMethod="get_object",
            Params=params,
            ExpiresIn=int(expires_in or self.presign_expires_seconds),
        )

    # --- Maintenance / Admin ---
    def delete_object(self, key: str) -> None:
        self._client.delete_object(Bucket=self.bucket, Key=key)

    def object_exists(self, key: str) -> bool:
        try:
            self._client.head_object(Bucket=self.bucket, Key=key)
            return True
        except ClientError as e:  # type: ignore[name-defined]
            code = str(getattr(e, "response", {}).get("Error", {}).get("Code", ""))
            if code in {"404", "NoSuchKey", "NotFound"}:
                return False
            # Unknown errors should bubble up — treat as exists=False to be safe
            return False

    def list_keys(self, prefix: str) -> List[str]:
        out: List[str] = []
        token: Optional[str] = None
        while True:
            kwargs = {"Bucket": self.bucket, "Prefix": prefix, "MaxKeys": 1000}
            if token:
                kwargs["ContinuationToken"] = token
            resp = self._client.list_objects_v2(**kwargs)
            for it in resp.get("Contents", []) or []:
                k = it.get("Key")
                if k:
                    out.append(str(k))
            if resp.get("IsTruncated"):
                token = resp.get("NextContinuationToken")
                if not token:
                    break
            else:
                break
        return out

    def delete_prefix(self, prefix: str) -> int:
        keys = self.list_keys(prefix)
        if not keys:
            return 0
        # Batch delete in chunks of 1000 (S3 limit)
        deleted = 0
        for i in range(0, len(keys), 1000):
            batch = keys[i : i + 1000]
            if not batch:
                continue
            self._client.delete_objects(
                Bucket=self.bucket,
                Delete={"Objects": [{"Key": k} for k in batch], "Quiet": True},
            )
            deleted += len(batch)
        return deleted

