"""Small remote backend protocol used by the sync engine."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Protocol


@dataclass(frozen=True)
class RemoteObjectMetadata:
    scope: str
    object_id: str
    logical_path: str
    content_hash: str
    remote_rev: str
    size_bytes: int
    mtime: float
    updated_at: str
    source_device_id: str | None = None
    tombstone: bool = False
    extra: Dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "scope": self.scope,
            "object_id": self.object_id,
            "logical_path": self.logical_path,
            "content_hash": self.content_hash,
            "remote_rev": self.remote_rev,
            "size_bytes": self.size_bytes,
            "mtime": self.mtime,
            "updated_at": self.updated_at,
            "source_device_id": self.source_device_id,
            "tombstone": self.tombstone,
            "extra": dict(self.extra),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "RemoteObjectMetadata":
        return cls(
            scope=str(data["scope"]),
            object_id=str(data["object_id"]),
            logical_path=str(data["logical_path"]),
            content_hash=str(data["content_hash"]),
            remote_rev=str(data["remote_rev"]),
            size_bytes=int(data.get("size_bytes") or 0),
            mtime=float(data.get("mtime") or 0.0),
            updated_at=str(data["updated_at"]),
            source_device_id=(
                str(data["source_device_id"]) if data.get("source_device_id") else None
            ),
            tombstone=bool(data.get("tombstone", False)),
            extra=dict(data.get("extra") or {}),
        )


@dataclass(frozen=True)
class RemoteObject:
    metadata: RemoteObjectMetadata
    content: bytes


class RemoteBackend(Protocol):
    def list_objects(self) -> list[RemoteObjectMetadata]:
        """Return active remote objects."""

    def upload_object(
        self,
        metadata: RemoteObjectMetadata,
        content: bytes,
    ) -> RemoteObjectMetadata:
        """Upload or replace one object and return the stored metadata."""

    def download_object(self, scope: str, object_id: str) -> RemoteObject:
        """Download one active remote object."""

    def put_tombstone(self, metadata: RemoteObjectMetadata) -> RemoteObjectMetadata:
        """Record an explicit delete marker without silently removing history."""

    def list_tombstones(self) -> list[RemoteObjectMetadata]:
        """Return remote tombstones."""
