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
            return {
                "status": "error",
                "subcommand": subcommand,
                "message": str(exc),
                "actions": {
                    "uploaded": 0,
                    "downloaded": 0,
                    "imported": 0,
                    "deleted": 0,
                },
            }
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
        return "\n".join(
            [
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
        )
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
