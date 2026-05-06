"""WebDAV remote backend.

The backend uses a small WebDAV subset and stores the same object layout as
the local-folder reference backend: metadata plus content for active objects,
and separate tombstone metadata for deletes.
"""

from __future__ import annotations

import base64
import json
import os
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Dict

from .base import RemoteObject, RemoteObjectMetadata
from .local import _SAFE_ID


@dataclass(frozen=True)
class WebDavCredentials:
    username: str
    password: str


class WebDavBackend:
    """RemoteBackend implementation for WebDAV storage."""

    def __init__(
        self,
        *,
        base_url: str,
        prefix: str = "",
        credentials: WebDavCredentials | None = None,
        timeout_seconds: float = 30.0,
    ):
        self.base_url = base_url.rstrip("/")
        self.prefix = self._normalize_prefix(prefix)
        self.timeout_seconds = timeout_seconds
        self.credentials = credentials if credentials is not None else self._env_credentials()

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
        try:
            payload = self._request("PROPFIND", prefix, headers={"Depth": "infinity"})
        except FileNotFoundError:
            return []
        keys = []
        for href in self._parse_propfind_hrefs(payload):
            key = self._key_from_href(href)
            if key and key.startswith(prefix):
                keys.append(key)
        return sorted(set(keys))

    def _put_key(self, key: str, data: bytes, *, content_type: str) -> None:
        self._ensure_parent_collections(key)
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
            raise ValueError("WebDAV metadata must be a JSON object")
        return RemoteObjectMetadata.from_dict(data)

    def _ensure_parent_collections(self, key: str) -> None:
        parts = [part for part in key.split("/") if part]
        for index in range(1, len(parts)):
            self._mkcol("/".join(parts[:index]))

    def _mkcol(self, key: str) -> None:
        try:
            self._request("MKCOL", key)
        except urllib.error.HTTPError as exc:
            if exc.code in {405, 409}:
                return
            raise

    def _request(
        self,
        method: str,
        key: str,
        *,
        body: bytes = b"",
        headers: Dict[str, str] | None = None,
    ) -> bytes:
        request_headers = dict(headers or {})
        if self.credentials is not None:
            token = f"{self.credentials.username}:{self.credentials.password}".encode("utf-8")
            request_headers["Authorization"] = "Basic " + base64.b64encode(token).decode("ascii")
        data = body if method in {"PUT", "POST", "PROPFIND"} else None
        request = urllib.request.Request(
            self._url_for_key(key),
            data=data,
            headers=request_headers,
            method=method,
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                return response.read()
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                raise FileNotFoundError(key) from exc
            raise

    def _url_for_key(self, key: str) -> str:
        parsed = urllib.parse.urlsplit(self.base_url)
        if not parsed.scheme or not parsed.netloc:
            raise ValueError(f"invalid WebDAV URL: {self.base_url!r}")
        quoted_key = urllib.parse.quote(key.strip("/"), safe="/-_.~")
        path = "/".join(part for part in (parsed.path.strip("/"), quoted_key) if part)
        return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, "/" + path if path else "/", "", ""))

    def _key_from_href(self, href: str) -> str | None:
        parsed_base = urllib.parse.urlsplit(self.base_url)
        parsed_href = urllib.parse.urlsplit(href)
        base_path = parsed_base.path.strip("/")
        href_path = parsed_href.path.strip("/")
        if base_path:
            if href_path == base_path:
                return ""
            prefix = base_path + "/"
            if not href_path.startswith(prefix):
                return None
            href_path = href_path[len(prefix):]
        return urllib.parse.unquote(href_path)

    @staticmethod
    def _parse_propfind_hrefs(payload: bytes) -> list[str]:
        root = ET.fromstring(payload)
        return [element.text or "" for element in root.iter() if element.tag.endswith("href")]

    @staticmethod
    def _env_credentials() -> WebDavCredentials | None:
        username = os.environ.get("HERMES_SYNC_WEBDAV_USERNAME")
        password = os.environ.get("HERMES_SYNC_WEBDAV_PASSWORD")
        if not username or not password:
            return None
        return WebDavCredentials(username=username, password=password)

    @staticmethod
    def _safe_segment(value: str) -> str:
        if not value or not _SAFE_ID.match(value):
            raise ValueError(f"unsafe remote object identifier: {value!r}")
        return value

    @staticmethod
    def _normalize_prefix(value: str) -> str:
        prefix = value.strip("/")
        if not prefix:
            return ""
        for part in prefix.split("/"):
            if not part or part in {".", ".."}:
                raise ValueError(f"unsafe WebDAV prefix: {value!r}")
        return prefix
