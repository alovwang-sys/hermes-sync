"""Local-folder remote backend.

The local backend is the reference backend for the harness. It stores objects
as metadata plus content under a user-selected folder and records tombstones
separately so deletes are explicit.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict

from .base import RemoteObject, RemoteObjectMetadata

_SAFE_ID = re.compile(r"^[A-Za-z0-9_.-]+$")


class LocalFolderBackend:
    def __init__(self, root: Path | str):
        self.root = Path(root)

    def list_objects(self) -> list[RemoteObjectMetadata]:
        objects_root = self._safe_path("objects")
        if not objects_root.exists():
            return []
        result: list[RemoteObjectMetadata] = []
        for metadata_path in sorted(objects_root.glob("*/*/metadata.json")):
            try:
                metadata = self._read_metadata(metadata_path)
            except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError):
                continue
            if metadata.tombstone or self._tombstone_path(metadata.scope, metadata.object_id).exists():
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
        object_dir = self._object_dir(metadata.scope, metadata.object_id)
        object_dir.mkdir(parents=True, exist_ok=True)
        content_path = object_dir / "content"
        metadata_path = object_dir / "metadata.json"
        tombstone_path = self._tombstone_path(metadata.scope, metadata.object_id)
        if tombstone_path.exists():
            tombstone_path.unlink()
        self._write_bytes_if_changed(content_path, content)
        self._write_json_if_changed(metadata_path, metadata.as_dict())
        return metadata

    def download_object(self, scope: str, object_id: str) -> RemoteObject:
        metadata_path = self._object_dir(scope, object_id) / "metadata.json"
        metadata = self._read_metadata(metadata_path)
        if metadata.tombstone or self._tombstone_path(scope, object_id).exists():
            raise FileNotFoundError(f"remote object is tombstoned: {scope}/{object_id}")
        content = (metadata_path.parent / "content").read_bytes()
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
        path = self._tombstone_path(tombstone.scope, tombstone.object_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._write_json_if_changed(path, tombstone.as_dict())
        return tombstone

    def list_tombstones(self) -> list[RemoteObjectMetadata]:
        tombstone_root = self._safe_path("tombstones")
        if not tombstone_root.exists():
            return []
        result: list[RemoteObjectMetadata] = []
        for metadata_path in sorted(tombstone_root.glob("*/*.json")):
            try:
                result.append(self._read_metadata(metadata_path))
            except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError):
                continue
        return result

    def _object_dir(self, scope: str, object_id: str) -> Path:
        return self._safe_path("objects", self._safe_segment(scope), self._safe_segment(object_id))

    def _tombstone_path(self, scope: str, object_id: str) -> Path:
        return self._safe_path(
            "tombstones",
            self._safe_segment(scope),
            self._safe_segment(object_id) + ".json",
        )

    def _safe_path(self, *parts: str) -> Path:
        root = self.root.resolve()
        path = root.joinpath(*parts)
        resolved = path.resolve(strict=False)
        try:
            resolved.relative_to(root)
        except ValueError as exc:
            raise ValueError(f"remote path escapes root: {path}") from exc
        return path

    @staticmethod
    def _safe_segment(value: str) -> str:
        if not value or not _SAFE_ID.match(value):
            raise ValueError(f"unsafe remote object identifier: {value!r}")
        return value

    @staticmethod
    def _read_metadata(path: Path) -> RemoteObjectMetadata:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("remote metadata must be a JSON object")
        return RemoteObjectMetadata.from_dict(data)

    @staticmethod
    def _write_json_if_changed(path: Path, data: Dict[str, Any]) -> None:
        payload = json.dumps(data, indent=2, sort_keys=True) + "\n"
        if path.exists() and path.read_text(encoding="utf-8") == payload:
            return
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(payload, encoding="utf-8")
        tmp.replace(path)

    @staticmethod
    def _write_bytes_if_changed(path: Path, data: bytes) -> None:
        if path.exists() and path.read_bytes() == data:
            return
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_bytes(data)
        tmp.replace(path)
