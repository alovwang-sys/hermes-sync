"""Read-only scope scanner and path guards for hermes-sync."""

from __future__ import annotations

import fnmatch
import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable

DEFAULT_SCOPES: Dict[str, bool] = {
    "config": True,
    "sessions": True,
    "memory": True,
    "artifacts": True,
    "skills": True,
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

    def as_dict(self) -> Dict[str, Any]:
        return {
            "object_count": len(self.objects),
            "objects": [obj.as_dict() for obj in self.objects],
            "blocked_count": self.blocked_count,
            "blocked_reasons": dict(sorted(self.blocked_reasons.items())),
            "scope_counts": dict(sorted(self.scope_counts.items())),
        }


def _record_block(blocked_reasons: Dict[str, int], code: str) -> None:
    blocked_reasons[code] = blocked_reasons.get(code, 0) + 1


def _load_scope_overrides(profile: Path) -> Dict[str, bool]:
    scopes = dict(DEFAULT_SCOPES)
    config_path = profile / "config.yaml"
    if not config_path.exists():
        return scopes
    try:
        import yaml  # type: ignore

        loaded = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    except Exception:
        return scopes
    sync_cfg = loaded.get("sync") if isinstance(loaded, dict) else None
    scope_cfg = sync_cfg.get("scopes") if isinstance(sync_cfg, dict) else None
    if isinstance(scope_cfg, dict):
        for key, value in scope_cfg.items():
            if key in scopes and isinstance(value, bool):
                scopes[key] = value
    return scopes


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


def _object_id(scope: str, logical_path: str) -> str:
    return hashlib.sha256(f"{scope}:{logical_path}".encode("utf-8")).hexdigest()


def _scan_file(
    profile: Path,
    scope: str,
    rel: Path,
    objects: list[ScanObject],
    blocked_reasons: Dict[str, int],
) -> None:
    try:
        path = validate_profile_relative_path(profile, rel)
    except PathSafetyError as exc:
        _record_block(blocked_reasons, exc.code)
        return
    try:
        if not path.is_file():
            return
        stat = path.stat()
        logical_path = rel.as_posix()
        objects.append(
            ScanObject(
                scope=scope,
                object_id=_object_id(scope, logical_path),
                logical_path=logical_path,
                content_hash="",
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


def scan_profile(profile: Path | None = None, scopes: Dict[str, bool] | None = None) -> ScanResult:
    """Scan explicit sync scopes without mutating user data or remote state."""

    from .manifest import get_hermes_home

    profile_root = Path(profile) if profile is not None else get_hermes_home()
    scope_flags = dict(DEFAULT_SCOPES)
    scope_flags.update(_load_scope_overrides(profile_root))
    if scopes:
        scope_flags.update(scopes)

    objects: list[ScanObject] = []
    blocked_reasons: Dict[str, int] = {}

    if scope_flags.get("config", False):
        for name in CONFIG_FILES:
            rel = Path(name)
            if (profile_root / rel).exists():
                _scan_file(profile_root, "config", rel, objects, blocked_reasons)

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
                _scan_file(profile_root, scope, rel, objects, blocked_reasons)

    scope_counts: Dict[str, int] = {}
    for obj in objects:
        scope_counts[obj.scope] = scope_counts.get(obj.scope, 0) + 1

    return ScanResult(
        objects=sorted(objects, key=lambda obj: (obj.scope, obj.logical_path)),
        blocked_count=sum(blocked_reasons.values()),
        blocked_reasons=blocked_reasons,
        scope_counts=scope_counts,
    )
