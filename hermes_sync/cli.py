"""Slash-command routing for hermes-sync."""

from __future__ import annotations

from typing import Any, Dict

from .manifest import list_conflicts
from .scheduler import set_paused
from .status import get_status
from .sync_engine import SyncConfigurationError, run_once


def route_sync_command(raw_args: str) -> Dict[str, Any]:
    argv = raw_args.strip().split()
    subcommand = argv[0].lower() if argv else "status"
    if subcommand in {"status", "st"}:
        return get_status()
    if subcommand in {"now", "once"}:
        try:
            return run_once()
        except SyncConfigurationError as exc:
            return _error_response(subcommand, str(exc))
        except Exception as exc:
            return _error_response(subcommand, _exception_message(exc))
    if subcommand == "pause":
        return {
            "status": "ok",
            "subcommand": subcommand,
            "scheduler": set_paused(paused=True, reason="slash_command"),
            "actions": {
                "uploaded": 0,
                "downloaded": 0,
                "imported": 0,
                "deleted": 0,
            },
        }
    if subcommand == "resume":
        return {
            "status": "ok",
            "subcommand": subcommand,
            "scheduler": set_paused(paused=False, reason="slash_command"),
            "actions": {
                "uploaded": 0,
                "downloaded": 0,
                "imported": 0,
                "deleted": 0,
            },
        }
    if subcommand == "conflicts":
        return {
            "status": "ok",
            "subcommand": subcommand,
            "conflicts": list_conflicts(),
            "actions": {
                "uploaded": 0,
                "downloaded": 0,
                "imported": 0,
                "deleted": 0,
            },
        }
    return {
        "status": "error",
        "message": f"Unknown /sync subcommand: {subcommand}",
        "supported": ["status", "now", "pause", "resume", "conflicts"],
    }


def _error_response(subcommand: str, message: str) -> Dict[str, Any]:
    return {
        "status": "error",
        "subcommand": subcommand,
        "message": message or "Sync command failed with an empty error message.",
        "actions": {
            "uploaded": 0,
            "downloaded": 0,
            "imported": 0,
            "deleted": 0,
        },
    }


def _exception_message(exc: BaseException) -> str:
    message = str(exc)
    if message:
        return f"{type(exc).__name__}: {message}"
    cause = getattr(exc, "__cause__", None)
    if cause is not None and str(cause):
        return f"{type(exc).__name__}: caused by {type(cause).__name__}: {cause}"
    return f"{type(exc).__name__}: empty exception message"


def format_status(data: Dict[str, Any]) -> str:
    if data.get("status") != "ok":
        return data.get("message", "Sync command failed.")

    scan = data["scan"]
    manifest = data["manifest"]
    actions = data["actions"]
    scope_counts = scan.get("scope_counts") or {}
    if scope_counts:
        scope_text = ", ".join(f"{name}={count}" for name, count in sorted(scope_counts.items()))
    else:
        scope_text = "none"

    return "\n".join(
        [
            "Hermes Sync status",
            f"Device: {data['device']['device_name']} ({data['device']['device_id'][:12]})",
            f"Manifest schema: v{manifest['schema_version']}",
            f"Manifest objects: {manifest['objects']} tracked, {manifest['dirty_objects']} dirty",
            f"Read-only scan: {scan['object_count']} candidate object(s), {scan['blocked_count']} excluded path(s)",
            f"Scopes: {scope_text}",
            (
                "Actions: "
                f"{actions['uploaded']} uploaded, {actions['downloaded']} downloaded, "
                f"{actions['imported']} imported, {actions['deleted']} deleted"
            ),
        ]
    )


def handle_sync_command(raw_args: str) -> str:
    data = route_sync_command(raw_args)
    if data.get("status") == "ok" and data.get("command") in {"push", "pull", "once"}:
        actions = data["actions"]
        staging = data.get("staging", {})
        metrics = data.get("metrics", {})
        lines = [
            "Hermes Sync now",
            (
                "Actions: "
                f"{actions['uploaded']} uploaded, {actions['downloaded']} downloaded, "
                f"{actions['imported']} imported, {actions['deleted']} deleted"
            ),
            (
                "Staging: "
                f"{staging.get('outbox', 0)} outbox, {staging.get('inbox', 0)} inbox, "
                f"{staging.get('skipped', 0)} skipped"
            ),
        ]
        incremental = _format_incremental_metrics(metrics)
        if incremental:
            lines.append(incremental)
        timing = _format_phase_timings(data.get("phases", []))
        if timing:
            lines.append(timing)
        return "\n".join(lines)
    if data.get("status") == "ok" and data.get("subcommand") == "conflicts":
        conflicts = data.get("conflicts", [])
        if not conflicts:
            return "Hermes Sync conflicts\nNo pending conflicts."
        return "\n".join(
            ["Hermes Sync conflicts"]
            + [
                f"{item['conflict_id'][:12]} {item['logical_path']} {item.get('conflict_path') or ''}".rstrip()
                for item in conflicts
            ]
        )
    if data.get("status") == "ok" and data.get("subcommand") in {"pause", "resume"}:
        state = data.get("scheduler", {})
        return "Hermes Sync paused" if state.get("paused") else "Hermes Sync resumed"
    if data.get("status") == "ok":
        return format_status(data)
    if data.get("status") == "not_implemented":
        return data["message"]
    return data.get("message", "Sync command failed.")


def _format_incremental_metrics(metrics: Dict[str, Any]) -> str:
    candidate_objects = _metric_int(metrics, "candidate_objects")
    if candidate_objects <= 0:
        return ""
    dirty_objects = _metric_int(metrics, "dirty_objects")
    unchanged_objects = _metric_int(metrics, "unchanged_objects")
    hash_reused_objects = _metric_int(metrics, "hash_reused_objects")
    uploaded_bytes = _metric_int(metrics, "uploaded_bytes")
    candidate_bytes = _metric_int(metrics, "candidate_bytes")
    return (
        "Incremental: "
        f"{dirty_objects} dirty / {candidate_objects} scanned, "
        f"{unchanged_objects} unchanged, {hash_reused_objects} hash reused, "
        f"{_format_bytes(uploaded_bytes)} uploaded / {_format_bytes(candidate_bytes)} candidates"
    )


def _format_phase_timings(phases: list[Dict[str, Any]]) -> str:
    parts = []
    for phase in phases:
        if "duration_ms" not in phase:
            continue
        try:
            duration = int(phase["duration_ms"])
        except (TypeError, ValueError):
            continue
        parts.append(f"{phase.get('name', 'phase')}={duration}ms")
    return "Timing: " + ", ".join(parts) if parts else ""


def _metric_int(metrics: Dict[str, Any], key: str) -> int:
    try:
        return int(metrics.get(key, 0))
    except (TypeError, ValueError):
        return 0


def _format_bytes(value: int) -> str:
    units = ("B", "KiB", "MiB", "GiB")
    amount = float(max(0, value))
    for unit in units:
        if amount < 1024 or unit == units[-1]:
            if unit == "B":
                return f"{int(amount)} {unit}"
            return f"{amount:.1f} {unit}"
        amount /= 1024
