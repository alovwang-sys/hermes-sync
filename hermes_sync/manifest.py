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
import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

SCHEMA_VERSION = 2


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
    conflict_path TEXT,
    strategy TEXT NOT NULL DEFAULT '',
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
        _ensure_schema_migrations(conn)
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


def _ensure_schema_migrations(conn: sqlite3.Connection) -> None:
    """Apply additive schema updates for manifests created by older phases."""

    columns = _table_columns(conn, "conflicts")
    if "conflict_path" not in columns:
        conn.execute("ALTER TABLE conflicts ADD COLUMN conflict_path TEXT")
    if "strategy" not in columns:
        conn.execute("ALTER TABLE conflicts ADD COLUMN strategy TEXT NOT NULL DEFAULT ''")


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f'PRAGMA table_info("{table}")').fetchall()}


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


def _row_to_dict(row: sqlite3.Row | None) -> Dict[str, Any] | None:
    if row is None:
        return None
    return {key: row[key] for key in row.keys()}


def get_manifest_object(
    profile: Path | None,
    scope: str,
    object_id: str,
) -> Dict[str, Any] | None:
    manifest_path = ensure_manifest(profile)
    conn = sqlite3.connect(str(manifest_path))
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT * FROM objects WHERE scope = ? AND object_id = ?",
            (scope, object_id),
        ).fetchone()
        return _row_to_dict(row)
    finally:
        conn.close()


def list_manifest_objects(
    profile: Path | None = None,
    *,
    scopes: set[str] | None = None,
    include_deleted: bool = True,
) -> list[Dict[str, Any]]:
    manifest_path = ensure_manifest(profile)
    conn = sqlite3.connect(str(manifest_path))
    conn.row_factory = sqlite3.Row
    try:
        clauses: list[str] = []
        params: list[Any] = []
        if scopes:
            placeholders = ", ".join("?" for _ in scopes)
            clauses.append(f"scope IN ({placeholders})")
            params.extend(sorted(scopes))
        if not include_deleted:
            clauses.append("deleted = 0")
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = conn.execute(
            f"SELECT * FROM objects {where} ORDER BY scope, logical_path",
            params,
        ).fetchall()
        return [_row_to_dict(row) or {} for row in rows]
    finally:
        conn.close()


def revision_id(scope: str, object_id: str, content_hash: str) -> str:
    return uuid.uuid5(uuid.NAMESPACE_URL, f"{scope}:{object_id}:{content_hash}").hex


def tombstone_content_hash(parent_rev: str | None) -> str:
    parent = parent_rev or "missing"
    return hashlib.sha256(f"tombstone:{parent}".encode("utf-8")).hexdigest()


def tombstone_revision_id(scope: str, object_id: str, parent_rev: str | None) -> str:
    parent = parent_rev or "missing"
    return uuid.uuid5(uuid.NAMESPACE_URL, f"{scope}:{object_id}:tombstone:{parent}").hex


def record_revision(
    profile: Path | None,
    *,
    revision: str,
    scope: str,
    object_id: str,
    logical_path: str,
    content_hash: str,
    local_rev: str | None,
    remote_rev: str | None,
    source_device_id: str | None = None,
    tombstone: bool = False,
) -> None:
    manifest_path = ensure_manifest(profile)
    conn = sqlite3.connect(str(manifest_path))
    now = utc_now()
    try:
        conn.execute(
            """
            INSERT OR IGNORE INTO revisions(
                revision_id, scope, object_id, logical_path, content_hash,
                local_rev, remote_rev, source_device_id, tombstone, created_at
            )
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                revision,
                scope,
                object_id,
                logical_path,
                content_hash,
                local_rev,
                remote_rev,
                source_device_id,
                1 if tombstone else 0,
                now,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def upsert_local_object(
    profile: Path | None,
    *,
    scope: str,
    object_id: str,
    logical_path: str,
    content_hash: str,
    local_rev: str,
    mtime: float,
    size_bytes: int,
    reason: str = "scan_changed",
) -> bool:
    """Record a scanned object and return True when it needs upload."""

    manifest_path = ensure_manifest(profile)
    conn = sqlite3.connect(str(manifest_path))
    conn.row_factory = sqlite3.Row
    now = utc_now()
    try:
        existing = conn.execute(
            "SELECT * FROM objects WHERE scope = ? AND object_id = ?",
            (scope, object_id),
        ).fetchone()
        existing_dict = _row_to_dict(existing)
        remote_rev = existing_dict["remote_rev"] if existing_dict else None
        dirty = 0 if remote_rev == local_rev else 1
        changed = (
            existing_dict is None
            or existing_dict["logical_path"] != logical_path
            or existing_dict["content_hash"] != content_hash
            or existing_dict["local_rev"] != local_rev
            or int(existing_dict["size_bytes"] or 0) != int(size_bytes)
            or int(existing_dict["deleted"] or 0) != 0
            or int(existing_dict["dirty"] or 0) != dirty
        )
        if changed:
            conn.execute(
                """
                INSERT INTO objects(
                    scope, object_id, logical_path, content_hash, local_rev,
                    remote_rev, base_rev, mtime, size_bytes, deleted, dirty,
                    conflict_state, updated_at
                )
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, '', ?)
                ON CONFLICT(scope, object_id) DO UPDATE SET
                    logical_path = excluded.logical_path,
                    content_hash = excluded.content_hash,
                    local_rev = excluded.local_rev,
                    mtime = excluded.mtime,
                    size_bytes = excluded.size_bytes,
                    deleted = 0,
                    dirty = excluded.dirty,
                    updated_at = excluded.updated_at
                """,
                (
                    scope,
                    object_id,
                    logical_path,
                    content_hash,
                    local_rev,
                    remote_rev,
                    remote_rev,
                    mtime,
                    size_bytes,
                    dirty,
                    now,
                ),
            )
            if dirty:
                conn.execute(
                    """
                    INSERT INTO dirty_queue(scope, object_id, reason, status, created_at)
                    VALUES(?, ?, ?, 'pending', ?)
                    """,
                    (scope, object_id, reason, now),
                )
        conn.commit()
        return bool(dirty)
    finally:
        conn.close()


def mark_object_deleted(
    profile: Path | None,
    *,
    scope: str,
    object_id: str,
    source_device_id: str | None = None,
    reason: str = "scan_deleted",
) -> bool:
    """Mark a previously tracked object as an explicit local tombstone."""

    manifest_path = ensure_manifest(profile)
    conn = sqlite3.connect(str(manifest_path))
    conn.row_factory = sqlite3.Row
    now = utc_now()
    try:
        existing = conn.execute(
            "SELECT * FROM objects WHERE scope = ? AND object_id = ?",
            (scope, object_id),
        ).fetchone()
        if existing is None or int(existing["deleted"] or 0):
            return False
        parent_rev = existing["remote_rev"] or existing["local_rev"] or existing["content_hash"]
        content_hash = tombstone_content_hash(parent_rev)
        local_rev = tombstone_revision_id(scope, object_id, parent_rev)
        dirty = 0 if existing["remote_rev"] == local_rev else 1
        conn.execute(
            """
            UPDATE objects
            SET content_hash = ?, local_rev = ?, base_rev = ?, deleted = 1,
                dirty = ?, conflict_state = '', updated_at = ?
            WHERE scope = ? AND object_id = ?
            """,
            (
                content_hash,
                local_rev,
                existing["remote_rev"],
                dirty,
                now,
                scope,
                object_id,
            ),
        )
        conn.execute(
            """
            INSERT INTO tombstones(
                scope, object_id, logical_path, local_rev, remote_rev,
                deleted_at, source_device_id
            )
            VALUES(?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(scope, object_id) DO UPDATE SET
                logical_path = excluded.logical_path,
                local_rev = excluded.local_rev,
                remote_rev = excluded.remote_rev,
                deleted_at = excluded.deleted_at,
                source_device_id = excluded.source_device_id
            """,
            (
                scope,
                object_id,
                existing["logical_path"],
                local_rev,
                existing["remote_rev"],
                now,
                source_device_id,
            ),
        )
        conn.execute(
            """
            INSERT OR IGNORE INTO revisions(
                revision_id, scope, object_id, logical_path, content_hash,
                local_rev, remote_rev, source_device_id, tombstone, created_at
            )
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
            """,
            (
                local_rev,
                scope,
                object_id,
                existing["logical_path"],
                content_hash,
                local_rev,
                None,
                source_device_id,
                now,
            ),
        )
        if dirty:
            conn.execute(
                """
                INSERT INTO dirty_queue(scope, object_id, reason, status, created_at)
                VALUES(?, ?, ?, 'pending', ?)
                """,
                (scope, object_id, reason, now),
            )
        conn.commit()
        return bool(dirty)
    finally:
        conn.close()


def list_dirty_objects(profile: Path | None = None) -> list[Dict[str, Any]]:
    manifest_path = ensure_manifest(profile)
    conn = sqlite3.connect(str(manifest_path))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT * FROM objects
            WHERE dirty = 1 AND deleted = 0 AND conflict_state = ''
            ORDER BY scope, logical_path
            """
        ).fetchall()
        return [_row_to_dict(row) or {} for row in rows]
    finally:
        conn.close()


def list_dirty_tombstones(profile: Path | None = None) -> list[Dict[str, Any]]:
    manifest_path = ensure_manifest(profile)
    conn = sqlite3.connect(str(manifest_path))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT * FROM objects
            WHERE dirty = 1 AND deleted = 1 AND conflict_state = ''
            ORDER BY scope, logical_path
            """
        ).fetchall()
        return [_row_to_dict(row) or {} for row in rows]
    finally:
        conn.close()


def mark_object_clean(
    profile: Path | None,
    *,
    scope: str,
    object_id: str,
    logical_path: str,
    content_hash: str,
    remote_rev: str,
    mtime: float,
    size_bytes: int,
    source_device_id: str | None = None,
    tombstone: bool = False,
) -> None:
    manifest_path = ensure_manifest(profile)
    conn = sqlite3.connect(str(manifest_path))
    now = utc_now()
    try:
        deleted = 1 if tombstone else 0
        conn.execute(
            """
            INSERT INTO objects(
                scope, object_id, logical_path, content_hash, local_rev,
                remote_rev, base_rev, mtime, size_bytes, deleted, dirty,
                conflict_state, updated_at
            )
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, '', ?)
            ON CONFLICT(scope, object_id) DO UPDATE SET
                logical_path = excluded.logical_path,
                content_hash = excluded.content_hash,
                local_rev = excluded.local_rev,
                remote_rev = excluded.remote_rev,
                base_rev = excluded.base_rev,
                mtime = excluded.mtime,
                size_bytes = excluded.size_bytes,
                deleted = excluded.deleted,
                dirty = 0,
                conflict_state = '',
                updated_at = excluded.updated_at
            """,
            (
                scope,
                object_id,
                logical_path,
                content_hash,
                remote_rev,
                remote_rev,
                remote_rev,
                mtime,
                size_bytes,
                deleted,
                now,
            ),
        )
        conn.execute(
            """
            INSERT OR IGNORE INTO revisions(
                revision_id, scope, object_id, logical_path, content_hash,
                local_rev, remote_rev, source_device_id, tombstone, created_at
            )
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                remote_rev,
                scope,
                object_id,
                logical_path,
                content_hash,
                remote_rev,
                remote_rev,
                source_device_id,
                deleted,
                now,
            ),
        )
        if deleted:
            conn.execute(
                """
                INSERT INTO tombstones(
                    scope, object_id, logical_path, local_rev, remote_rev,
                    deleted_at, source_device_id
                )
                VALUES(?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(scope, object_id) DO UPDATE SET
                    logical_path = excluded.logical_path,
                    local_rev = excluded.local_rev,
                    remote_rev = excluded.remote_rev,
                    deleted_at = excluded.deleted_at,
                    source_device_id = excluded.source_device_id
                """,
                (
                    scope,
                    object_id,
                    logical_path,
                    remote_rev,
                    remote_rev,
                    now,
                    source_device_id,
                ),
            )
        else:
            conn.execute(
                "DELETE FROM tombstones WHERE scope = ? AND object_id = ?",
                (scope, object_id),
            )
        conn.execute(
            """
            UPDATE dirty_queue
            SET status = 'done'
            WHERE scope = ? AND object_id = ? AND status = 'pending'
            """,
            (scope, object_id),
        )
        conn.commit()
    finally:
        conn.close()


def mark_object_merged_dirty(
    profile: Path | None,
    *,
    scope: str,
    object_id: str,
    logical_path: str,
    content_hash: str,
    local_rev: str,
    remote_rev: str,
    mtime: float,
    size_bytes: int,
    reason: str = "merge",
) -> None:
    """Record a successful local merge that still needs to be pushed."""

    manifest_path = ensure_manifest(profile)
    conn = sqlite3.connect(str(manifest_path))
    now = utc_now()
    try:
        conn.execute(
            """
            INSERT INTO objects(
                scope, object_id, logical_path, content_hash, local_rev,
                remote_rev, base_rev, mtime, size_bytes, deleted, dirty,
                conflict_state, updated_at
            )
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 1, '', ?)
            ON CONFLICT(scope, object_id) DO UPDATE SET
                logical_path = excluded.logical_path,
                content_hash = excluded.content_hash,
                local_rev = excluded.local_rev,
                remote_rev = excluded.remote_rev,
                base_rev = excluded.base_rev,
                mtime = excluded.mtime,
                size_bytes = excluded.size_bytes,
                deleted = 0,
                dirty = 1,
                conflict_state = '',
                updated_at = excluded.updated_at
            """,
            (
                scope,
                object_id,
                logical_path,
                content_hash,
                local_rev,
                remote_rev,
                remote_rev,
                mtime,
                size_bytes,
                now,
            ),
        )
        conn.execute(
            """
            INSERT INTO dirty_queue(scope, object_id, reason, status, created_at)
            VALUES(?, ?, ?, 'pending', ?)
            """,
            (scope, object_id, reason, now),
        )
        conn.commit()
    finally:
        conn.close()


def record_conflict(
    profile: Path | None,
    *,
    scope: str,
    object_id: str,
    logical_path: str,
    local_rev: str,
    remote_rev: str,
    base_rev: str | None,
    conflict_path: str,
    strategy: str,
) -> str:
    conflict_id = uuid.uuid5(
        uuid.NAMESPACE_URL,
        f"{scope}:{object_id}:conflict:{local_rev}:{remote_rev}",
    ).hex
    manifest_path = ensure_manifest(profile)
    conn = sqlite3.connect(str(manifest_path))
    now = utc_now()
    try:
        conn.execute(
            """
            INSERT INTO conflicts(
                conflict_id, scope, object_id, logical_path, local_rev,
                remote_rev, base_rev, conflict_path, strategy, state, created_at
            )
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)
            ON CONFLICT(conflict_id) DO UPDATE SET
                conflict_path = excluded.conflict_path,
                strategy = excluded.strategy,
                state = 'pending',
                resolved_at = NULL
            """,
            (
                conflict_id,
                scope,
                object_id,
                logical_path,
                local_rev,
                remote_rev,
                base_rev,
                conflict_path,
                strategy,
                now,
            ),
        )
        conn.execute(
            """
            UPDATE objects
            SET conflict_state = 'pending', updated_at = ?
            WHERE scope = ? AND object_id = ?
            """,
            (now, scope, object_id),
        )
        conn.commit()
        return conflict_id
    finally:
        conn.close()


def list_conflicts(profile: Path | None = None) -> list[Dict[str, Any]]:
    manifest_path = ensure_manifest(profile)
    conn = sqlite3.connect(str(manifest_path))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT * FROM conflicts WHERE state = 'pending' ORDER BY created_at, logical_path"
        ).fetchall()
        return [_row_to_dict(row) or {} for row in rows]
    finally:
        conn.close()


def get_revision(
    profile: Path | None,
    *,
    object_id: str,
    revision: str,
    scope: str | None = None,
) -> Dict[str, Any] | None:
    manifest_path = ensure_manifest(profile)
    conn = sqlite3.connect(str(manifest_path))
    conn.row_factory = sqlite3.Row
    try:
        if scope is None:
            row = conn.execute(
                """
                SELECT * FROM revisions
                WHERE object_id = ? AND revision_id = ?
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (object_id, revision),
            ).fetchone()
        else:
            row = conn.execute(
                """
                SELECT * FROM revisions
                WHERE scope = ? AND object_id = ? AND revision_id = ?
                """,
                (scope, object_id, revision),
            ).fetchone()
        return _row_to_dict(row)
    finally:
        conn.close()


def list_revisions(
    profile: Path | None = None,
    *,
    object_id: str | None = None,
    scope: str | None = None,
) -> list[Dict[str, Any]]:
    manifest_path = ensure_manifest(profile)
    conn = sqlite3.connect(str(manifest_path))
    conn.row_factory = sqlite3.Row
    try:
        clauses: list[str] = []
        params: list[Any] = []
        if scope is not None:
            clauses.append("scope = ?")
            params.append(scope)
        if object_id is not None:
            clauses.append("object_id = ?")
            params.append(object_id)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = conn.execute(
            f"SELECT * FROM revisions {where} ORDER BY created_at, logical_path",
            params,
        ).fetchall()
        return [_row_to_dict(row) or {} for row in rows]
    finally:
        conn.close()


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
