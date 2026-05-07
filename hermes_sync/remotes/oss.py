"""Alibaba Cloud OSS remote backend.

The backend uses the OSS S3-compatible API surface and stores the same object
layout as the local-folder reference backend: metadata plus content for active
objects, and separate tombstone metadata for deletes.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import hmac
import json
import os
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Dict

from .base import RemoteObject, RemoteObjectMetadata
from .local import _SAFE_ID


@dataclass(frozen=True)
class OssCredentials:
    access_key_id: str
    access_key_secret: str
    security_token: str | None = None


class OssBackend:
    """RemoteBackend implementation for Alibaba Cloud OSS.

    Live OSS usage should use virtual-hosted style with an S3-compatible
    endpoint such as `https://s3.oss-cn-hangzhou.aliyuncs.com`. The path-style
    mode exists for the local fake OSS harness only.
    """

    def __init__(
        self,
        *,
        bucket: str,
        endpoint: str,
        prefix: str = "",
        region: str = "cn-hangzhou",
        credentials: OssCredentials | None = None,
        unsigned: bool = False,
        path_style: bool = False,
        timeout_seconds: float = 60.0,
        max_attempts: int = 4,
        service: str = "s3",
    ):
        self.bucket = self._safe_bucket(bucket)
        self.endpoint = endpoint.rstrip("/")
        self.prefix = self._normalize_prefix(prefix)
        self.region = region
        self.unsigned = unsigned
        self.path_style = path_style
        self.timeout_seconds = timeout_seconds
        self.max_attempts = max(1, int(max_attempts))
        self.service = service
        self.credentials = credentials if credentials is not None else self._env_credentials()
        if not self.unsigned and self.credentials is None:
            raise ValueError(
                "OSS credentials are required; set OSS_ACCESS_KEY_ID/OSS_ACCESS_KEY_SECRET "
                "or ALIBABA_CLOUD_ACCESS_KEY_ID/ALIBABA_CLOUD_ACCESS_KEY_SECRET"
            )

    def list_objects(self) -> list[RemoteObjectMetadata]:
        tombstones = {(item.scope, item.object_id) for item in self.list_tombstones()}
        result: list[RemoteObjectMetadata] = []
        for key in self._list_keys(self._key("objects")):
            if not key.endswith("/metadata.json"):
                continue
            try:
                metadata = self._get_metadata(key)
            except (FileNotFoundError, json.JSONDecodeError, KeyError, TypeError, ValueError):
                continue
            if metadata.tombstone or (metadata.scope, metadata.object_id) in tombstones:
                continue
            result.append(metadata)
        return result

    def upload_object(
        self,
        metadata: RemoteObjectMetadata,
        content: bytes,
    ) -> RemoteObjectMetadata:
        metadata = RemoteObjectMetadata(
            scope=metadata.scope,
            object_id=metadata.object_id,
            logical_path=metadata.logical_path,
            content_hash=metadata.content_hash,
            remote_rev=metadata.remote_rev,
            size_bytes=len(content),
            mtime=metadata.mtime,
            updated_at=metadata.updated_at,
            source_device_id=metadata.source_device_id,
            tombstone=False,
            extra=metadata.extra,
        )
        self._delete_key(self._tombstone_key(metadata.scope, metadata.object_id))
        self._put_key(
            self._content_key(metadata.scope, metadata.object_id),
            content,
            content_type="application/octet-stream",
        )
        payload = json.dumps(metadata.as_dict(), indent=2, sort_keys=True).encode("utf-8") + b"\n"
        self._put_key(
            self._metadata_key(metadata.scope, metadata.object_id),
            payload,
            content_type="application/json",
        )
        return metadata

    def download_object(self, scope: str, object_id: str) -> RemoteObject:
        tombstone_key = self._tombstone_key(scope, object_id)
        try:
            self._get_metadata(tombstone_key)
        except FileNotFoundError:
            pass
        else:
            raise FileNotFoundError(f"remote object is tombstoned: {scope}/{object_id}")

        metadata = self._get_metadata(self._metadata_key(scope, object_id))
        if metadata.tombstone:
            raise FileNotFoundError(f"remote object is tombstoned: {scope}/{object_id}")
        content = self._get_key(self._content_key(scope, object_id))
        return RemoteObject(metadata=metadata, content=content)

    def put_tombstone(self, metadata: RemoteObjectMetadata) -> RemoteObjectMetadata:
        tombstone = RemoteObjectMetadata(
            scope=metadata.scope,
            object_id=metadata.object_id,
            logical_path=metadata.logical_path,
            content_hash=metadata.content_hash,
            remote_rev=metadata.remote_rev,
            size_bytes=0,
            mtime=metadata.mtime,
            updated_at=metadata.updated_at,
            source_device_id=metadata.source_device_id,
            tombstone=True,
            extra=metadata.extra,
        )
        payload = json.dumps(tombstone.as_dict(), indent=2, sort_keys=True).encode("utf-8") + b"\n"
        self._put_key(
            self._tombstone_key(tombstone.scope, tombstone.object_id),
            payload,
            content_type="application/json",
        )
        return tombstone

    def list_tombstones(self) -> list[RemoteObjectMetadata]:
        result: list[RemoteObjectMetadata] = []
        for key in self._list_keys(self._key("tombstones")):
            if not key.endswith(".json"):
                continue
            try:
                result.append(self._get_metadata(key))
            except (FileNotFoundError, json.JSONDecodeError, KeyError, TypeError, ValueError):
                continue
        return result

    def _metadata_key(self, scope: str, object_id: str) -> str:
        return self._key("objects", self._safe_segment(scope), self._safe_segment(object_id), "metadata.json")

    def _content_key(self, scope: str, object_id: str) -> str:
        return self._key("objects", self._safe_segment(scope), self._safe_segment(object_id), "content")

    def _tombstone_key(self, scope: str, object_id: str) -> str:
        return self._key("tombstones", self._safe_segment(scope), self._safe_segment(object_id) + ".json")

    def _key(self, *parts: str) -> str:
        cleaned = [part.strip("/") for part in parts if part and part.strip("/")]
        if self.prefix:
            cleaned.insert(0, self.prefix)
        return "/".join(cleaned)

    def _list_keys(self, prefix: str) -> list[str]:
        keys: list[str] = []
        continuation: str | None = None
        while True:
            query = {"list-type": "2", "prefix": prefix}
            if continuation:
                query["continuation-token"] = continuation
            payload = self._request("GET", "", query=query)
            page_keys, truncated, continuation = self._parse_list_objects(payload)
            keys.extend(page_keys)
            if not truncated:
                return sorted(keys)

    def _put_key(self, key: str, data: bytes, *, content_type: str) -> None:
        self._request("PUT", key, body=data, headers={"Content-Type": content_type})

    def _get_key(self, key: str) -> bytes:
        return self._request("GET", key)

    def _delete_key(self, key: str) -> None:
        try:
            self._request("DELETE", key)
        except FileNotFoundError:
            return

    def _get_metadata(self, key: str) -> RemoteObjectMetadata:
        data = json.loads(self._get_key(key).decode("utf-8"))
        if not isinstance(data, dict):
            raise ValueError("OSS metadata must be a JSON object")
        return RemoteObjectMetadata.from_dict(data)

    def _request(
        self,
        method: str,
        key: str,
        *,
        query: Dict[str, str] | None = None,
        body: bytes = b"",
        headers: Dict[str, str] | None = None,
    ) -> bytes:
        query = query or {}
        url = self._url_for_key(key, query)
        request_headers = dict(headers or {})
        request_headers.update(self._auth_headers(method, url, query, body, request_headers))
        data = body if method in {"PUT", "POST"} else None
        last_error: Exception | None = None
        for attempt in range(1, self.max_attempts + 1):
            request = urllib.request.Request(url, data=data, headers=request_headers, method=method)
            try:
                with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                    return response.read()
            except urllib.error.HTTPError as exc:
                if exc.code == 404:
                    raise FileNotFoundError(key) from exc
                if exc.code not in {429, 500, 502, 503, 504} or attempt == self.max_attempts:
                    raise RuntimeError(self._format_http_error(exc)) from exc
                last_error = exc
            except (TimeoutError, socket.timeout, urllib.error.URLError) as exc:
                if attempt == self.max_attempts:
                    raise RuntimeError(
                        self._format_request_error(
                            exc,
                            method=method,
                            key=key,
                            attempt=attempt,
                        )
                    ) from exc
                last_error = exc
            time.sleep(min(0.5 * (2 ** (attempt - 1)), 4.0))
        assert last_error is not None
        raise last_error

    def _format_request_error(
        self,
        exc: BaseException,
        *,
        method: str,
        key: str,
        attempt: int,
    ) -> str:
        reason = ""
        if isinstance(exc, urllib.error.URLError):
            reason = str(exc.reason)
        if not reason:
            reason = str(exc)
        if not reason:
            reason = type(exc).__name__
        parsed = urllib.parse.urlsplit(self.endpoint)
        host = parsed.netloc or self.endpoint
        return (
            f"OSS request failed after {attempt} attempt(s): "
            f"{type(exc).__name__}: {reason}; method={method}; "
            f"bucket={self.bucket}; endpoint={host}; key={key or '<bucket-root>'}"
        )

    @staticmethod
    def _format_http_error(exc: urllib.error.HTTPError) -> str:
        try:
            body = exc.read(4096)
        except Exception:
            body = b""
        code = ""
        message = ""
        request_id = ""
        if body:
            try:
                root = ET.fromstring(body)
                code = next((item.text or "" for item in root.iter() if item.tag.endswith("Code")), "")
                message = next((item.text or "" for item in root.iter() if item.tag.endswith("Message")), "")
                request_id = next((item.text or "" for item in root.iter() if item.tag.endswith("RequestId")), "")
            except ET.ParseError:
                message = body.decode("utf-8", errors="replace").strip()[:300]
        parts = [f"OSS HTTP {exc.code}"]
        if code:
            parts.append(code)
        if message:
            parts.append(message)
        if request_id:
            parts.append(f"RequestId={request_id}")
        return ": ".join(parts)

    def _url_for_key(self, key: str, query: Dict[str, str]) -> str:
        parsed = urllib.parse.urlsplit(self.endpoint)
        if not parsed.scheme or not parsed.netloc:
            raise ValueError(f"invalid OSS endpoint: {self.endpoint!r}")
        quoted_key = urllib.parse.quote(key, safe="/-_.~")
        if self.path_style:
            path = "/".join(part for part in (parsed.path.strip("/"), self.bucket, quoted_key) if part)
            netloc = parsed.netloc
        else:
            path = "/".join(part for part in (parsed.path.strip("/"), quoted_key) if part)
            netloc = f"{self.bucket}.{parsed.netloc}"
        encoded_query = self._canonical_query(query)
        return urllib.parse.urlunsplit((parsed.scheme, netloc, "/" + path if path else "/", encoded_query, ""))

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
            return {"x-amz-content-sha256": payload_hash, "x-oss-s3-compat": "true"}
        assert self.credentials is not None
        now = _dt.datetime.now(_dt.timezone.utc)
        amz_date = now.strftime("%Y%m%dT%H%M%SZ")
        datestamp = now.strftime("%Y%m%d")
        parsed = urllib.parse.urlsplit(url)
        headers = {
            "host": parsed.netloc,
            "x-amz-content-sha256": payload_hash,
            "x-amz-date": amz_date,
            "x-oss-s3-compat": "true",
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

    def _signing_key(self, secret: str, datestamp: str) -> bytes:
        date_key = hmac.new(("AWS4" + secret).encode("utf-8"), datestamp.encode("utf-8"), hashlib.sha256).digest()
        region_key = hmac.new(date_key, self.region.encode("utf-8"), hashlib.sha256).digest()
        service_key = hmac.new(region_key, self.service.encode("utf-8"), hashlib.sha256).digest()
        return hmac.new(service_key, b"aws4_request", hashlib.sha256).digest()

    @staticmethod
    def _canonical_headers(headers: Dict[str, str]) -> tuple[str, str]:
        normalized = {key.lower(): " ".join(value.strip().split()) for key, value in headers.items()}
        signed = sorted(normalized)
        canonical = "".join(f"{key}:{normalized[key]}\n" for key in signed)
        return canonical, ";".join(signed)

    @staticmethod
    def _canonical_query(query: Dict[str, str]) -> str:
        parts = []
        for key, value in sorted(query.items()):
            parts.append(
                urllib.parse.quote(str(key), safe="-_.~")
                + "="
                + urllib.parse.quote(str(value), safe="-_.~")
            )
        return "&".join(parts)

    @staticmethod
    def _parse_list_objects(payload: bytes) -> tuple[list[str], bool, str | None]:
        root = ET.fromstring(payload)
        keys = [element.text or "" for element in root.iter() if element.tag.endswith("Key")]
        truncated_text = next((element.text or "" for element in root.iter() if element.tag.endswith("IsTruncated")), "")
        continuation = next(
            (element.text or "" for element in root.iter() if element.tag.endswith("NextContinuationToken")),
            "",
        )
        return [key for key in keys if key], truncated_text.lower() == "true", continuation or None

    @staticmethod
    def _env_credentials() -> OssCredentials | None:
        access_key_id = _clean_env_secret(
            os.environ.get("ALIBABA_CLOUD_ACCESS_KEY_ID") or os.environ.get("OSS_ACCESS_KEY_ID")
        )
        access_key_secret = _clean_env_secret(
            os.environ.get("ALIBABA_CLOUD_ACCESS_KEY_SECRET") or os.environ.get("OSS_ACCESS_KEY_SECRET")
        )
        if not access_key_id or not access_key_secret:
            return None
        return OssCredentials(
            access_key_id=access_key_id,
            access_key_secret=access_key_secret,
            security_token=_clean_env_secret(
                os.environ.get("ALIBABA_CLOUD_SECURITY_TOKEN") or os.environ.get("OSS_SECURITY_TOKEN")
            ),
        )

    @staticmethod
    def _safe_segment(value: str) -> str:
        if not value or not _SAFE_ID.match(value):
            raise ValueError(f"unsafe remote object identifier: {value!r}")
        return value

    @staticmethod
    def _safe_bucket(value: str) -> str:
        if not value or not _SAFE_ID.match(value):
            raise ValueError(f"unsafe OSS bucket name: {value!r}")
        return value

    @staticmethod
    def _normalize_prefix(value: str) -> str:
        prefix = value.strip("/")
        if not prefix:
            return ""
        for part in prefix.split("/"):
            if not part or part in {".", ".."}:
                raise ValueError(f"unsafe OSS prefix: {value!r}")
        return prefix


def _clean_env_secret(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    return cleaned or None
