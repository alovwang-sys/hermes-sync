"""Push, pull, and once orchestration for the local-folder MVP."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Dict

from .manifest import (
    ensure_device,
    get_manifest_object,
    get_sync_dir,
    list_dirty_objects,
    mark_object_clean,
    revision_id,
    upsert_local_object,
    utc_now,
)
from .remotes import LocalFolderBackend, RemoteBackend, RemoteObjectMetadata
from .scopes import (
    PathSafetyError,
    ScanObject,
    scan_profile,
    validate_profile_relative_path,
)
from .session_snapshots import (
    export_session_snapshot_content,
    export_session_snapshots,
    is_session_snapshot_path,
)

SUPPORTED_SYNC_SCOPES: Dict[str, bool] = {
    "config": True,
    "sessions": True,
    "memory": False,
    "artifacts": True,
    "skills": False,
    "plugins": False,
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
    phases.append(
        {"name": "stage_outbox", "status": "completed", "objects": staging["outbox"]}
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
    actions["uploaded"] = uploaded
    phases.append({"name": "upload", "status": "completed", "objects": uploaded})
    return _result("push", profile_root, backend, actions, staging, phases)


def run_pull(profile: Path | None = None, remote_path: Path | str | None = None) -> Dict[str, Any]:
    profile_root = _profile_root(profile)
    backend = _backend_for(profile_root, remote_path)
    ensure_device(profile_root)
    actions = _empty_actions()
    staging = {"outbox": 0, "inbox": 0, "skipped": 0}
    phases: list[Dict[str, Any]] = []

    remote_objects = [
        metadata
        for metadata in backend.list_objects()
        if SUPPORTED_SYNC_SCOPES.get(metadata.scope, False)
    ]
    phases.append(
        {"name": "list_remote", "status": "completed", "objects": len(remote_objects)}
    )

    staged: list[tuple[RemoteObjectMetadata, bytes]] = []
    for metadata in remote_objects:
        if not _is_safe_stage_metadata(metadata):
            staging["skipped"] += 1
            continue
        if _has_remote_rev(profile_root, metadata):
            continue
        downloaded = backend.download_object(metadata.scope, metadata.object_id)
        if _stage_inbox(profile_root, downloaded.metadata, downloaded.content):
            staging["inbox"] += 1
        staged.append((downloaded.metadata, downloaded.content))
    actions["downloaded"] = len(staged)
    phases.append(
        {"name": "stage_inbox", "status": "completed", "objects": staging["inbox"]}
    )

    imported = 0
    for metadata, content in staged:
        import_state = _import_object(profile_root, metadata, content)
        if import_state == "imported":
            imported += 1
        if import_state in {"imported", "unchanged"}:
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
        else:
            staging["skipped"] += 1
    actions["imported"] = imported
    phases.append({"name": "import", "status": "completed", "objects": imported})
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


def make_local_backend(remote_path: Path | str) -> LocalFolderBackend:
    return LocalFolderBackend(Path(remote_path))


def _scan_export_objects(profile: Path) -> tuple[list[ScanObject], Dict[str, bytes]]:
    scan = scan_profile(profile, scopes=SUPPORTED_SYNC_SCOPES)
    objects = list(scan.objects)
    session_contents: Dict[str, bytes] = {}
    if SUPPORTED_SYNC_SCOPES.get("sessions", False):
        for export in export_session_snapshots(profile):
            objects.append(export.scan_object)
            session_contents[export.scan_object.object_id] = export.content
    return sorted(objects, key=lambda obj: (obj.scope, obj.logical_path)), session_contents


def _profile_root(profile: Path | None) -> Path:
    if profile is not None:
        return Path(profile)
    from .manifest import get_hermes_home

    return get_hermes_home()


def _backend_for(profile: Path, remote_path: Path | str | None) -> RemoteBackend:
    sync_config = _load_sync_config(profile)
    remote_kind = str(sync_config.get("remote") or "local")
    if remote_kind != "local":
        raise SyncConfigurationError(f"unsupported sync remote: {remote_kind}")
    configured_path = remote_path or sync_config.get("remote_path")
    if not configured_path:
        raise SyncConfigurationError("sync.remote_path is required for local-folder sync")
    return make_local_backend(Path(str(configured_path)))


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
            if key.strip() in {"remote", "remote_path"}:
                parsed = value.strip().strip("'\"")
                result[key.strip()] = "" if parsed.lower() in {"null", "none"} else parsed
    except OSError:
        return {}
    return result


def _read_export_content(profile: Path, obj: ScanObject) -> bytes | None:
    if obj.scope == "sessions":
        if not is_session_snapshot_path(obj.logical_path):
            return None
        return export_session_snapshot_content(profile, obj.object_id)
    try:
        path = validate_profile_relative_path(profile, obj.logical_path)
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


def _has_remote_rev(profile: Path, metadata: RemoteObjectMetadata) -> bool:
    row = get_manifest_object(profile, metadata.scope, metadata.object_id)
    return bool(row and row.get("remote_rev") == metadata.remote_rev and not row.get("dirty"))


def _import_object(profile: Path, metadata: RemoteObjectMetadata, content: bytes) -> str:
    if metadata.scope == "sessions":
        return _import_session_snapshot(profile, metadata, content)
    try:
        target = validate_profile_relative_path(profile, metadata.logical_path)
    except PathSafetyError:
        return "skipped"
    if target.exists():
        try:
            existing_content = target.read_bytes()
        except OSError:
            return "skipped"
        if existing_content == content:
            return "unchanged"
        row = get_manifest_object(profile, metadata.scope, metadata.object_id)
        if row is None or row.get("dirty"):
            return "skipped"
        existing_hash = hashlib.sha256(existing_content).hexdigest()
        if existing_hash != row.get("content_hash"):
            return "skipped"
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".sync-tmp")
    tmp.write_bytes(content)
    tmp.replace(target)
    return "imported"


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
