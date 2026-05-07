"""Read-only scope scanner and path guards for hermes-sync."""

from __future__ import annotations

import fnmatch
import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable

DEFAULT_SCOPES: Dict[str, bool] = {
    "config": True,
    "sessions": False,
    "memory": False,
    "artifacts": True,
    "skills": False,
    "plugins": False,
    "secrets": False,
}

CONFIG_FILES = ("config.yaml", "config.yml", "cli-config.yaml")
SCOPE_ROOTS: Dict[str, tuple[str, ...]] = {
    "artifacts": ("artifacts", "outputs", "reports"),
    "memory": ("memories",),
    "skills": ("skills",),
    "plugins": ("plugins",),
}
PLUGIN_MANIFEST_NAMES = {"plugin.yaml", "plugin.yml", "plugin.json"}
SKILL_RUNTIME_NAMES = {".bundled_manifest", ".curator_state"}
SKILL_RUNTIME_DIRS = {".hub"}

BLOCKED_TOP_LEVEL = {
    ".git",
    "__pycache__",
    "cache",
    "caches",
    "log",
    "logs",
    "lock",
    "locks",
    "sync",
    "temp",
    "tmp",
}
BLOCKED_NAMES = {
    ".env",
    "auth.json",
    "credentials.json",
    "provider_credentials.json",
    "state.db",
    "state.db-shm",
    "state.db-wal",
}
BLOCKED_SUFFIXES = (
    ".db",
    ".db-shm",
    ".db-wal",
    ".lock",
    ".log",
    ".pyc",
    ".tmp",
)
SECRET_NAME_FRAGMENTS = (
    "api_key",
    "apikey",
    "credential",
    "credentials",
    "oauth",
    "secret",
    "token",
)
DEFAULT_EXCLUDE_PATTERNS = (
    "logs/**",
    "cache/**",
    "tmp/**",
    "locks/**",
    "*.db",
    "*.db-wal",
    "*.db-shm",
    ".env",
)
_BOOL_TRUE = {"1", "true", "yes", "on"}
_BOOL_FALSE = {"0", "false", "no", "off"}


class PathSafetyError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class ScanObject:
    scope: str
    object_id: str
    logical_path: str
    content_hash: str
    size_bytes: int
    mtime: float

    def as_dict(self) -> Dict[str, Any]:
        return {
            "scope": self.scope,
            "object_id": self.object_id,
            "logical_path": self.logical_path,
            "size_bytes": self.size_bytes,
            "mtime": self.mtime,
        }


@dataclass
class ScanResult:
    objects: list[ScanObject]
    blocked_count: int
    blocked_reasons: Dict[str, int]
    scope_counts: Dict[str, int]
    hashed_count: int = 0
    hash_reused_count: int = 0

    def as_dict(self) -> Dict[str, Any]:
        return {
            "object_count": len(self.objects),
            "objects": [obj.as_dict() for obj in self.objects],
            "blocked_count": self.blocked_count,
            "blocked_reasons": dict(sorted(self.blocked_reasons.items())),
            "scope_counts": dict(sorted(self.scope_counts.items())),
            "hashed_count": self.hashed_count,
            "hash_reused_count": self.hash_reused_count,
        }


def _record_block(blocked_reasons: Dict[str, int], code: str) -> None:
    blocked_reasons[code] = blocked_reasons.get(code, 0) + 1


def load_configured_scopes(
    profile: Path,
    limit: Dict[str, bool] | None = None,
) -> Dict[str, bool]:
    scopes = dict(DEFAULT_SCOPES)
    scopes.update(_load_scope_overrides(profile))
    if limit is None:
        return scopes
    return {
        key: bool(enabled) and bool(scopes.get(key, False))
        for key, enabled in limit.items()
    }


def _load_scope_overrides(profile: Path) -> Dict[str, bool]:
    config_path = profile / "config.yaml"
    if not config_path.exists():
        return {}
    try:
        text = config_path.read_text(encoding="utf-8")
    except OSError:
        return {}

    loaded_scopes: Dict[str, bool] = {}
    try:
        import yaml  # type: ignore

        loaded = yaml.safe_load(text) or {}
        sync_cfg = loaded.get("sync") if isinstance(loaded, dict) else None
        scope_cfg = sync_cfg.get("scopes") if isinstance(sync_cfg, dict) else None
        if isinstance(scope_cfg, dict):
            for key, value in scope_cfg.items():
                parsed = _parse_bool(value)
                if key in DEFAULT_SCOPES and parsed is not None:
                    loaded_scopes[key] = parsed
    except Exception:
        loaded_scopes = {}
    if loaded_scopes:
        return loaded_scopes
    return _load_scope_overrides_text(text)


def _load_scope_overrides_text(text: str) -> Dict[str, bool]:
    scopes: Dict[str, bool] = {}
    in_sync = False
    in_scopes = False
    scopes_indent = 0
    for raw_line in text.splitlines():
        line = raw_line.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip(" "))
        stripped = line.strip()
        if indent == 0:
            in_sync = stripped == "sync:"
            in_scopes = False
            continue
        if not in_sync:
            continue
        if in_scopes and indent <= scopes_indent:
            in_scopes = False
        if stripped == "scopes:":
            in_scopes = True
            scopes_indent = indent
            continue
        if not in_scopes or indent <= scopes_indent or ":" not in stripped:
            continue
        key, value = stripped.split(":", 1)
        key = key.strip()
        parsed = _parse_bool(value.strip().strip("'\""))
        if key in DEFAULT_SCOPES and parsed is not None:
            scopes[key] = parsed
    return scopes


def _parse_bool(value: object) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in _BOOL_TRUE:
            return True
        if normalized in _BOOL_FALSE:
            return False
    return None


def _matches_default_exclude(rel: Path) -> bool:
    rel_posix = rel.as_posix()
    name = rel.name
    return any(
        fnmatch.fnmatch(rel_posix, pattern) or fnmatch.fnmatch(name, pattern)
        for pattern in DEFAULT_EXCLUDE_PATTERNS
    )


def blocked_path_reason(rel: Path) -> str | None:
    parts = rel.parts
    if not parts:
        return "empty_path"
    if any(part in ("", ".", "..") for part in parts):
        return "traversal"
    for index, part in enumerate(parts):
        if part.lower() in BLOCKED_TOP_LEVEL:
            return "blocked_top_level" if index == 0 else "blocked_runtime_dir"
    if _matches_default_exclude(rel):
        return "excluded_pattern"
    for part in parts:
        lower = part.lower()
        if lower in BLOCKED_NAMES or lower.startswith(".env"):
            return "excluded_name"
        if lower.endswith(BLOCKED_SUFFIXES):
            return "excluded_suffix"
        if any(fragment in lower for fragment in SECRET_NAME_FRAGMENTS):
            return "secret_like_name"
    return None


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def validate_profile_relative_path(profile: Path, relative_path: str | Path) -> Path:
    """Resolve a profile-relative path and reject traversal or escapes."""

    profile_root = profile.resolve()
    rel = Path(relative_path)
    if rel.is_absolute():
        raise PathSafetyError("absolute_path", "absolute paths are not allowed")
    reason = blocked_path_reason(rel)
    if reason:
        raise PathSafetyError(reason, "path is blocked by sync policy")
    target = profile_root / rel
    resolved = target.resolve(strict=False)
    if not _is_relative_to(resolved, profile_root):
        raise PathSafetyError("symlink_escape", "path resolves outside profile root")
    return target


def validate_scope_relative_path(profile: Path, scope: str, relative_path: str | Path) -> Path:
    """Resolve a profile-relative path and reject scope escapes."""

    rel = Path(relative_path)
    target = validate_profile_relative_path(profile, rel)
    reason = scope_path_block_reason(scope, rel)
    if reason:
        raise PathSafetyError(reason, "path is blocked by scope policy")
    return target


def scope_path_block_reason(scope: str, rel: Path) -> str | None:
    parts = rel.parts
    if scope == "config":
        return None if rel.as_posix() in CONFIG_FILES else "scope_root_mismatch"
    if scope in SCOPE_ROOTS:
        roots = SCOPE_ROOTS[scope]
        if not parts or parts[0] not in roots:
            return "scope_root_mismatch"
    if scope == "plugins":
        if len(parts) != 3 or parts[-1] not in PLUGIN_MANIFEST_NAMES:
            return "plugin_non_manifest"
    if scope == "skills":
        if len(parts) >= 2 and parts[1] in SKILL_RUNTIME_DIRS:
            return "blocked_runtime_dir"
        if rel.name in SKILL_RUNTIME_NAMES:
            return "blocked_runtime_file"
    return None


def _object_id(scope: str, logical_path: str) -> str:
    return hashlib.sha256(f"{scope}:{logical_path}".encode("utf-8")).hexdigest()


def _scan_file(
    profile: Path,
    scope: str,
    rel: Path,
    objects: list[ScanObject],
    blocked_reasons: Dict[str, int],
    hash_cache: Dict[tuple[str, str], Dict[str, Any]] | None = None,
    scan_stats: Dict[str, int] | None = None,
) -> None:
    try:
        path = validate_profile_relative_path(profile, rel)
    except PathSafetyError as exc:
        _record_block(blocked_reasons, exc.code)
        return
    reason = scope_path_block_reason(scope, rel)
    if reason:
        _record_block(blocked_reasons, reason)
        return
    try:
        if not path.is_file():
            return
        stat = path.stat()
        logical_path = rel.as_posix()
        object_id = _object_id(scope, logical_path)
        cached_hash = _cached_content_hash(
            hash_cache,
            scope=scope,
            object_id=object_id,
            logical_path=logical_path,
            size_bytes=stat.st_size,
            mtime=stat.st_mtime,
        )
        if cached_hash is None:
            content_hash = file_sha256(path)
            if scan_stats is not None:
                scan_stats["hashed_count"] = scan_stats.get("hashed_count", 0) + 1
        else:
            content_hash = cached_hash
            if scan_stats is not None:
                scan_stats["hash_reused_count"] = scan_stats.get("hash_reused_count", 0) + 1
        objects.append(
            ScanObject(
                scope=scope,
                object_id=object_id,
                logical_path=logical_path,
                content_hash=content_hash,
                size_bytes=stat.st_size,
                mtime=stat.st_mtime,
            )
        )
    except OSError:
        _record_block(blocked_reasons, "read_error")


def _iter_files(root: Path) -> Iterable[Path]:
    try:
        for path in root.rglob("*"):
            if path.is_file():
                yield path
    except OSError:
        return


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _cached_content_hash(
    hash_cache: Dict[tuple[str, str], Dict[str, Any]] | None,
    *,
    scope: str,
    object_id: str,
    logical_path: str,
    size_bytes: int,
    mtime: float,
) -> str | None:
    if scope == "config" or not hash_cache:
        return None
    row = hash_cache.get((scope, object_id))
    if not row:
        return None
    try:
        if str(row.get("logical_path") or "") != logical_path:
            return None
        if int(row.get("size_bytes") or 0) != int(size_bytes):
            return None
        if abs(float(row.get("mtime") or 0.0) - float(mtime)) > 0.000001:
            return None
        content_hash = str(row.get("content_hash") or "")
    except (TypeError, ValueError):
        return None
    return content_hash or None


def scan_profile(
    profile: Path | None = None,
    scopes: Dict[str, bool] | None = None,
    hash_cache: Dict[tuple[str, str], Dict[str, Any]] | None = None,
) -> ScanResult:
    """Scan explicit sync scopes without mutating user data or remote state."""

    from .manifest import get_hermes_home

    profile_root = Path(profile) if profile is not None else get_hermes_home()
    scope_flags = load_configured_scopes(profile_root)
    if scopes:
        scope_flags = {
            key: bool(enabled) and bool(scope_flags.get(key, False))
            for key, enabled in scopes.items()
        }

    objects: list[ScanObject] = []
    blocked_reasons: Dict[str, int] = {}
    scan_stats: Dict[str, int] = {"hashed_count": 0, "hash_reused_count": 0}

    if scope_flags.get("config", False):
        for name in CONFIG_FILES:
            rel = Path(name)
            if (profile_root / rel).exists():
                _scan_file(
                    profile_root,
                    "config",
                    rel,
                    objects,
                    blocked_reasons,
                    hash_cache,
                    scan_stats,
                )

    for scope, root_names in SCOPE_ROOTS.items():
        if not scope_flags.get(scope, False):
            continue
        for root_name in root_names:
            root_rel = Path(root_name)
            try:
                root = validate_profile_relative_path(profile_root, root_rel)
            except PathSafetyError as exc:
                _record_block(blocked_reasons, exc.code)
                continue
            if not root.exists() or not root.is_dir():
                continue
            for path in _iter_files(root):
                try:
                    rel = path.relative_to(profile_root)
                except ValueError:
                    _record_block(blocked_reasons, "outside_profile")
                    continue
                _scan_file(
                    profile_root,
                    scope,
                    rel,
                    objects,
                    blocked_reasons,
                    hash_cache,
                    scan_stats,
                )

    scope_counts: Dict[str, int] = {}
    for obj in objects:
        scope_counts[obj.scope] = scope_counts.get(obj.scope, 0) + 1

    return ScanResult(
        objects=sorted(objects, key=lambda obj: (obj.scope, obj.logical_path)),
        blocked_count=sum(blocked_reasons.values()),
        blocked_reasons=blocked_reasons,
        scope_counts=scope_counts,
        hashed_count=scan_stats["hashed_count"],
        hash_reused_count=scan_stats["hash_reused_count"],
    )
