"""RemoteBackend conformance checks for harness scenarios."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Callable

from hermes_sync.manifest import utc_now
from hermes_sync.remotes import RemoteBackend, RemoteObjectMetadata

from .sync_harness import require


BackendFactory = Callable[[], RemoteBackend]


def run_backend_conformance(
    *,
    backend_name: str,
    backend_factory: BackendFactory,
    remote_root: Path | None = None,
) -> str:
    backend = backend_factory()
    require(backend.list_objects() == [], f"{backend_name} did not start empty")
    require(backend.list_tombstones() == [], f"{backend_name} started with tombstones")

    first_content = b"backend conformance version one\n"
    first = _metadata(
        scope="artifacts",
        object_id="conformance-object",
        logical_path="artifacts/conformance.txt",
        content=first_content,
        remote_rev="rev-one",
    )
    stored_first = backend.upload_object(first, first_content)
    require(stored_first.size_bytes == len(first_content), "upload did not store content size")
    require(stored_first.tombstone is False, "upload returned tombstone metadata")
    active = backend.list_objects()
    require(len(active) == 1, "upload did not create one active object")
    require(active[0].remote_rev == "rev-one", "active object has wrong revision")
    downloaded = backend.download_object(first.scope, first.object_id)
    require(downloaded.content == first_content, "downloaded content did not match upload")
    require(downloaded.metadata.logical_path == first.logical_path, "download metadata changed logical path")

    backend.upload_object(first, first_content)
    active = backend.list_objects()
    require(len(active) == 1, "idempotent upload duplicated active objects")
    require(
        backend.download_object(first.scope, first.object_id).content == first_content,
        "idempotent upload changed content",
    )

    second_content = b"backend conformance version two\n"
    second = _metadata(
        scope="artifacts",
        object_id="conformance-object",
        logical_path="artifacts/conformance.txt",
        content=second_content,
        remote_rev="rev-two",
    )
    stored_second = backend.upload_object(second, second_content)
    require(stored_second.remote_rev == "rev-two", "replacement upload returned wrong revision")
    active = backend.list_objects()
    require(len(active) == 1, "replacement upload duplicated active objects")
    require(active[0].remote_rev == "rev-two", "replacement upload did not update active metadata")
    require(
        backend.download_object(second.scope, second.object_id).content == second_content,
        "replacement upload did not update content",
    )

    tombstone = _metadata(
        scope="artifacts",
        object_id="conformance-object",
        logical_path="artifacts/conformance.txt",
        content=b"",
        remote_rev="rev-delete",
        tombstone=True,
    )
    stored_tombstone = backend.put_tombstone(tombstone)
    require(stored_tombstone.tombstone is True, "put_tombstone did not return tombstone metadata")
    require(backend.list_objects() == [], "tombstoned object remained active")
    tombstones = backend.list_tombstones()
    require(len(tombstones) == 1, "tombstone was not listed once")
    require(tombstones[0].remote_rev == "rev-delete", "tombstone revision was not stored")
    try:
        backend.download_object("artifacts", "conformance-object")
    except FileNotFoundError:
        pass
    else:
        raise AssertionError("tombstoned object was downloadable")

    backend.put_tombstone(tombstone)
    require(len(backend.list_tombstones()) == 1, "idempotent tombstone duplicated metadata")

    third_content = b"backend conformance version three\n"
    third = _metadata(
        scope="artifacts",
        object_id="conformance-object",
        logical_path="artifacts/conformance.txt",
        content=third_content,
        remote_rev="rev-three",
    )
    backend.upload_object(third, third_content)
    require(len(backend.list_tombstones()) == 0, "reupload did not clear the tombstone")
    active = backend.list_objects()
    require(len(active) == 1 and active[0].remote_rev == "rev-three", "reupload did not restore active object")
    require(
        backend.download_object(third.scope, third.object_id).content == third_content,
        "reuploaded content was not downloadable",
    )

    _assert_unsafe_identifier_rejected(backend, remote_root)
    return f"{backend_name} backend satisfies object, tombstone, idempotency, and path-safety conformance"


def _metadata(
    *,
    scope: str,
    object_id: str,
    logical_path: str,
    content: bytes,
    remote_rev: str,
    tombstone: bool = False,
) -> RemoteObjectMetadata:
    return RemoteObjectMetadata(
        scope=scope,
        object_id=object_id,
        logical_path=logical_path,
        content_hash=hashlib.sha256(content).hexdigest(),
        remote_rev=remote_rev,
        size_bytes=len(content),
        mtime=0.0,
        updated_at=utc_now(),
        source_device_id="backend-conformance",
        tombstone=tombstone,
    )


def _assert_unsafe_identifier_rejected(
    backend: RemoteBackend,
    remote_root: Path | None,
) -> None:
    unsafe = _metadata(
        scope="../escape",
        object_id="bad",
        logical_path="artifacts/bad.txt",
        content=b"bad\n",
        remote_rev="rev-bad",
    )
    try:
        backend.upload_object(unsafe, b"bad\n")
    except (ValueError, FileNotFoundError, OSError):
        pass
    else:
        raise AssertionError("backend accepted an unsafe scope identifier")

    if remote_root is not None:
        escaped = remote_root.parent / "escape"
        require(not escaped.exists(), "unsafe backend identifier wrote outside remote root")
