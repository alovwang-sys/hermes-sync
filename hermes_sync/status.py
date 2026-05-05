"""Status implementation shared by slash commands, tools, and harness."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

from .manifest import ensure_device, ensure_manifest, get_hermes_home, inspect_manifest
from .scopes import scan_profile


def get_status(profile: Path | None = None) -> Dict[str, Any]:
    """Initialize local sync metadata and run a read-only scope scan."""

    profile_root = Path(profile) if profile is not None else get_hermes_home()
    device = ensure_device(profile_root)
    ensure_manifest(profile_root)
    manifest = inspect_manifest(profile_root)
    scan = scan_profile(profile_root)
    return {
        "status": "ok",
        "profile": str(profile_root),
        "device": {
            "device_id": device["device_id"],
            "device_name": device["device_name"],
            "schema_version": device["schema_version"],
            "last_remote_cursor": device.get("last_remote_cursor"),
        },
        "manifest": manifest,
        "scan": scan.as_dict(),
        "dirty_object_count": manifest["dirty_objects"],
        "actions": {
            "uploaded": 0,
            "downloaded": 0,
            "imported": 0,
            "deleted": 0,
        },
        "read_only": True,
    }
