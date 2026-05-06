"""Continuous sync scheduling state for hermes-sync."""

from __future__ import annotations

import json
import os
import shlex
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from .manifest import get_hermes_home, get_sync_dir, utc_now
from .sync_engine import SyncConfigurationError, run_once
from .scopes import PathSafetyError, scan_profile, validate_profile_relative_path

STATE_SCHEMA_VERSION = 1
POLL_SCOPES = {
    "config": True,
    "sessions": False,
    "memory": False,
    "artifacts": True,
    "skills": False,
    "plugins": False,
    "secrets": False,
}
ARTIFACT_ROOTS = {"artifacts", "outputs", "reports"}
TOOL_PATH_KEYS = ("path", "file_path", "output_path", "target_path")


def scheduler_state(profile: Path | None = None) -> dict[str, Any]:
    path = _state_path(_profile_root(profile))
    if not path.exists():
        return _default_state()
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _default_state()
    if not isinstance(loaded, dict):
        return _default_state()
    state = _default_state()
    state.update(loaded)
    state["paused"] = bool(state.get("paused"))
    pending = state.get("pending_sessions")
    state["pending_sessions"] = pending if isinstance(pending, list) else []
    pending_artifacts = state.get("pending_artifacts")
    state["pending_artifacts"] = pending_artifacts if isinstance(pending_artifacts, list) else []
    pending_reasons = state.get("pending_reasons")
    state["pending_reasons"] = pending_reasons if isinstance(pending_reasons, dict) else {}
    signatures = state.get("mtime_signatures")
    state["mtime_signatures"] = signatures if isinstance(signatures, dict) else {}
    state["pending_wake_count"] = _safe_int(state.get("pending_wake_count"))
    state["pending_mtime"] = bool(state.get("pending_mtime"))
    state["mtime_poll_initialized"] = bool(state.get("mtime_poll_initialized"))
    return state


def set_paused(
    profile: Path | None = None,
    *,
    paused: bool,
    reason: str | None = None,
) -> dict[str, Any]:
    profile_root = _profile_root(profile)
    state = scheduler_state(profile_root)
    state["schema_version"] = STATE_SCHEMA_VERSION
    state["paused"] = bool(paused)
    state["pause_reason"] = reason or ""
    if paused:
        state["paused_at"] = utc_now()
    else:
        state["resumed_at"] = utc_now()
    state["updated_at"] = utc_now()
    _write_state(profile_root, state)
    return state


def note_session_changed(
    profile: Path | None = None,
    *,
    session_id: str | None = None,
) -> dict[str, Any]:
    profile_root = _profile_root(profile)
    state = scheduler_state(profile_root)
    if session_id:
        pending = [str(item) for item in state.get("pending_sessions", [])]
        if session_id not in pending:
            pending.append(session_id)
        state["pending_sessions"] = pending
    state["last_session_event_at"] = utc_now()
    _mark_pending(state, "session")
    state["updated_at"] = utc_now()
    _write_state(profile_root, state)
    return state


def note_tool_changed(
    profile: Path | None = None,
    *,
    tool_name: str = "",
    args: dict[str, Any] | None = None,
    artifact_paths: list[str] | tuple[str, ...] | None = None,
    session_id: str | None = None,
) -> dict[str, Any]:
    profile_root = _profile_root(profile)
    candidates = list(artifact_paths or _extract_tool_paths(tool_name, args or {}))
    allowed: list[str] = []
    for candidate in candidates:
        logical_path = _allowlisted_artifact_path(profile_root, candidate)
        if logical_path is not None and logical_path not in allowed:
            allowed.append(logical_path)
    if not allowed:
        return scheduler_state(profile_root)

    state = scheduler_state(profile_root)
    pending = [str(item) for item in state.get("pending_artifacts", [])]
    for logical_path in allowed:
        if logical_path not in pending:
            pending.append(logical_path)
    state["pending_artifacts"] = pending
    if session_id:
        state["last_tool_session_id"] = str(session_id)
    state["last_tool_event_at"] = utc_now()
    state["last_tool_name"] = str(tool_name or "")
    _mark_pending(state, "tool")
    state["updated_at"] = utc_now()
    _write_state(profile_root, state)
    return state


def run_continuous(
    profile: Path | None = None,
    remote_path: Path | str | None = None,
    *,
    interval_seconds: float = 5.0,
    max_cycles: int | None = None,
    run_immediately: bool = True,
    stop_event: Any = None,
    debounce_seconds: float = 0.05,
    poll_mtime: bool = True,
    sync_on_idle: bool = True,
) -> dict[str, Any]:
    profile_root = _profile_root(profile)
    interval = max(0.0, float(interval_seconds))
    debounce = max(0.0, float(debounce_seconds))
    actions = {"uploaded": 0, "downloaded": 0, "imported": 0, "deleted": 0}
    phases: list[dict[str, Any]] = []
    cycles = 0
    paused_cycles = 0
    sync_cycles = 0
    idle_cycles = 0
    locked_cycles = 0
    debounced_cycles = 0
    poll_changes = 0
    first = True

    while max_cycles is None or cycles < max_cycles:
        if stop_event is not None and stop_event.is_set():
            break
        if not first or not run_immediately:
            time.sleep(interval)
        first = False

        state = scheduler_state(profile_root)
        if state.get("paused"):
            paused_cycles += 1
            phases.append(
                {
                    "name": "cycle",
                    "status": "paused",
                    "pending": _pending_summary(state),
                }
            )
            cycles += 1
            continue

        poll_report: dict[str, Any] | None = None
        if poll_mtime:
            poll_report = _poll_mtime(profile_root, state)
            if poll_report["changed"]:
                poll_changes += len(poll_report["changed"])
                state = scheduler_state(profile_root)
                state["pending_mtime"] = True
                state["last_mtime_poll_at"] = utc_now()
                _mark_pending(state, "mtime_poll", count=len(poll_report["changed"]))
                _write_state(profile_root, state)
            elif poll_report["should_initialize"]:
                state["mtime_signatures"] = poll_report["signatures"]
                state["mtime_poll_initialized"] = True
                state["last_mtime_poll_at"] = utc_now()
                state["updated_at"] = utc_now()
                _write_state(profile_root, state)

        should_sync = sync_on_idle or _has_pending(state)
        if not should_sync:
            idle_cycles += 1
            phases.append({"name": "cycle", "status": "idle", "pending": _pending_summary(state)})
            cycles += 1
            continue

        if debounce and _has_pending(state):
            debounced_cycles += 1
            time.sleep(debounce)
            state = scheduler_state(profile_root)
            if state.get("paused"):
                paused_cycles += 1
                phases.append(
                    {
                        "name": "cycle",
                        "status": "paused",
                        "pending": _pending_summary(state),
                    }
                )
                cycles += 1
                continue

        with _local_sync_lock(profile_root) as acquired:
            if not acquired:
                locked_cycles += 1
                phases.append(
                    {
                        "name": "cycle",
                        "status": "locked",
                        "pending": _pending_summary(scheduler_state(profile_root)),
                    }
                )
                cycles += 1
                continue

            state = scheduler_state(profile_root)
            if state.get("paused"):
                paused_cycles += 1
                phases.append(
                    {
                        "name": "cycle",
                        "status": "paused",
                        "pending": _pending_summary(state),
                    }
                )
                cycles += 1
                continue

            if not sync_on_idle and not _has_pending(state):
                idle_cycles += 1
                phases.append({"name": "cycle", "status": "idle", "pending": _pending_summary(state)})
                cycles += 1
                continue

            pending_before = _pending_summary(state)
            try:
                result = run_once(profile_root, remote_path)
            except SyncConfigurationError as exc:
                return {
                    "status": "error",
                    "command": "continuous",
                    "message": str(exc),
                    "cycles": cycles,
                    "paused_cycles": paused_cycles,
                    "sync_cycles": sync_cycles,
                    "idle_cycles": idle_cycles,
                    "locked_cycles": locked_cycles,
                    "debounced_cycles": debounced_cycles,
                    "poll_changes": poll_changes,
                    "actions": actions,
                    "phases": phases,
                    "read_only": False,
                }
            state = _clear_pending(scheduler_state(profile_root))
            if poll_mtime:
                state["mtime_signatures"] = _mtime_signatures(profile_root)
                state["mtime_poll_initialized"] = True
            _write_state(
                profile_root,
                {
                    **state,
                    "schema_version": STATE_SCHEMA_VERSION,
                    "last_cycle_at": utc_now(),
                    "updated_at": utc_now(),
                },
            )
        sync_cycles += 1
        for key in actions:
            actions[key] += int(result.get("actions", {}).get(key, 0))
        phases.append(
            {
                "name": "cycle",
                "status": result.get("status", "unknown"),
                "actions": result.get("actions", {}),
                "pending": pending_before,
            }
        )
        cycles += 1

    return {
        "status": "ok",
        "command": "continuous",
        "profile": str(profile_root),
        "cycles": cycles,
        "paused_cycles": paused_cycles,
        "sync_cycles": sync_cycles,
        "idle_cycles": idle_cycles,
        "locked_cycles": locked_cycles,
        "debounced_cycles": debounced_cycles,
        "poll_changes": poll_changes,
        "actions": actions,
        "phases": phases,
        "read_only": False,
    }


def _profile_root(profile: Path | None) -> Path:
    return Path(profile) if profile is not None else get_hermes_home()


def _default_state() -> dict[str, Any]:
    return {
        "schema_version": STATE_SCHEMA_VERSION,
        "paused": False,
        "pause_reason": "",
        "pending_sessions": [],
        "pending_artifacts": [],
        "pending_reasons": {},
        "pending_wake_count": 0,
        "pending_mtime": False,
        "mtime_poll_initialized": False,
        "mtime_signatures": {},
        "updated_at": utc_now(),
    }


def _state_path(profile: Path) -> Path:
    profile_root = profile.resolve()
    path = get_sync_dir(profile) / "watcher-state.json"
    resolved = path.resolve(strict=False)
    try:
        resolved.relative_to(profile_root)
    except ValueError as exc:
        raise ValueError("scheduler state path escapes profile root") from exc
    return path


def _write_state(profile: Path, state: dict[str, Any]) -> None:
    path = _state_path(profile)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(state, indent=2, sort_keys=True) + "\n"
    if path.exists() and path.read_text(encoding="utf-8") == payload:
        return
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(payload, encoding="utf-8")
    tmp.replace(path)


def _safe_int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _mark_pending(state: dict[str, Any], reason: str, *, count: int = 1) -> None:
    reasons = state.get("pending_reasons")
    if not isinstance(reasons, dict):
        reasons = {}
    reasons[reason] = _safe_int(reasons.get(reason)) + max(1, count)
    state["pending_reasons"] = reasons
    state["pending_wake_count"] = _safe_int(state.get("pending_wake_count")) + max(1, count)
    state["last_wake_at"] = utc_now()
    state["updated_at"] = utc_now()


def _clear_pending(state: dict[str, Any]) -> dict[str, Any]:
    state["pending_sessions"] = []
    state["pending_artifacts"] = []
    state["pending_reasons"] = {}
    state["pending_wake_count"] = 0
    state["pending_mtime"] = False
    return state


def _has_pending(state: dict[str, Any]) -> bool:
    return bool(
        state.get("pending_sessions")
        or state.get("pending_artifacts")
        or _safe_int(state.get("pending_wake_count"))
        or state.get("pending_mtime")
    )


def _pending_summary(state: dict[str, Any]) -> dict[str, Any]:
    return {
        "sessions": len(state.get("pending_sessions") or []),
        "artifacts": len(state.get("pending_artifacts") or []),
        "wake_count": _safe_int(state.get("pending_wake_count")),
        "mtime": bool(state.get("pending_mtime")),
        "reasons": dict(state.get("pending_reasons") or {}),
    }


def _extract_tool_paths(tool_name: str, args: dict[str, Any]) -> list[str]:
    paths: list[str] = []
    if tool_name in {"write_file", "patch"}:
        for key in TOOL_PATH_KEYS:
            value = args.get(key)
            if isinstance(value, str) and value:
                paths.append(value)
    elif tool_name == "terminal":
        command = args.get("command")
        if isinstance(command, str) and len(command) < 4096:
            try:
                tokens = shlex.split(command, posix=True)
            except ValueError:
                tokens = []
            paths.extend(token for token in tokens if _looks_like_path(token))
    return paths


def _looks_like_path(value: str) -> bool:
    return (
        value.startswith("/")
        or value.startswith("~/")
        or any(value == root or value.startswith(f"{root}/") for root in ARTIFACT_ROOTS)
    )


def _allowlisted_artifact_path(profile: Path, path_text: str) -> str | None:
    try:
        candidate = Path(path_text).expanduser()
    except (TypeError, ValueError):
        return None
    profile_root = profile.resolve()
    if candidate.is_absolute():
        try:
            rel = candidate.resolve(strict=False).relative_to(profile_root)
        except ValueError:
            return None
    else:
        rel = candidate
    if not rel.parts or rel.parts[0] not in ARTIFACT_ROOTS:
        return None
    try:
        validate_profile_relative_path(profile_root, rel)
    except PathSafetyError:
        return None
    return rel.as_posix()


def _poll_mtime(profile: Path, state: dict[str, Any]) -> dict[str, Any]:
    signatures = _mtime_signatures(profile)
    initialized = bool(state.get("mtime_poll_initialized"))
    previous = state.get("mtime_signatures") if initialized else {}
    if not isinstance(previous, dict):
        previous = {}

    changed: list[str] = []
    keys = set(signatures) | set(previous)
    for key in sorted(keys):
        if signatures.get(key) != previous.get(key):
            changed.append(key)

    return {
        "changed": changed,
        "signatures": signatures,
        "should_initialize": not initialized and not changed,
    }


def _mtime_signatures(profile: Path) -> dict[str, dict[str, Any]]:
    scan = scan_profile(profile, scopes=POLL_SCOPES)
    signatures: dict[str, dict[str, Any]] = {}
    for obj in scan.objects:
        if obj.scope not in {"config", "artifacts"}:
            continue
        key = f"{obj.scope}:{obj.object_id}"
        signatures[key] = {
            "scope": obj.scope,
            "logical_path": obj.logical_path,
            "mtime": obj.mtime,
            "size_bytes": obj.size_bytes,
            "content_hash": obj.content_hash,
        }
    return signatures


def _lock_path(profile: Path) -> Path:
    profile_root = profile.resolve()
    path = get_sync_dir(profile) / "sync.lock"
    resolved = path.resolve(strict=False)
    try:
        resolved.relative_to(profile_root)
    except ValueError as exc:
        raise ValueError("scheduler lock path escapes profile root") from exc
    return path


@contextmanager
def _local_sync_lock(profile: Path) -> Iterator[bool]:
    path = _lock_path(profile)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd: int | None = None
    acquired = False
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        acquired = True
        payload = json.dumps(
            {"schema_version": STATE_SCHEMA_VERSION, "created_at": utc_now()},
            sort_keys=True,
        )
        os.write(fd, payload.encode("utf-8"))
    except FileExistsError:
        yield False
        return
    except OSError:
        if fd is not None:
            os.close(fd)
        if acquired:
            try:
                path.unlink()
            except FileNotFoundError:
                pass
        raise
    try:
        yield True
    finally:
        if fd is not None:
            os.close(fd)
        if acquired:
            try:
                path.unlink()
            except FileNotFoundError:
                pass
