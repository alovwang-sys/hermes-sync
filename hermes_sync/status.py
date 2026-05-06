"""Status implementation shared by slash commands, tools, and harness."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

from .manifest import ensure_device, ensure_manifest, get_hermes_home, inspect_manifest
from .scopes import load_configured_scopes, scan_profile
from .session_snapshots import export_session_snapshots


def get_status(profile: Path | None = None) -> Dict[str, Any]:
    """Initialize local sync metadata and run a read-only scope scan."""

    profile_root = Path(profile) if profile is not None else get_hermes_home()
    device = ensure_device(profile_root)
    ensure_manifest(profile_root)
    manifest = inspect_manifest(profile_root)
    scan = scan_profile(profile_root)
    scan_data = scan.as_dict()
    scope_flags = load_configured_scopes(profile_root)
    session_exports = export_session_snapshots(profile_root) if scope_flags.get("sessions", False) else []
    if session_exports:
        scan_data["objects"].extend(export.scan_object.as_dict() for export in session_exports)
        scan_data["objects"].sort(key=lambda obj: (obj["scope"], obj["logical_path"]))
        scan_data["object_count"] = len(scan_data["objects"])
        scope_counts = dict(scan_data.get("scope_counts") or {})
        scope_counts["sessions"] = scope_counts.get("sessions", 0) + len(session_exports)
        scan_data["scope_counts"] = dict(sorted(scope_counts.items()))
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
        "scan": scan_data,
        "dirty_object_count": manifest["dirty_objects"],
        "actions": {
            "uploaded": 0,
            "downloaded": 0,
            "imported": 0,
            "deleted": 0,
        },
        "read_only": True,
    }
