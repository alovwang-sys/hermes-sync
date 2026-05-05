"""Local sync metadata for the Hermes sync plugin.

Only plugin-owned metadata lives here. User data is not imported, uploaded, or
deleted by these helpers.
"""

from __future__ import annotations

import json
import os
import socket
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

SCHEMA_VERSION = 1


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def get_hermes_home() -> Path:
    try:
        from hermes_constants import get_hermes_home as core_get_hermes_home

        return Path(core_get_hermes_home())
    except Exception:
        val = os.environ.get("HERMES_HOME", "").strip()
        return Path(val) if val else Path.home() / ".hermes"


def get_sync_dir(profile: Path | None = None) -> Path:
    return Path(profile) / "sync" if profile is not None else get_hermes_home() / "sync"


def get_device_path(profile: Path | None = None) -> Path:
    return get_sync_dir(profile) / "device.json"


def get_manifest_path(profile: Path | None = None) -> Path:
    return get_sync_dir(profile) / "manifest.sqlite"


def _atomic_json_write(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def ensure_device(profile: Path | None = None, device_name: str | None = None) -> Dict[str, Any]:
    """Create or load a stable per-profile device identity."""

    device_path = get_device_path(profile)
    data: Dict[str, Any] = {}
    if device_path.exists():
        try:
            loaded = json.loads(device_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                data = loaded
        except (OSError, json.JSONDecodeError):
            data = {}

    changed = False
    if not isinstance(data.get("device_id"), str) or not data.get("device_id"):
        data["device_id"] = uuid.uuid4().hex
        changed = True
    if data.get("schema_version") != SCHEMA_VERSION:
        data["schema_version"] = SCHEMA_VERSION
        changed = True
    if not data.get("device_name"):
        data["device_name"] = device_name or socket.gethostname() or "hermes-device"
        changed = True
    if not data.get("created_at"):
        data["created_at"] = utc_now()
        changed = True
    if "last_remote_cursor" not in data:
        data["last_remote_cursor"] = None
        changed = True

    if changed or not device_path.exists():
        _atomic_json_write(device_path, data)
    return data


MANIFEST_SQL = """
CREATE TABLE IF NOT EXISTS metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS objects (
    scope TEXT NOT NULL,
    object_id TEXT NOT NULL,
    logical_path TEXT NOT NULL,
    content_hash TEXT,
    local_rev TEXT,
    remote_rev TEXT,
    base_rev TEXT,
    mtime REAL,
    size_bytes INTEGER DEFAULT 0,
    deleted INTEGER NOT NULL DEFAULT 0,
    dirty INTEGER NOT NULL DEFAULT 0,
    conflict_state TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL,
    PRIMARY KEY (scope, object_id)
);

CREATE TABLE IF NOT EXISTS revisions (
    revision_id TEXT PRIMARY KEY,
    scope TEXT NOT NULL,
    object_id TEXT NOT NULL,
    logical_path TEXT NOT NULL,
    content_hash TEXT,
    local_rev TEXT,
    remote_rev TEXT,
    source_device_id TEXT,
    tombstone INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS dirty_queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scope TEXT NOT NULL,
    object_id TEXT NOT NULL,
    reason TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tombstones (
    scope TEXT NOT NULL,
    object_id TEXT NOT NULL,
    logical_path TEXT NOT NULL,
    local_rev TEXT,
    remote_rev TEXT,
    deleted_at TEXT NOT NULL,
    source_device_id TEXT,
    PRIMARY KEY (scope, object_id)
);

CREATE TABLE IF NOT EXISTS conflicts (
    conflict_id TEXT PRIMARY KEY,
    scope TEXT NOT NULL,
    object_id TEXT NOT NULL,
    logical_path TEXT NOT NULL,
    local_rev TEXT,
    remote_rev TEXT,
    base_rev TEXT,
    state TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL,
    resolved_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_objects_dirty ON objects(dirty);
CREATE INDEX IF NOT EXISTS idx_objects_deleted ON objects(deleted);
CREATE INDEX IF NOT EXISTS idx_conflicts_state ON conflicts(state);
"""


def ensure_manifest(profile: Path | None = None) -> Path:
    """Create the local manifest schema if needed and return its path."""

    manifest_path = get_manifest_path(profile)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(manifest_path))
    try:
        conn.executescript(MANIFEST_SQL)
        conn.execute(
            "INSERT INTO metadata(key, value) VALUES('schema_version', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (str(SCHEMA_VERSION),),
        )
        conn.execute(
            "INSERT INTO metadata(key, value) VALUES('created_by', ?) "
            "ON CONFLICT(key) DO NOTHING",
            ("hermes-sync",),
        )
        conn.commit()
    finally:
        conn.close()
    return manifest_path


def inspect_manifest(profile: Path | None = None) -> Dict[str, Any]:
    manifest_path = ensure_manifest(profile)
    conn = sqlite3.connect(str(manifest_path))
    try:
        row = conn.execute(
            "SELECT value FROM metadata WHERE key = 'schema_version'"
        ).fetchone()
        object_count = conn.execute("SELECT COUNT(*) FROM objects").fetchone()[0]
        dirty_count = conn.execute(
            "SELECT COUNT(*) FROM objects WHERE dirty = 1"
        ).fetchone()[0]
        tombstone_count = conn.execute("SELECT COUNT(*) FROM tombstones").fetchone()[0]
        conflict_count = conn.execute(
            "SELECT COUNT(*) FROM conflicts WHERE state = 'pending'"
        ).fetchone()[0]
    finally:
        conn.close()
    return {
        "path": str(manifest_path),
        "schema_version": int(row[0]) if row else SCHEMA_VERSION,
        "objects": object_count,
        "dirty_objects": dirty_count,
        "tombstones": tombstone_count,
        "pending_conflicts": conflict_count,
    }


def manifest_tables(profile: Path | None = None) -> Dict[str, list[str]]:
    manifest_path = ensure_manifest(profile)
    conn = sqlite3.connect(str(manifest_path))
    try:
        tables = [
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
            ).fetchall()
        ]
        result: Dict[str, list[str]] = {}
        for table in tables:
            result[table] = [
                row[1]
                for row in conn.execute(f'PRAGMA table_info("{table}")').fetchall()
            ]
        return result
    finally:
        conn.close()
