"""Read-only session snapshot export from Hermes state.db."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict

from .scopes import ScanObject

SNAPSHOT_SCHEMA_VERSION = 1
SNAPSHOT_LOGICAL_PREFIX = "sessions/snapshots/"
_JSON_COLUMNS = {
    "model_config",
    "tool_calls",
    "reasoning_details",
    "codex_reasoning_items",
    "codex_message_items",
}
_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]+$")


@dataclass(frozen=True)
class SessionSnapshotExport:
    scan_object: ScanObject
    content: bytes


def session_object_id(session_id: str) -> str:
    return hashlib.sha256(f"sessions:{session_id}".encode("utf-8")).hexdigest()


def session_logical_path(object_id: str) -> str:
    return f"{SNAPSHOT_LOGICAL_PREFIX}{object_id}.json"


def is_session_snapshot_path(logical_path: str) -> bool:
    if not logical_path.startswith(SNAPSHOT_LOGICAL_PREFIX) or not logical_path.endswith(".json"):
        return False
    name = logical_path.removeprefix(SNAPSHOT_LOGICAL_PREFIX)[:-5]
    return bool(name) and re.fullmatch(r"[a-f0-9]{64}", name) is not None


def export_session_snapshots(profile: Path) -> list[SessionSnapshotExport]:
    """Export each session as deterministic JSON without writing to state.db."""

    db_path = Path(profile) / "state.db"
    if not db_path.exists() or not db_path.is_file():
        return []
    if not _looks_like_sqlite_database(db_path):
        return []

    try:
        conn = _connect_readonly(db_path)
    except sqlite3.Error:
        return []
    conn.row_factory = sqlite3.Row
    try:
        if not _has_required_tables(conn):
            return []
        session_columns = _table_columns(conn, "sessions")
        message_columns = _table_columns(conn, "messages")
        if not _has_snapshot_columns(session_columns, message_columns):
            return []
        session_rows = conn.execute(
            "SELECT * FROM sessions ORDER BY started_at, id"
        ).fetchall()
        exports: list[SessionSnapshotExport] = []
        for session_row in session_rows:
            session_id = str(session_row["id"])
            if not session_id or not _SESSION_ID_RE.match(session_id):
                continue
            messages = conn.execute(
                "SELECT * FROM messages WHERE session_id = ? ORDER BY timestamp, id",
                (session_id,),
            ).fetchall()
            content = _snapshot_content(
                session=_row_dict(session_row, session_columns),
                messages=[_row_dict(row, message_columns) for row in messages],
            )
            object_id = session_object_id(session_id)
            logical_path = session_logical_path(object_id)
            content_hash = hashlib.sha256(content).hexdigest()
            mtime = _snapshot_mtime(session_row, messages)
            exports.append(
                SessionSnapshotExport(
                    scan_object=ScanObject(
                        scope="sessions",
                        object_id=object_id,
                        logical_path=logical_path,
                        content_hash=content_hash,
                        size_bytes=len(content),
                        mtime=mtime,
                    ),
                    content=content,
                )
            )
        return exports
    except (sqlite3.Error, KeyError, TypeError, ValueError):
        return []
    finally:
        conn.close()


def export_session_snapshot_content(profile: Path, object_id: str) -> bytes | None:
    for export in export_session_snapshots(profile):
        if export.scan_object.object_id == object_id:
            return export.content
    return None


def _connect_readonly(db_path: Path) -> sqlite3.Connection:
    uri = f"file:{db_path.resolve()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.execute("PRAGMA query_only = ON")
    return conn


def _looks_like_sqlite_database(db_path: Path) -> bool:
    try:
        with db_path.open("rb") as handle:
            return handle.read(16) == b"SQLite format 3\x00"
    except OSError:
        return False


def _has_required_tables(conn: sqlite3.Connection) -> bool:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name IN ('sessions', 'messages')"
    ).fetchall()
    return {row[0] for row in rows} == {"sessions", "messages"}


def _table_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    return [row[1] for row in conn.execute(f'PRAGMA table_info("{table}")').fetchall()]


def _has_snapshot_columns(session_columns: list[str], message_columns: list[str]) -> bool:
    return {"id", "started_at"}.issubset(session_columns) and {
        "id",
        "session_id",
        "timestamp",
    }.issubset(message_columns)


def _row_dict(row: sqlite3.Row, columns: list[str]) -> Dict[str, Any]:
    data: Dict[str, Any] = {}
    for column in columns:
        value = row[column]
        if column in _JSON_COLUMNS and isinstance(value, str) and value:
            try:
                data[column] = json.loads(value)
            except json.JSONDecodeError:
                data[column] = value
        else:
            data[column] = value
    return data


def _snapshot_content(session: Dict[str, Any], messages: list[Dict[str, Any]]) -> bytes:
    payload = {
        "snapshot_schema_version": SNAPSHOT_SCHEMA_VERSION,
        "source": "hermes-state-db",
        "session": session,
        "messages": messages,
    }
    return (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _snapshot_mtime(session_row: sqlite3.Row, messages: list[sqlite3.Row]) -> float:
    candidates: list[float] = []
    session_keys = set(session_row.keys())
    for key in ("ended_at", "started_at"):
        if key not in session_keys:
            continue
        value = session_row[key]
        if value is not None:
            candidates.append(float(value))
    for row in messages:
        if "timestamp" not in row.keys():
            continue
        value = row["timestamp"]
        if value is not None:
            candidates.append(float(value))
    return max(candidates) if candidates else 0.0
