#!/usr/bin/env python3
"""Run a real-profile hermes-sync smoke deployment test.

This script intentionally requires --allow-real-profile before it writes to
the default ~/.hermes profile. It does not print config.yaml contents.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_AGENT_ROOT = Path("/home/amos/project/hermes-agent")
DEFAULT_PROFILE = Path.home() / ".hermes"
DEFAULT_REMOTE = Path("/tmp/hermes-sync-real-remote")
DEFAULT_TARGET = Path("/tmp/hermes-sync-real-target")
SMOKE_RELATIVE_PATH = Path("artifacts/hermes-sync-smoke.txt")
MARKER_START = "# BEGIN hermes-sync deployment smoke"
MARKER_END = "# END hermes-sync deployment smoke"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", type=Path, default=DEFAULT_PROFILE)
    parser.add_argument("--remote", type=Path, default=DEFAULT_REMOTE)
    parser.add_argument("--target-profile", type=Path, default=DEFAULT_TARGET)
    parser.add_argument("--hermes-agent-root", type=Path, default=DEFAULT_AGENT_ROOT)
    parser.add_argument("--allow-real-profile", action="store_true")
    parser.add_argument("--reset-remote", action="store_true")
    args = parser.parse_args()

    profile = args.profile.expanduser().resolve()
    remote = args.remote.expanduser().resolve()
    target = args.target_profile.expanduser().resolve()
    agent_root = args.hermes_agent_root.expanduser().resolve()

    if profile == DEFAULT_PROFILE.resolve() and not args.allow_real_profile:
        raise SystemExit("Refusing to write ~/.hermes without --allow-real-profile.")
    if not (profile / "config.yaml").exists():
        raise SystemExit(f"Profile config does not exist: {profile / 'config.yaml'}")
    if not (profile / "plugins" / "hermes-sync" / "plugin.yaml").exists():
        raise SystemExit("hermes-sync is not installed in the selected profile.")
    if not (agent_root / "hermes_cli" / "plugins.py").exists():
        raise SystemExit(f"Hermes agent root is invalid: {agent_root}")
    if profile == target:
        raise SystemExit("Target profile must be different from the source profile.")
    if args.reset_remote and remote.exists():
        require_tmp_path(remote)
        shutil.rmtree(remote)

    backup_path = update_profile_config(profile, remote)
    smoke_content = write_smoke_artifact(profile, remote)

    source = run_installed_plugin(profile, agent_root)
    prepare_target_profile(target, remote)
    target_result = run_installed_plugin(target, agent_root)

    pulled_path = target / SMOKE_RELATIVE_PATH
    if not pulled_path.exists():
        raise SystemExit(f"Smoke artifact was not pulled into target profile: {pulled_path}")
    pulled_content = pulled_path.read_bytes()
    if pulled_content != smoke_content:
        raise SystemExit("Pulled smoke artifact content did not match the source artifact.")

    remote_report = inspect_remote(remote)
    forbidden = find_forbidden_remote_paths(remote_report["objects"], remote_report["tombstones"])
    if forbidden:
        raise SystemExit(f"Forbidden paths appeared in remote metadata: {forbidden}")
    leaked_markers = find_secret_markers(remote)
    if leaked_markers:
        raise SystemExit(f"Secret-like markers appeared in remote content: {leaked_markers}")

    result = {
        "status": "ok",
        "profile": str(profile),
        "config_backup": str(backup_path),
        "remote": {
            "path": str(remote),
            "objects": remote_report["objects"],
            "tombstones": remote_report["tombstones"],
        },
        "source_plugin": source,
        "target_profile": str(target),
        "target_plugin": target_result,
        "smoke_artifact": {
            "logical_path": SMOKE_RELATIVE_PATH.as_posix(),
            "source_sha256": hashlib.sha256(smoke_content).hexdigest(),
            "target_sha256": hashlib.sha256(pulled_content).hexdigest(),
            "target_exists": True,
        },
        "safety": {
            "forbidden_paths_absent": True,
            "secret_markers_absent": True,
        },
    }
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def update_profile_config(profile: Path, remote: Path) -> Path:
    config_path = profile / "config.yaml"
    text = config_path.read_text(encoding="utf-8")
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    backup_path = config_path.with_name(f"config.yaml.hermes-sync-backup-{timestamp}")
    shutil.copy2(config_path, backup_path)

    block = (
        f"{MARKER_START}\n"
        "plugins:\n"
        "  enabled:\n"
        "    - hermes-sync\n"
        "sync:\n"
        "  remote: local\n"
        f"  remote_path: {remote.as_posix()}\n"
        "  scopes:\n"
        "    config: true\n"
        "    sessions: false\n"
        "    memory: false\n"
        "    artifacts: true\n"
        "    skills: false\n"
        "    plugins: false\n"
        "    secrets: false\n"
        f"{MARKER_END}\n"
    )

    if MARKER_START in text or MARKER_END in text:
        start = text.index(MARKER_START)
        end = text.index(MARKER_END, start) + len(MARKER_END)
        text = text[:start].rstrip() + "\n\n" + block + text[end:].lstrip("\n")
    else:
        if has_top_level_key(text, "plugins") or has_top_level_key(text, "sync"):
            raise SystemExit(
                "config.yaml already has plugins or sync settings. "
                "Refusing to merge automatically; update it manually or restore the backup."
            )
        text = text.rstrip() + "\n\n" + block
    config_path.write_text(text, encoding="utf-8")
    return backup_path


def has_top_level_key(text: str, key: str) -> bool:
    prefix = f"{key}:"
    return any(line.startswith(prefix) for line in text.splitlines())


def require_tmp_path(path: Path) -> None:
    tmp_root = Path("/tmp").resolve()
    try:
        path.resolve().relative_to(tmp_root)
    except ValueError as exc:
        raise SystemExit(f"Refusing to reset non-/tmp remote path: {path}") from exc


def write_smoke_artifact(profile: Path, remote: Path) -> bytes:
    artifact = profile / SMOKE_RELATIVE_PATH
    artifact.parent.mkdir(parents=True, exist_ok=True)
    content = (
        "hermes-sync real deployment smoke test\n"
        f"timestamp_utc={datetime.now(timezone.utc).isoformat()}\n"
        f"remote={remote.as_posix()}\n"
    ).encode("utf-8")
    artifact.write_bytes(content)
    return content


def run_installed_plugin(profile: Path, agent_root: Path) -> dict[str, Any]:
    handler, loaded = load_sync_handler(profile, agent_root)
    status_before = handler("status")
    sync_now = handler("now")
    status_after = handler("status")
    return {
        "loaded": loaded,
        "slash_command_registered": True,
        "status_before": status_before,
        "sync_now": sync_now,
        "status_after": status_after,
    }


def load_sync_handler(profile: Path, agent_root: Path) -> tuple[Callable[[str], str], dict[str, Any]]:
    os.environ["HERMES_HOME"] = str(profile)
    for path in (str(REPO_ROOT), str(agent_root)):
        if path not in sys.path:
            sys.path.insert(0, path)

    import hermes_cli.plugins as plugins_mod  # type: ignore

    plugins_mod._plugin_manager = plugins_mod.PluginManager()
    manager = plugins_mod.get_plugin_manager()
    manager.discover_and_load(force=True)
    loaded_plugin = manager._plugins.get("hermes-sync")
    if loaded_plugin is None:
        raise SystemExit("hermes-sync was not discovered by Hermes plugin manager.")
    if loaded_plugin.error:
        raise SystemExit(f"hermes-sync failed to load: {loaded_plugin.error}")
    command = manager._plugin_commands.get("sync")
    if not command or "handler" not in command:
        raise SystemExit("hermes-sync did not register /sync.")
    return command["handler"], {
        "name": loaded_plugin.manifest.name,
        "version": loaded_plugin.manifest.version,
        "enabled": bool(loaded_plugin.enabled),
        "tools": list(loaded_plugin.tools_registered),
        "hooks": list(loaded_plugin.hooks_registered),
        "commands": list(loaded_plugin.commands_registered),
    }


def prepare_target_profile(target: Path, remote: Path) -> None:
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True, exist_ok=True)
    plugin_dir = target / "plugins" / "hermes-sync"
    plugin_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(REPO_ROOT / "plugin.yaml", plugin_dir / "plugin.yaml")
    shim = (
        "from __future__ import annotations\n"
        "import sys\n"
        "from pathlib import Path\n"
        f"_repo = Path({str(REPO_ROOT)!r})\n"
        "if str(_repo) not in sys.path:\n"
        "    sys.path.insert(0, str(_repo))\n"
        "from hermes_sync import register\n"
    )
    (plugin_dir / "__init__.py").write_text(shim, encoding="utf-8")
    (target / "config.yaml").write_text(
        "plugins:\n"
        "  enabled:\n"
        "    - hermes-sync\n"
        "sync:\n"
        "  remote: local\n"
        f"  remote_path: {remote.as_posix()}\n"
        "  scopes:\n"
        "    config: true\n"
        "    sessions: false\n"
        "    memory: false\n"
        "    artifacts: true\n"
        "    skills: false\n"
        "    plugins: false\n"
        "    secrets: false\n",
        encoding="utf-8",
    )


def inspect_remote(remote: Path) -> dict[str, list[dict[str, Any]]]:
    from hermes_sync.remotes.local import LocalFolderBackend

    backend = LocalFolderBackend(remote)
    objects = [
        {
            "scope": item.scope,
            "logical_path": item.logical_path,
            "size_bytes": item.size_bytes,
            "tombstone": item.tombstone,
        }
        for item in backend.list_objects()
    ]
    tombstones = [
        {
            "scope": item.scope,
            "logical_path": item.logical_path,
            "size_bytes": item.size_bytes,
            "tombstone": item.tombstone,
        }
        for item in backend.list_tombstones()
    ]
    return {
        "objects": sorted(objects, key=lambda item: (item["scope"], item["logical_path"])),
        "tombstones": sorted(tombstones, key=lambda item: (item["scope"], item["logical_path"])),
    }


def find_forbidden_remote_paths(
    objects: list[dict[str, Any]],
    tombstones: list[dict[str, Any]],
) -> list[str]:
    forbidden: list[str] = []
    for item in objects + tombstones:
        logical_path = str(item.get("logical_path") or "")
        parts = logical_path.split("/")
        lower = logical_path.lower()
        if lower in {".env", "state.db", "state.db-wal", "state.db-shm"}:
            forbidden.append(logical_path)
        if parts and parts[0].lower() in {"logs", "cache", "tmp", "locks", "plugins"}:
            forbidden.append(logical_path)
        if lower.endswith((".db", ".db-wal", ".db-shm", ".log", ".lock", ".tmp")):
            forbidden.append(logical_path)
    return sorted(set(forbidden))


def find_secret_markers(remote: Path) -> list[str]:
    markers: list[str] = []
    for content_path in sorted((remote / "objects").glob("*/*/content")):
        try:
            text = content_path.read_text(encoding="utf-8").lower()
        except UnicodeDecodeError:
            continue
        if "api_key" in text or "apikey" in text or "credential" in text or "oauth" in text:
            markers.append(str(content_path.relative_to(remote)))
        if "token:" in text or "token =" in text:
            markers.append(str(content_path.relative_to(remote)))
    return sorted(set(markers))


if __name__ == "__main__":
    raise SystemExit(main())
