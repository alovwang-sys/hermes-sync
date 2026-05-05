"""Hermes tool handlers for hermes-sync."""

from __future__ import annotations

import json
from typing import Any, Dict

from .status import get_status

SYNC_STATUS_SCHEMA: Dict[str, Any] = {
    "name": "sync_status",
    "description": "Return hermes-sync status using a read-only local scan.",
    "parameters": {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    },
}

SYNC_NOW_SCHEMA: Dict[str, Any] = {
    "name": "sync_now",
    "description": "Run one sync cycle. Phase 1 registers the tool but performs no sync actions.",
    "parameters": {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    },
}

SYNC_LIST_CONFLICTS_SCHEMA: Dict[str, Any] = {
    "name": "sync_list_conflicts",
    "description": "List pending sync conflicts. Phase 1 returns an empty list.",
    "parameters": {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    },
}

SYNC_RESTORE_VERSION_SCHEMA: Dict[str, Any] = {
    "name": "sync_restore_version",
    "description": "Restore an object version. Phase 1 registers the schema but does not restore data.",
    "parameters": {
        "type": "object",
        "properties": {
            "object_id": {"type": "string"},
            "version_id": {"type": "string"},
        },
        "required": ["object_id", "version_id"],
        "additionalProperties": False,
    },
}


def _json(data: Dict[str, Any]) -> str:
    return json.dumps(data, sort_keys=True)


def sync_status_tool(args: Dict[str, Any] | None = None, **_: Any) -> str:
    return _json(get_status())


def sync_now_tool(args: Dict[str, Any] | None = None, **_: Any) -> str:
    return _json(
        {
            "status": "not_implemented",
            "message": "sync_now is registered but push/pull/once are not implemented in phase 1.",
            "actions": {"uploaded": 0, "downloaded": 0, "imported": 0, "deleted": 0},
        }
    )


def sync_list_conflicts_tool(args: Dict[str, Any] | None = None, **_: Any) -> str:
    return _json({"status": "ok", "conflicts": []})


def sync_restore_version_tool(args: Dict[str, Any] | None = None, **_: Any) -> str:
    return _json(
        {
            "status": "not_implemented",
            "message": "sync_restore_version is registered but version history is not implemented in phase 1.",
            "actions": {"uploaded": 0, "downloaded": 0, "imported": 0, "deleted": 0},
        }
    )
