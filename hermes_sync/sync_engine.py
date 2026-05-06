"""Push, pull, restore, and once orchestration for hermes-sync."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

from .manifest import (
    ensure_device,
    get_revision,
    get_manifest_object,
    get_sync_dir,
    list_dirty_objects,
    list_dirty_tombstones,
    list_manifest_objects,
    mark_object_merged_dirty,
    mark_object_clean,
    mark_object_deleted,
    record_conflict,
    record_revision,
    revision_id,
    tombstone_content_hash,
    tombstone_revision_id,
    upsert_local_object,
    utc_now,
)
from .remotes import (
    LocalFolderBackend,
    OssBackend,
    RemoteBackend,
    RemoteObjectMetadata,
    S3CompatibleBackend,
    WebDavBackend,
)
from .scopes import (
    PathSafetyError,
    ScanObject,
    load_configured_scopes,
    scan_profile,
    validate_scope_relative_path,
)
from .session_snapshots import (
    export_session_snapshot_content,
    export_session_snapshots,
    is_session_snapshot_path,
)

SUPPORTED_SYNC_SCOPES: Dict[str, bool] = {
    "config": True,
    "sessions": True,
    "memory": True,
    "artifacts": True,
    "skills": True,
    "plugins": True,
    "secrets": False,
}

_SECRET_KEY = re.compile(
    r"^\s*([A-Za-z0-9_.-]*(?:api_key|apikey|credential|credentials|oauth|secret|token)"
    r"[A-Za-z0-9_.-]*)\s*[:=]\s*(.*?)\s*(?:#.*)?$",
    re.IGNORECASE,
)
_DISABLED_VALUES = {"", "false", "null", "none", "[]", "{}"}
_SAFE_STAGE_ID = re.compile(r"^[A-Za-z0-9_.-]+$")


class SyncConfigurationError(ValueError):
    pass


@dataclass(frozen=True)
class ImportOutcome:
    state: str
    conflict: Dict[str, Any] | None = None
    merged: Dict[str, Any] | None = None


def run_push(profile: Path | None = None, remote_path: Path | str | None = None) -> Dict[str, Any]:
    profile_root = _profile_root(profile)
    backend = _backend_for(profile_root, remote_path)
    device = ensure_device(profile_root)
    actions = _empty_actions()
    staging = {"outbox": 0, "inbox": 0, "skipped": 0}
    phases: list[Dict[str, Any]] = []

    scan_objects, session_contents = _scan_export_objects(profile_root)
    phases.append({"name": "scan", "status": "completed", "objects": len(scan_objects)})
    for obj in scan_objects:
        content = session_contents.get(obj.object_id) if obj.scope == "sessions" else None
        if content is None:
            content = _read_export_content(profile_root, obj)
        if content is None:
            staging["skipped"] += 1
            continue
        rev = revision_id(obj.scope, obj.object_id, obj.content_hash)
        needs_upload = upsert_local_object(
            profile_root,
            scope=obj.scope,
            object_id=obj.object_id,
            logical_path=obj.logical_path,
            content_hash=obj.content_hash,
            local_rev=rev,
            mtime=obj.mtime,
            size_bytes=obj.size_bytes,
        )
        if needs_upload:
            metadata = _metadata_from_scan(device["device_id"], obj, rev)
            if _stage_outbox(profile_root, metadata, content):
                staging["outbox"] += 1
    local_deletes = _record_missing_deletes(profile_root, device["device_id"], scan_objects)
    for row in list_dirty_tombstones(profile_root):
        metadata = _metadata_from_tombstone(device["device_id"], row)
        if _stage_outbox(profile_root, metadata, b""):
            staging["outbox"] += 1
    phases.append(
        {
            "name": "stage_outbox",
            "status": "completed",
            "objects": staging["outbox"],
            "tombstones": local_deletes,
        }
    )

    uploaded = 0
    for row in list_dirty_objects(profile_root):
        staged = _read_staged_outbox(profile_root, str(row["scope"]), str(row["object_id"]))
        if staged is None:
            obj = ScanObject(
                scope=str(row["scope"]),
                object_id=str(row["object_id"]),
                logical_path=str(row["logical_path"]),
                content_hash=str(row["content_hash"]),
                size_bytes=int(row["size_bytes"] or 0),
                mtime=float(row["mtime"] or 0.0),
            )
            content = _read_export_content(profile_root, obj)
            if content is None:
                staging["skipped"] += 1
                continue
            metadata = _metadata_from_scan(device["device_id"], obj, str(row["local_rev"]))
            _stage_outbox(profile_root, metadata, content)
        else:
            metadata, content = staged
        stored = backend.upload_object(metadata, content)
        _store_version(profile_root, stored, content)
        mark_object_clean(
            profile_root,
            scope=stored.scope,
            object_id=stored.object_id,
            logical_path=stored.logical_path,
            content_hash=stored.content_hash,
            remote_rev=stored.remote_rev,
            mtime=stored.mtime,
            size_bytes=stored.size_bytes,
            source_device_id=stored.source_device_id,
        )
        uploaded += 1
    deleted = 0
    for row in list_dirty_tombstones(profile_root):
        metadata = _metadata_from_tombstone(device["device_id"], row)
        stored = backend.put_tombstone(metadata)
        mark_object_clean(
            profile_root,
            scope=stored.scope,
            object_id=stored.object_id,
            logical_path=stored.logical_path,
            content_hash=stored.content_hash,
            remote_rev=stored.remote_rev,
            mtime=stored.mtime,
            size_bytes=stored.size_bytes,
            source_device_id=stored.source_device_id,
            tombstone=True,
        )
        deleted += 1
    actions["uploaded"] = uploaded
    actions["deleted"] = deleted
    phases.append(
        {"name": "upload", "status": "completed", "objects": uploaded, "tombstones": deleted}
    )
    return _result("push", profile_root, backend, actions, staging, phases)


def run_pull(profile: Path | None = None, remote_path: Path | str | None = None) -> Dict[str, Any]:
    profile_root = _profile_root(profile)
    backend = _backend_for(profile_root, remote_path)
    ensure_device(profile_root)
    actions = _empty_actions()
    staging = {"outbox": 0, "inbox": 0, "skipped": 0}
    phases: list[Dict[str, Any]] = []
    scope_flags = load_configured_scopes(profile_root, SUPPORTED_SYNC_SCOPES)

    remote_objects = [
        metadata
        for metadata in backend.list_objects()
        if scope_flags.get(metadata.scope, False)
    ]
    remote_tombstones = [
        metadata
        for metadata in backend.list_tombstones()
        if scope_flags.get(metadata.scope, False)
    ]
    phases.append(
        {
            "name": "list_remote",
            "status": "completed",
            "objects": len(remote_objects),
            "tombstones": len(remote_tombstones),
        }
    )

    staged: list[tuple[RemoteObjectMetadata, bytes]] = []
    staged_tombstones: list[RemoteObjectMetadata] = []
    for metadata in remote_objects:
        if not _is_safe_stage_metadata(metadata):
            staging["skipped"] += 1
            continue
        if _has_remote_rev(profile_root, metadata):
            continue
        try:
            downloaded = backend.download_object(metadata.scope, metadata.object_id)
        except FileNotFoundError:
            staging["skipped"] += 1
            continue
        if _stage_inbox(profile_root, downloaded.metadata, downloaded.content):
            staging["inbox"] += 1
        staged.append((downloaded.metadata, downloaded.content))
    for metadata in remote_tombstones:
        if not _is_safe_stage_metadata(metadata):
            staging["skipped"] += 1
            continue
        if _has_remote_rev(profile_root, metadata):
            continue
        if _stage_inbox(profile_root, metadata, b""):
            staging["inbox"] += 1
        staged_tombstones.append(metadata)
    actions["downloaded"] = len(staged) + len(staged_tombstones)
    phases.append(
        {"name": "stage_inbox", "status": "completed", "objects": staging["inbox"]}
    )

    imported = 0
    deleted = 0
    for metadata in staged_tombstones:
        outcome = _import_tombstone(profile_root, metadata)
        if outcome.state in {"deleted", "unchanged", "conflicted"}:
            mark_object_clean(
                profile_root,
                scope=metadata.scope,
                object_id=metadata.object_id,
                logical_path=metadata.logical_path,
                content_hash=metadata.content_hash,
                remote_rev=metadata.remote_rev,
                mtime=metadata.mtime,
                size_bytes=metadata.size_bytes,
                source_device_id=metadata.source_device_id,
                tombstone=True,
            )
            if outcome.conflict:
                record_conflict(profile_root, **outcome.conflict)
            if outcome.state in {"deleted", "conflicted"}:
                deleted += 1
        else:
            staging["skipped"] += 1
    for metadata, content in staged:
        outcome = _import_object(profile_root, metadata, content)
        if outcome.state in {"imported", "conflicted", "merged"}:
            imported += 1
        if outcome.state in {"imported", "unchanged", "conflicted", "merged"}:
            _store_version(profile_root, metadata, content)
        if outcome.state == "merged" and outcome.merged:
            mark_object_merged_dirty(profile_root, **outcome.merged)
            continue
        if outcome.state in {"imported", "unchanged", "conflicted"}:
            mark_object_clean(
                profile_root,
                scope=metadata.scope,
                object_id=metadata.object_id,
                logical_path=metadata.logical_path,
                content_hash=metadata.content_hash,
                remote_rev=metadata.remote_rev,
                mtime=metadata.mtime,
                size_bytes=metadata.size_bytes,
                source_device_id=metadata.source_device_id,
            )
            if outcome.conflict:
                record_conflict(profile_root, **outcome.conflict)
        else:
            staging["skipped"] += 1
    actions["imported"] = imported
    actions["deleted"] = deleted
    phases.append(
        {"name": "import", "status": "completed", "objects": imported, "tombstones": deleted}
    )
    return _result("pull", profile_root, backend, actions, staging, phases)


def run_once(profile: Path | None = None, remote_path: Path | str | None = None) -> Dict[str, Any]:
    profile_root = _profile_root(profile)
    pull_result = run_pull(profile_root, remote_path)
    if pull_result.get("status") != "ok":
        return pull_result
    push_result = run_push(profile_root, remote_path)
    actions = _empty_actions()
    for key in actions:
        actions[key] = int(pull_result["actions"].get(key, 0)) + int(
            push_result["actions"].get(key, 0)
        )
    staging = {
        "outbox": int(push_result["staging"].get("outbox", 0)),
        "inbox": int(pull_result["staging"].get("inbox", 0)),
        "skipped": int(pull_result["staging"].get("skipped", 0))
        + int(push_result["staging"].get("skipped", 0)),
    }
    phases = []
    phases.extend(
        {**phase, "name": f"pull.{phase['name']}"} for phase in pull_result["phases"]
    )
    phases.extend(
        {**phase, "name": f"push.{phase['name']}"} for phase in push_result["phases"]
    )
    backend = _backend_for(profile_root, remote_path)
    return _result("once", profile_root, backend, actions, staging, phases)


def restore_version(
    profile: Path | None = None,
    *,
    object_id: str,
    version_id: str,
    scope: str | None = None,
) -> Dict[str, Any]:
    profile_root = _profile_root(profile)
    revision = get_revision(profile_root, object_id=object_id, revision=version_id, scope=scope)
    if revision is None:
        return {
            "status": "not_found",
            "message": "version was not found in local sync history",
            "actions": _empty_actions(),
        }
    if int(revision.get("tombstone") or 0):
        return {
            "status": "not_supported",
            "message": "restoring tombstone revisions is not implemented",
            "actions": _empty_actions(),
        }
    revision_scope = str(revision["scope"])
    if revision_scope == "sessions":
        return {
            "status": "not_supported",
            "message": "session snapshots are stored as read-only history in this phase",
            "actions": _empty_actions(),
        }
    content_path = _version_content_path(
        profile_root,
        revision_scope,
        str(revision["object_id"]),
        str(revision["revision_id"]),
    )
    if content_path is None or not content_path.exists():
        return {
            "status": "not_found",
            "message": "version content was not found in local sync history",
            "actions": _empty_actions(),
        }
    try:
        content = content_path.read_bytes()
        target = validate_scope_relative_path(
            profile_root,
            revision_scope,
            str(revision["logical_path"]),
        )
    except (OSError, PathSafetyError):
        return {
            "status": "error",
            "message": "version content could not be restored safely",
            "actions": _empty_actions(),
        }
    content_hash = hashlib.sha256(content).hexdigest()
    if content_hash != revision.get("content_hash"):
        return {
            "status": "error",
            "message": "version content hash did not match manifest history",
            "actions": _empty_actions(),
        }
    if revision_scope == "config" and _contains_secret_like_config(content):
        return {
            "status": "error",
            "message": "refusing to restore secret-like config content",
            "actions": _empty_actions(),
        }
    _write_bytes_atomic(target, content)
    stat = target.stat()
    rev = revision_id(revision_scope, str(revision["object_id"]), content_hash)
    upsert_local_object(
        profile_root,
        scope=revision_scope,
        object_id=str(revision["object_id"]),
        logical_path=str(revision["logical_path"]),
        content_hash=content_hash,
        local_rev=rev,
        mtime=stat.st_mtime,
        size_bytes=stat.st_size,
        reason="restore_version",
    )
    return {
        "status": "ok",
        "command": "restore",
        "profile": str(profile_root),
        "object": {
            "scope": revision_scope,
            "object_id": str(revision["object_id"]),
            "logical_path": str(revision["logical_path"]),
            "version_id": str(revision["revision_id"]),
        },
        "actions": _empty_actions(),
        "read_only": False,
    }


def make_local_backend(remote_path: Path | str) -> LocalFolderBackend:
    return LocalFolderBackend(Path(remote_path))


def make_oss_backend(sync_config: Dict[str, str]) -> OssBackend:
    bucket = sync_config.get("bucket") or sync_config.get("oss_bucket")
    endpoint = sync_config.get("endpoint") or sync_config.get("oss_endpoint")
    if not bucket:
        raise SyncConfigurationError("sync.bucket is required for OSS sync")
    if not endpoint:
        raise SyncConfigurationError("sync.endpoint is required for OSS sync")
    return OssBackend(
        bucket=bucket,
        endpoint=endpoint,
        prefix=sync_config.get("prefix") or sync_config.get("oss_prefix") or "",
        region=sync_config.get("region") or sync_config.get("oss_region") or "cn-hangzhou",
        unsigned=_config_bool(sync_config.get("unsigned"), default=False),
        path_style=_config_bool(sync_config.get("path_style"), default=False),
        timeout_seconds=_config_float(sync_config.get("timeout_seconds"), default=60.0),
        max_attempts=_config_int(sync_config.get("max_attempts"), default=4),
    )


def make_s3_backend(sync_config: Dict[str, str], *, default_region: str = "us-east-1") -> S3CompatibleBackend:
    bucket = sync_config.get("bucket") or sync_config.get("s3_bucket") or sync_config.get("r2_bucket")
    endpoint = sync_config.get("endpoint") or sync_config.get("s3_endpoint") or sync_config.get("r2_endpoint")
    if not bucket:
        raise SyncConfigurationError("sync.bucket is required for S3-compatible sync")
    if not endpoint:
        raise SyncConfigurationError("sync.endpoint is required for S3-compatible sync")
    return S3CompatibleBackend(
        bucket=bucket,
        endpoint=endpoint,
        prefix=sync_config.get("prefix") or sync_config.get("s3_prefix") or sync_config.get("r2_prefix") or "",
        region=(
            sync_config.get("region")
            or sync_config.get("s3_region")
            or sync_config.get("r2_region")
            or default_region
        ),
        unsigned=_config_bool(sync_config.get("unsigned"), default=False),
        path_style=_config_bool(sync_config.get("path_style"), default=False),
        timeout_seconds=_config_float(sync_config.get("timeout_seconds"), default=60.0),
        max_attempts=_config_int(sync_config.get("max_attempts"), default=4),
    )


def make_webdav_backend(sync_config: Dict[str, str]) -> WebDavBackend:
    base_url = sync_config.get("url") or sync_config.get("endpoint") or sync_config.get("webdav_url")
    if not base_url:
        raise SyncConfigurationError("sync.url is required for WebDAV sync")
    return WebDavBackend(
        base_url=base_url,
        prefix=sync_config.get("prefix") or sync_config.get("webdav_prefix") or "",
    )


def _scan_export_objects(profile: Path) -> tuple[list[ScanObject], Dict[str, bytes]]:
    scope_flags = load_configured_scopes(profile, SUPPORTED_SYNC_SCOPES)
    scan = scan_profile(profile, scopes=scope_flags)
    objects = list(scan.objects)
    session_contents: Dict[str, bytes] = {}
    if scope_flags.get("sessions", False):
        for export in export_session_snapshots(profile):
            objects.append(export.scan_object)
            session_contents[export.scan_object.object_id] = export.content
    return sorted(objects, key=lambda obj: (obj.scope, obj.logical_path)), session_contents


def _record_missing_deletes(profile: Path, device_id: str, scan_objects: list[ScanObject]) -> int:
    tombstone_scopes = {"config", "artifacts", "memory", "skills", "plugins"}
    current = {
        (obj.scope, obj.object_id)
        for obj in scan_objects
        if obj.scope in tombstone_scopes
    }
    marked = 0
    for row in list_manifest_objects(profile, scopes=tombstone_scopes, include_deleted=False):
        key = (str(row["scope"]), str(row["object_id"]))
        if key in current:
            continue
        try:
            target = validate_scope_relative_path(
                profile,
                str(row["scope"]),
                str(row["logical_path"]),
            )
        except PathSafetyError:
            continue
        if target.exists():
            continue
        if mark_object_deleted(
            profile,
            scope=str(row["scope"]),
            object_id=str(row["object_id"]),
            source_device_id=device_id,
        ):
            marked += 1
    return marked


def _profile_root(profile: Path | None) -> Path:
    if profile is not None:
        return Path(profile)
    from .manifest import get_hermes_home

    return get_hermes_home()


def _backend_for(profile: Path, remote_path: Path | str | None) -> RemoteBackend:
    sync_config = _load_sync_config(profile)
    remote_kind = str(sync_config.get("remote") or "local").lower().replace("_", "-")
    if remote_kind in {"local", "local-folder"}:
        configured_path = remote_path or sync_config.get("remote_path")
        if not configured_path:
            raise SyncConfigurationError("sync.remote_path is required for local-folder sync")
        return make_local_backend(Path(str(configured_path)))
    if remote_kind in {"oss", "alibaba-oss", "aliyun-oss"}:
        return make_oss_backend(sync_config)
    if remote_kind in {"s3", "s3-compatible"}:
        return make_s3_backend(sync_config)
    if remote_kind in {"r2", "cloudflare-r2"}:
        return make_s3_backend(sync_config, default_region="auto")
    if remote_kind in {"webdav", "web-dav"}:
        return make_webdav_backend(sync_config)
    raise SyncConfigurationError(f"unsupported sync remote: {remote_kind}")


def _load_sync_config(profile: Path) -> Dict[str, str]:
    config_path = profile / "config.yaml"
    if not config_path.exists():
        return {}
    result: Dict[str, str] = {}
    try:
        for line in config_path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or ":" not in stripped:
                continue
            key, value = stripped.split(":", 1)
            if key.strip() in {
                "remote",
                "remote_path",
                "bucket",
                "endpoint",
                "url",
                "prefix",
                "region",
                "oss_bucket",
                "oss_endpoint",
                "oss_prefix",
                "oss_region",
                "s3_bucket",
                "s3_endpoint",
                "s3_prefix",
                "s3_region",
                "r2_bucket",
                "r2_endpoint",
                "r2_prefix",
                "r2_region",
                "webdav_url",
                "webdav_prefix",
                "unsigned",
                "path_style",
                "timeout_seconds",
                "max_attempts",
            }:
                parsed = value.strip().strip("'\"")
                result[key.strip()] = "" if parsed.lower() in {"null", "none"} else parsed
    except OSError:
        return {}
    return result


def _config_bool(value: str | None, *, default: bool) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _config_float(value: str | None, *, default: float) -> float:
    if value is None:
        return default
    try:
        parsed = float(value)
    except ValueError:
        return default
    return parsed if parsed > 0 else default


def _config_int(value: str | None, *, default: int) -> int:
    if value is None:
        return default
    try:
        parsed = int(value)
    except ValueError:
        return default
    return parsed if parsed > 0 else default


def _read_export_content(profile: Path, obj: ScanObject) -> bytes | None:
    if obj.scope == "sessions":
        if not is_session_snapshot_path(obj.logical_path):
            return None
        return export_session_snapshot_content(profile, obj.object_id)
    try:
        path = validate_scope_relative_path(profile, obj.scope, obj.logical_path)
    except PathSafetyError:
        return None
    try:
        content = path.read_bytes()
    except OSError:
        return None
    if obj.scope == "config" and _contains_secret_like_config(content):
        return None
    return content


def _contains_secret_like_config(content: bytes) -> bool:
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        return True
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        match = _SECRET_KEY.match(line)
        if match and match.group(2).strip().lower() not in _DISABLED_VALUES:
            return True
    return False


def _metadata_from_scan(
    device_id: str,
    obj: ScanObject,
    remote_rev: str,
) -> RemoteObjectMetadata:
    return RemoteObjectMetadata(
        scope=obj.scope,
        object_id=obj.object_id,
        logical_path=obj.logical_path,
        content_hash=obj.content_hash,
        remote_rev=remote_rev,
        size_bytes=obj.size_bytes,
        mtime=obj.mtime,
        updated_at=utc_now(),
        source_device_id=device_id,
    )


def _metadata_from_tombstone(device_id: str, row: Dict[str, Any]) -> RemoteObjectMetadata:
    parent_rev = row.get("base_rev") or row.get("remote_rev") or row.get("local_rev")
    remote_rev = str(
        row.get("local_rev")
        or tombstone_revision_id(str(row["scope"]), str(row["object_id"]), str(parent_rev))
    )
    content_hash = str(row.get("content_hash") or tombstone_content_hash(str(parent_rev)))
    return RemoteObjectMetadata(
        scope=str(row["scope"]),
        object_id=str(row["object_id"]),
        logical_path=str(row["logical_path"]),
        content_hash=content_hash,
        remote_rev=remote_rev,
        size_bytes=0,
        mtime=float(row.get("mtime") or 0.0),
        updated_at=utc_now(),
        source_device_id=device_id,
        tombstone=True,
        extra={"base_rev": parent_rev},
    )


def _stage_outbox(profile: Path, metadata: RemoteObjectMetadata, content: bytes) -> bool:
    root = _stage_root(profile, "outbox")
    if root is None:
        return False
    return _stage_object(root, metadata, content)


def _stage_inbox(profile: Path, metadata: RemoteObjectMetadata, content: bytes) -> bool:
    root = _stage_root(profile, "inbox")
    if root is None:
        return False
    return _stage_object(root, metadata, content)


def _stage_root(profile: Path, stage: str) -> Path | None:
    profile_root = profile.resolve()
    root = get_sync_dir(profile) / stage
    resolved = root.resolve(strict=False)
    try:
        resolved.relative_to(profile_root)
    except ValueError:
        return None
    return root


def _stage_object(root: Path, metadata: RemoteObjectMetadata, content: bytes) -> bool:
    if not _is_safe_stage_metadata(metadata):
        return False
    object_dir = root / metadata.scope / metadata.object_id
    object_dir.mkdir(parents=True, exist_ok=True)
    metadata_path = object_dir / "metadata.json"
    content_path = object_dir / "content"
    payload = json.dumps(metadata.as_dict(), indent=2, sort_keys=True) + "\n"
    changed = False
    if not metadata_path.exists() or metadata_path.read_text(encoding="utf-8") != payload:
        tmp = metadata_path.with_suffix(metadata_path.suffix + ".tmp")
        tmp.write_text(payload, encoding="utf-8")
        tmp.replace(metadata_path)
        changed = True
    if not content_path.exists() or content_path.read_bytes() != content:
        tmp = content_path.with_suffix(content_path.suffix + ".tmp")
        tmp.write_bytes(content)
        tmp.replace(content_path)
        changed = True
    return changed


def _is_safe_stage_metadata(metadata: RemoteObjectMetadata) -> bool:
    return (
        SUPPORTED_SYNC_SCOPES.get(metadata.scope, False)
        and bool(_SAFE_STAGE_ID.match(metadata.scope))
        and bool(_SAFE_STAGE_ID.match(metadata.object_id))
    )


def _read_staged_outbox(
    profile: Path,
    scope: str,
    object_id: str,
) -> tuple[RemoteObjectMetadata, bytes] | None:
    if not _SAFE_STAGE_ID.match(scope) or not _SAFE_STAGE_ID.match(object_id):
        return None
    root = _stage_root(profile, "outbox")
    if root is None:
        return None
    object_dir = root / scope / object_id
    metadata_path = object_dir / "metadata.json"
    content_path = object_dir / "content"
    if not metadata_path.exists() or not content_path.exists():
        return None
    try:
        loaded = json.loads(metadata_path.read_text(encoding="utf-8"))
        if not isinstance(loaded, dict):
            return None
        metadata = RemoteObjectMetadata.from_dict(loaded)
        return metadata, content_path.read_bytes()
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError):
        return None


def _store_version(profile: Path, metadata: RemoteObjectMetadata, content: bytes) -> bool:
    content_path = _version_content_path(
        profile,
        metadata.scope,
        metadata.object_id,
        metadata.remote_rev,
    )
    if content_path is None:
        return False
    metadata_path = content_path.parent / "metadata.json"
    content_path.parent.mkdir(parents=True, exist_ok=True)
    changed = False
    payload = json.dumps(metadata.as_dict(), indent=2, sort_keys=True) + "\n"
    if not metadata_path.exists() or metadata_path.read_text(encoding="utf-8") != payload:
        tmp = metadata_path.with_suffix(metadata_path.suffix + ".tmp")
        tmp.write_text(payload, encoding="utf-8")
        tmp.replace(metadata_path)
        changed = True
    if not content_path.exists() or content_path.read_bytes() != content:
        tmp = content_path.with_suffix(content_path.suffix + ".tmp")
        tmp.write_bytes(content)
        tmp.replace(content_path)
        changed = True
    record_revision(
        profile,
        revision=metadata.remote_rev,
        scope=metadata.scope,
        object_id=metadata.object_id,
        logical_path=metadata.logical_path,
        content_hash=metadata.content_hash,
        local_rev=metadata.remote_rev,
        remote_rev=metadata.remote_rev,
        source_device_id=metadata.source_device_id,
        tombstone=metadata.tombstone,
    )
    return changed


def _version_content_path(
    profile: Path,
    scope: str,
    object_id: str,
    revision: str,
) -> Path | None:
    if not (
        _SAFE_STAGE_ID.match(scope)
        and _SAFE_STAGE_ID.match(object_id)
        and _SAFE_STAGE_ID.match(revision)
    ):
        return None
    sync_root = get_sync_dir(profile).resolve()
    target = sync_root / "versions" / scope / object_id / revision / "content"
    resolved = target.resolve(strict=False)
    try:
        resolved.relative_to(sync_root)
    except ValueError:
        return None
    return target


def _has_remote_rev(profile: Path, metadata: RemoteObjectMetadata) -> bool:
    row = get_manifest_object(profile, metadata.scope, metadata.object_id)
    if not row or row.get("remote_rev") != metadata.remote_rev:
        return False
    if metadata.tombstone:
        return int(row.get("deleted") or 0) == 1
    return True


def _import_object(profile: Path, metadata: RemoteObjectMetadata, content: bytes) -> ImportOutcome:
    if metadata.scope == "sessions":
        return ImportOutcome(_import_session_snapshot(profile, metadata, content))
    if hashlib.sha256(content).hexdigest() != metadata.content_hash:
        return ImportOutcome("skipped")
    try:
        target = validate_scope_relative_path(profile, metadata.scope, metadata.logical_path)
    except PathSafetyError:
        return ImportOutcome("skipped")
    if target.exists():
        try:
            existing_content = target.read_bytes()
        except OSError:
            return ImportOutcome("skipped")
        if existing_content == content:
            return ImportOutcome("unchanged")
        row = get_manifest_object(profile, metadata.scope, metadata.object_id)
        existing_hash = hashlib.sha256(existing_content).hexdigest()
        local_changed = (
            row is None
            or bool(row.get("dirty"))
            or int(row.get("deleted") or 0) != 0
            or existing_hash != row.get("content_hash")
        )
        if local_changed:
            merge = _try_automatic_merge(profile, metadata, target, row, existing_content, content)
            if merge is not None:
                merged_content, strategy = merge
                if metadata.scope == "config" and _contains_secret_like_config(merged_content):
                    merge = None
                else:
                    _store_local_version(profile, metadata, existing_content, existing_hash, target)
                    _write_bytes_atomic(target, merged_content)
                    stat = target.stat()
                    merged_hash = hashlib.sha256(merged_content).hexdigest()
                    merged_rev = revision_id(metadata.scope, metadata.object_id, merged_hash)
                    _store_local_version(profile, metadata, merged_content, merged_hash, target, rev=merged_rev)
                    if merged_content == content:
                        return ImportOutcome("imported")
                    return ImportOutcome(
                        "merged",
                        merged={
                            "scope": metadata.scope,
                            "object_id": metadata.object_id,
                            "logical_path": metadata.logical_path,
                            "content_hash": merged_hash,
                            "local_rev": merged_rev,
                            "remote_rev": metadata.remote_rev,
                            "mtime": stat.st_mtime,
                            "size_bytes": stat.st_size,
                            "reason": strategy,
                        },
                    )
            conflict = _preserve_local_conflict(
                profile,
                metadata,
                target,
                existing_content,
                existing_hash,
                row,
                strategy=_conflict_strategy(existing_content),
            )
            _write_bytes_atomic(target, content)
            return ImportOutcome("conflicted", conflict)
    _write_bytes_atomic(target, content)
    return ImportOutcome("imported")


def _import_tombstone(profile: Path, metadata: RemoteObjectMetadata) -> ImportOutcome:
    try:
        target = validate_scope_relative_path(profile, metadata.scope, metadata.logical_path)
    except PathSafetyError:
        return ImportOutcome("skipped")
    row = get_manifest_object(profile, metadata.scope, metadata.object_id)
    if not target.exists():
        return ImportOutcome("unchanged")
    if not target.is_file():
        return ImportOutcome("skipped")
    try:
        existing_content = target.read_bytes()
    except OSError:
        return ImportOutcome("skipped")
    existing_hash = hashlib.sha256(existing_content).hexdigest()
    local_changed = (
        row is None
        or bool(row.get("dirty"))
        or (int(row.get("deleted") or 0) == 0 and existing_hash != row.get("content_hash"))
    )
    if local_changed:
        conflict = _preserve_local_conflict(
            profile,
            metadata,
            target,
            existing_content,
            existing_hash,
            row,
            strategy=_conflict_strategy(existing_content),
        )
        target.unlink()
        return ImportOutcome("conflicted", conflict)
    target.unlink()
    return ImportOutcome("deleted")


def _preserve_local_conflict(
    profile: Path,
    metadata: RemoteObjectMetadata,
    target: Path,
    existing_content: bytes,
    existing_hash: str,
    row: Dict[str, Any] | None,
    *,
    strategy: str,
) -> Dict[str, Any]:
    local_rev = revision_id(metadata.scope, metadata.object_id, existing_hash)
    conflict_path = _write_conflict_copy(profile, metadata, target, existing_content)
    local_metadata = RemoteObjectMetadata(
        scope=metadata.scope,
        object_id=metadata.object_id,
        logical_path=metadata.logical_path,
        content_hash=existing_hash,
        remote_rev=local_rev,
        size_bytes=len(existing_content),
        mtime=target.stat().st_mtime if target.exists() else 0.0,
        updated_at=utc_now(),
        source_device_id=ensure_device(profile).get("device_id"),
        tombstone=False,
    )
    _store_version(profile, local_metadata, existing_content)
    return {
        "scope": metadata.scope,
        "object_id": metadata.object_id,
        "logical_path": metadata.logical_path,
        "local_rev": local_rev,
        "remote_rev": metadata.remote_rev,
        "base_rev": str(row.get("remote_rev")) if row and row.get("remote_rev") else None,
        "conflict_path": conflict_path,
        "strategy": strategy,
    }


def _try_automatic_merge(
    profile: Path,
    metadata: RemoteObjectMetadata,
    target: Path,
    row: Dict[str, Any] | None,
    local_content: bytes,
    remote_content: bytes,
) -> tuple[bytes, str] | None:
    if row is None:
        return None
    base_content = _base_content(profile, row)
    if base_content is None:
        return None

    suffix = Path(metadata.logical_path).suffix.lower()
    if metadata.scope == "config" or suffix in {".json", ".yaml", ".yml"}:
        structured = _try_structured_merge(
            metadata.logical_path,
            base_content,
            local_content,
            remote_content,
        )
        if structured is not None:
            return structured
        if suffix in {".json", ".yaml", ".yml"} or metadata.scope == "config":
            return None

    if metadata.scope == "artifacts":
        return _try_text_merge(base_content, local_content, remote_content)
    return None


def _base_content(profile: Path, row: Dict[str, Any]) -> bytes | None:
    revision = row.get("remote_rev") or row.get("base_rev")
    if not revision:
        return None
    content_path = _version_content_path(
        profile,
        str(row["scope"]),
        str(row["object_id"]),
        str(revision),
    )
    if content_path is None or not content_path.exists():
        return None
    try:
        return content_path.read_bytes()
    except OSError:
        return None


def _store_local_version(
    profile: Path,
    metadata: RemoteObjectMetadata,
    content: bytes,
    content_hash: str,
    target: Path,
    *,
    rev: str | None = None,
) -> None:
    local_rev = rev or revision_id(metadata.scope, metadata.object_id, content_hash)
    stat = target.stat()
    local_metadata = RemoteObjectMetadata(
        scope=metadata.scope,
        object_id=metadata.object_id,
        logical_path=metadata.logical_path,
        content_hash=content_hash,
        remote_rev=local_rev,
        size_bytes=len(content),
        mtime=stat.st_mtime,
        updated_at=utc_now(),
        source_device_id=ensure_device(profile).get("device_id"),
        tombstone=False,
    )
    _store_version(profile, local_metadata, content)


def _try_structured_merge(
    logical_path: str,
    base_content: bytes,
    local_content: bytes,
    remote_content: bytes,
) -> tuple[bytes, str] | None:
    suffix = Path(logical_path).suffix.lower()
    if suffix == ".json":
        try:
            base = json.loads(base_content.decode("utf-8"))
            local = json.loads(local_content.decode("utf-8"))
            remote = json.loads(remote_content.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None
        ok, merged = _merge_values(base, local, remote)
        if not ok:
            return None
        content = (json.dumps(merged, indent=2, sort_keys=True) + "\n").encode("utf-8")
        return content, "json_structured_merge"

    if suffix in {".yaml", ".yml"} or logical_path in {"config.yaml", "config.yml", "cli-config.yaml"}:
        base = _parse_simple_yaml(base_content)
        local = _parse_simple_yaml(local_content)
        remote = _parse_simple_yaml(remote_content)
        if base is _YAML_INVALID or local is _YAML_INVALID or remote is _YAML_INVALID:
            return None
        ok, merged = _merge_values(base, local, remote)
        if not ok:
            return None
        content = (_dump_simple_yaml(merged) + "\n").encode("utf-8")
        return content, "yaml_structured_merge"

    return None


_MISSING = object()


def _merge_values(base: Any, local: Any, remote: Any) -> tuple[bool, Any]:
    if local == remote:
        return True, local
    if local == base:
        return True, remote
    if remote == base:
        return True, local
    if isinstance(base, dict) and isinstance(local, dict) and isinstance(remote, dict):
        merged: dict[str, Any] = {}
        for key in sorted(set(base) | set(local) | set(remote)):
            ok, value = _merge_values(
                base.get(key, _MISSING),
                local.get(key, _MISSING),
                remote.get(key, _MISSING),
            )
            if not ok:
                return False, None
            if value is not _MISSING:
                merged[str(key)] = value
        return True, merged
    return False, None


def _try_text_merge(
    base_content: bytes,
    local_content: bytes,
    remote_content: bytes,
) -> tuple[bytes, str] | None:
    try:
        base_text = base_content.decode("utf-8")
        local_text = local_content.decode("utf-8")
        remote_text = remote_content.decode("utf-8")
    except UnicodeDecodeError:
        return None

    base_lines = base_text.splitlines(keepends=True)
    local_lines = local_text.splitlines(keepends=True)
    remote_lines = remote_text.splitlines(keepends=True)
    if len(base_lines) != len(local_lines) or len(base_lines) != len(remote_lines):
        return None

    merged: list[str] = []
    for base_line, local_line, remote_line in zip(base_lines, local_lines, remote_lines):
        if local_line == remote_line:
            merged.append(local_line)
        elif local_line == base_line:
            merged.append(remote_line)
        elif remote_line == base_line:
            merged.append(local_line)
        else:
            return None
    return "".join(merged).encode("utf-8"), "text_three_way_merge"


_YAML_INVALID = object()


def _parse_simple_yaml(content: bytes) -> Any:
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        return _YAML_INVALID
    lines: list[tuple[int, str]] = []
    for raw in text.splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        if indent != len(raw) - len(raw.lstrip()):
            return _YAML_INVALID
        lines.append((indent, raw.strip()))
    if not lines:
        return {}
    try:
        value, index = _parse_yaml_block(lines, 0, lines[0][0])
    except ValueError:
        return _YAML_INVALID
    if index != len(lines):
        return _YAML_INVALID
    return value


def _parse_yaml_block(
    lines: list[tuple[int, str]],
    index: int,
    indent: int,
) -> tuple[Any, int]:
    if index >= len(lines) or lines[index][0] < indent:
        return {}, index
    if lines[index][1].startswith("- "):
        values: list[Any] = []
        while index < len(lines):
            line_indent, stripped = lines[index]
            if line_indent != indent or not stripped.startswith("- "):
                break
            item_text = stripped[2:].strip()
            index += 1
            if item_text:
                values.append(_parse_yaml_scalar(item_text))
            elif index < len(lines) and lines[index][0] > indent:
                child, index = _parse_yaml_block(lines, index, lines[index][0])
                values.append(child)
            else:
                values.append(None)
        return values, index

    mapping: dict[str, Any] = {}
    while index < len(lines):
        line_indent, stripped = lines[index]
        if line_indent != indent or stripped.startswith("- "):
            break
        if ":" not in stripped:
            raise ValueError("invalid yaml mapping")
        key, raw_value = stripped.split(":", 1)
        key = key.strip()
        if not key:
            raise ValueError("empty yaml key")
        value_text = raw_value.strip()
        index += 1
        if value_text:
            mapping[key] = _parse_yaml_scalar(value_text)
        elif index < len(lines) and lines[index][0] > indent:
            child, index = _parse_yaml_block(lines, index, lines[index][0])
            mapping[key] = child
        else:
            mapping[key] = {}
    return mapping, index


def _parse_yaml_scalar(value: str) -> Any:
    lowered = value.lower()
    if lowered in {"true", "false"}:
        return lowered == "true"
    if lowered in {"null", "none", "~"}:
        return None
    if value.startswith(("\"", "'", "[", "{")):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value.strip("\"'")
    try:
        if "." in value:
            return float(value)
        return int(value)
    except ValueError:
        return value


def _dump_simple_yaml(value: Any, *, indent: int = 0) -> str:
    lines: list[str] = []
    prefix = " " * indent
    if isinstance(value, dict):
        for key in sorted(value):
            child = value[key]
            if isinstance(child, (dict, list)):
                lines.append(f"{prefix}{key}:")
                lines.append(_dump_simple_yaml(child, indent=indent + 2))
            else:
                lines.append(f"{prefix}{key}: {_dump_yaml_scalar(child)}")
        return "\n".join(lines)
    if isinstance(value, list):
        for item in value:
            if isinstance(item, (dict, list)):
                lines.append(f"{prefix}-")
                lines.append(_dump_simple_yaml(item, indent=indent + 2))
            else:
                lines.append(f"{prefix}- {_dump_yaml_scalar(item)}")
        return "\n".join(lines)
    return f"{prefix}{_dump_yaml_scalar(value)}"


def _dump_yaml_scalar(value: Any) -> str:
    if value is True:
        return "true"
    if value is False:
        return "false"
    if value is None:
        return "null"
    if isinstance(value, (int, float)):
        return str(value)
    text = str(value)
    if not text or any(char in text for char in ":#{}[],'\"") or text.strip() != text:
        return json.dumps(text)
    return text


def _write_conflict_copy(
    profile: Path,
    metadata: RemoteObjectMetadata,
    target: Path,
    content: bytes,
) -> str:
    profile_root = profile.resolve()
    root = get_sync_dir(profile).resolve()
    if not (_SAFE_STAGE_ID.match(metadata.scope) and _SAFE_STAGE_ID.match(metadata.object_id)):
        raise ValueError("unsafe conflict object metadata")
    conflict_dir = root / "conflicts" / metadata.scope / metadata.object_id
    filename = _conflict_filename(target.name)
    conflict_path = conflict_dir / filename
    resolved = conflict_path.resolve(strict=False)
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError("conflict path escapes sync root") from exc
    conflict_dir.mkdir(parents=True, exist_ok=True)
    _write_bytes_atomic(conflict_path, content)
    return resolved.relative_to(profile_root).as_posix()


def _conflict_filename(name: str) -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    path = Path(name)
    if path.suffix:
        return f"{path.stem}.sync-conflict-{timestamp}{path.suffix}"
    return f"{name}.sync-conflict-{timestamp}"


def _conflict_strategy(content: bytes) -> str:
    try:
        content.decode("utf-8")
    except UnicodeDecodeError:
        return "remote_wins_preserve_local_binary"
    return "remote_wins_preserve_local_text"


def _write_bytes_atomic(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".sync-tmp")
    tmp.write_bytes(content)
    tmp.replace(path)


def _import_session_snapshot(
    profile: Path,
    metadata: RemoteObjectMetadata,
    content: bytes,
) -> str:
    if not is_session_snapshot_path(metadata.logical_path):
        return "skipped"
    if hashlib.sha256(content).hexdigest() != metadata.content_hash:
        return "skipped"
    try:
        loaded = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return "skipped"
    if not isinstance(loaded, dict) or loaded.get("snapshot_schema_version") != 1:
        return "skipped"

    sync_root = get_sync_dir(profile).resolve()
    target = sync_root / "sessions" / metadata.object_id / "snapshot.json"
    resolved = target.resolve(strict=False)
    try:
        resolved.relative_to(sync_root)
    except ValueError:
        return "skipped"
    if target.exists():
        try:
            if target.read_bytes() == content:
                return "unchanged"
        except OSError:
            return "skipped"
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".sync-tmp")
    tmp.write_bytes(content)
    tmp.replace(target)
    return "imported"


def _empty_actions() -> Dict[str, int]:
    return {"uploaded": 0, "downloaded": 0, "imported": 0, "deleted": 0}


def _result(
    command: str,
    profile: Path,
    backend: RemoteBackend,
    actions: Dict[str, int],
    staging: Dict[str, int],
    phases: list[Dict[str, Any]],
) -> Dict[str, Any]:
    remote = getattr(backend, "root", None)
    return {
        "status": "ok",
        "command": command,
        "profile": str(profile),
        "remote": str(remote) if remote is not None else "unknown",
        "actions": actions,
        "staging": staging,
        "phases": phases,
        "read_only": False,
    }
