"""Generic S3-compatible remote backend.

This backend covers standard S3-compatible object storage, including services
such as AWS S3 and Cloudflare R2. It stores the same object layout as the
local-folder reference backend.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import hmac
import os
import urllib.parse
from dataclasses import dataclass
from typing import Dict

from .local import _SAFE_ID
from .oss import OssBackend


@dataclass(frozen=True)
class S3Credentials:
    access_key_id: str
    access_key_secret: str
    security_token: str | None = None


class S3CompatibleBackend(OssBackend):
    """RemoteBackend implementation for generic S3-compatible storage."""

    def __init__(
        self,
        *,
        bucket: str,
        endpoint: str,
        prefix: str = "",
        region: str = "us-east-1",
        credentials: S3Credentials | None = None,
        unsigned: bool = False,
        path_style: bool = False,
        timeout_seconds: float = 30.0,
        service: str = "s3",
    ):
        self.bucket = self._safe_bucket(bucket)
        self.endpoint = endpoint.rstrip("/")
        self.prefix = self._normalize_prefix(prefix)
        self.region = region
        self.unsigned = unsigned
        self.path_style = path_style
        self.timeout_seconds = timeout_seconds
        self.service = service
        self.credentials = credentials if credentials is not None else self._env_credentials()
        if not self.unsigned and self.credentials is None:
            raise ValueError(
                "S3 credentials are required; set AWS_ACCESS_KEY_ID "
                "and AWS_SECRET_ACCESS_KEY"
            )

    def _auth_headers(
        self,
        method: str,
        url: str,
        query: Dict[str, str],
        body: bytes,
        request_headers: Dict[str, str],
    ) -> Dict[str, str]:
        payload_hash = hashlib.sha256(body).hexdigest()
        if self.unsigned:
            return {"x-amz-content-sha256": payload_hash}
        assert self.credentials is not None
        now = _dt.datetime.now(_dt.timezone.utc)
        amz_date = now.strftime("%Y%m%dT%H%M%SZ")
        datestamp = now.strftime("%Y%m%d")
        parsed = urllib.parse.urlsplit(url)
        headers = {
            "host": parsed.netloc,
            "x-amz-content-sha256": payload_hash,
            "x-amz-date": amz_date,
        }
        if self.credentials.security_token:
            headers["x-amz-security-token"] = self.credentials.security_token
        canonical_headers, signed_headers = self._canonical_headers(headers)
        canonical_request = "\n".join(
            [
                method,
                urllib.parse.quote(parsed.path or "/", safe="/-_.~"),
                self._canonical_query(query),
                canonical_headers,
                signed_headers,
                payload_hash,
            ]
        )
        scope = f"{datestamp}/{self.region}/{self.service}/aws4_request"
        string_to_sign = "\n".join(
            [
                "AWS4-HMAC-SHA256",
                amz_date,
                scope,
                hashlib.sha256(canonical_request.encode("utf-8")).hexdigest(),
            ]
        )
        signing_key = self._signing_key(self.credentials.access_key_secret, datestamp)
        signature = hmac.new(signing_key, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()
        headers["Authorization"] = (
            "AWS4-HMAC-SHA256 "
            f"Credential={self.credentials.access_key_id}/{scope}, "
            f"SignedHeaders={signed_headers}, Signature={signature}"
        )
        return {**request_headers, **headers}

    @staticmethod
    def _env_credentials() -> S3Credentials | None:
        access_key_id = os.environ.get("AWS_ACCESS_KEY_ID")
        access_key_secret = os.environ.get("AWS_SECRET_ACCESS_KEY")
        if not access_key_id or not access_key_secret:
            return None
        return S3Credentials(
            access_key_id=access_key_id,
            access_key_secret=access_key_secret,
            security_token=os.environ.get("AWS_SESSION_TOKEN"),
        )

    @staticmethod
    def _safe_bucket(value: str) -> str:
        if not value or not _SAFE_ID.match(value):
            raise ValueError(f"unsafe S3 bucket name: {value!r}")
        return value

    @staticmethod
    def _normalize_prefix(value: str) -> str:
        prefix = value.strip("/")
        if not prefix:
            return ""
        for part in prefix.split("/"):
            if not part or part in {".", ".."}:
                raise ValueError(f"unsafe S3 prefix: {value!r}")
        return prefix
