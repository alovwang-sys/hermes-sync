#!/usr/bin/env python3
"""Install this checkout as a Hermes directory plugin.

By default the installer writes only the plugin shim under the selected Hermes
profile. With --enable-local it also enables the plugin and writes a safe local
remote configuration. It never stores remote credentials.
"""

from __future__ import annotations

import argparse
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path

PLUGIN_NAME = "hermes-sync"
DEFAULT_REMOTE_PATH = "/tmp/hermes-sync-dev-remote"


def main() -> int:
    parser = argparse.ArgumentParser(description="Install hermes-sync as a development plugin.")
    parser.add_argument(
        "--profile",
        default=os.environ.get("HERMES_HOME") or str(Path.home() / ".hermes"),
        help="Hermes profile root. Defaults to HERMES_HOME or ~/.hermes.",
    )
    parser.add_argument(
        "--repo",
        default=str(Path(__file__).resolve().parents[1]),
        help="hermes-sync repository checkout. Defaults to this script's repository.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print target paths without writing files.",
    )
    parser.add_argument(
        "--enable-local",
        action="store_true",
        help=(
            "Also update profile config.yaml to enable hermes-sync with a safe "
            "local-folder remote."
        ),
    )
    parser.add_argument(
        "--remote-path",
        default=DEFAULT_REMOTE_PATH,
        help=f"Local remote path used with --enable-local. Defaults to {DEFAULT_REMOTE_PATH}.",
    )
    parser.add_argument(
        "--replace-sync-config",
        action="store_true",
        help="Replace an existing top-level sync: block when used with --enable-local.",
    )
    args = parser.parse_args()

    profile = Path(args.profile).expanduser().resolve()
    repo = Path(args.repo).expanduser().resolve()
    plugin_dir = profile / "plugins" / PLUGIN_NAME
    manifest = repo / "plugin.yaml"
    package = repo / "hermes_sync"

    if not manifest.exists():
        raise SystemExit(f"plugin.yaml not found: {manifest}")
    if not package.is_dir():
        raise SystemExit(f"hermes_sync package not found: {package}")

    shim = (
        "from __future__ import annotations\n"
        "import sys\n"
        "from pathlib import Path\n\n"
        f"_repo = Path({str(repo)!r})\n"
        "if str(_repo) not in sys.path:\n"
        "    sys.path.insert(0, str(_repo))\n\n"
        "from hermes_sync import register\n"
    )

    if args.dry_run:
        print(f"profile={profile}")
        print(f"plugin_dir={plugin_dir}")
        print(f"repo={repo}")
        if args.enable_local:
            print(f"config={profile / 'config.yaml'}")
            print(f"remote_path={Path(args.remote_path).expanduser()}")
            if args.replace_sync_config:
                print("replace_sync_config=true")
        return 0

    plugin_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(manifest, plugin_dir / "plugin.yaml")
    (plugin_dir / "__init__.py").write_text(shim, encoding="utf-8")

    print(f"Installed hermes-sync development plugin to {plugin_dir}")
    if args.enable_local:
        result = configure_local_sync(
            profile,
            remote_path=Path(args.remote_path).expanduser(),
            replace_sync_config=args.replace_sync_config,
        )
        if result.backup_path:
            print(f"Backed up existing config.yaml to {result.backup_path}")
        if result.changed:
            print(f"Enabled hermes-sync with local remote in {profile / 'config.yaml'}")
        for warning in result.warnings:
            print(f"Warning: {warning}")
        print("Use `/sync status` in Hermes, then `/sync now` for the first sync.")
    else:
        print(
            "Add `hermes-sync` to plugins.enabled in config.yaml, or rerun with "
            "`--enable-local`."
        )
    return 0


class ConfigUpdateResult:
    def __init__(
        self,
        *,
        changed: bool,
        backup_path: Path | None = None,
        warnings: list[str] | None = None,
    ) -> None:
        self.changed = changed
        self.backup_path = backup_path
        self.warnings = warnings or []


def configure_local_sync(
    profile: Path,
    *,
    remote_path: Path,
    replace_sync_config: bool = False,
) -> ConfigUpdateResult:
    config_path = profile / "config.yaml"
    original = _read_text(config_path)
    text = original
    warnings: list[str] = []

    updated, plugin_warning = _ensure_plugin_enabled(text)
    text = updated
    if plugin_warning:
        warnings.append(plugin_warning)

    updated, sync_warning = _ensure_local_sync_config(
        text,
        remote_path=remote_path,
        replace_sync_config=replace_sync_config,
    )
    text = updated
    if sync_warning:
        warnings.append(sync_warning)

    if text == original:
        return ConfigUpdateResult(changed=False, warnings=warnings)

    backup_path = None
    if config_path.exists():
        backup_path = _backup_path(config_path)
        shutil.copy2(config_path, backup_path)

    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(text, encoding="utf-8")
    return ConfigUpdateResult(changed=True, backup_path=backup_path, warnings=warnings)


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""


def _ensure_plugin_enabled(text: str) -> tuple[str, str | None]:
    lines = text.splitlines()
    ranges = _top_level_ranges(lines)
    plugins_range = ranges.get("plugins")
    if plugins_range is None:
        return _append_block(
            text,
            "\n".join(
                [
                    "plugins:",
                    "  enabled:",
                    f"    - {PLUGIN_NAME}",
                ]
            ),
        ), None

    start, end = plugins_range
    block = lines[start:end]
    _, plugins_value = lines[start].split(":", 1)
    if plugins_value.strip():
        return (
            text,
            "top-level plugins already uses an inline or custom value; "
            "left it unchanged. Add `hermes-sync` to plugins.enabled manually.",
        )
    if any(line.strip() == f"- {PLUGIN_NAME}" for line in block):
        return text, None

    enabled_index = None
    for index, line in enumerate(block):
        stripped = line.strip()
        if line.startswith(" ") and stripped.startswith("enabled:"):
            enabled_index = start + index
            break
    if enabled_index is None:
        lines.insert(end, f"    - {PLUGIN_NAME}")
        lines.insert(end, "  enabled:")
        return _join_lines(lines, text), None

    enabled_line = lines[enabled_index]
    _, value = enabled_line.split(":", 1)
    if value.strip():
        return (
            text,
            "plugins.enabled already uses an inline or custom value; "
            "left it unchanged. Add `hermes-sync` there manually.",
        )

    insert_at = enabled_index + 1
    while insert_at < end and lines[insert_at].startswith("    "):
        insert_at += 1
    lines.insert(insert_at, f"    - {PLUGIN_NAME}")
    return _join_lines(lines, text), None


def _ensure_local_sync_config(
    text: str,
    *,
    remote_path: Path,
    replace_sync_config: bool,
) -> tuple[str, str | None]:
    block = _local_sync_block(remote_path)
    lines = text.splitlines()
    ranges = _top_level_ranges(lines)
    sync_range = ranges.get("sync")
    if sync_range is None:
        return _append_block(text, block), None
    start, end = sync_range
    current_block = "\n".join(lines[start:end]).strip()
    if current_block == block.strip():
        return text, None
    if not replace_sync_config:
        return (
            text,
            "config.yaml already has a top-level sync: block; left it unchanged. "
            "Use --replace-sync-config to overwrite it with the local quick-start config.",
        )
    replacement = block.splitlines()
    updated = lines[:start] + replacement + lines[end:]
    return _join_lines(updated, text), None


def _local_sync_block(remote_path: Path) -> str:
    return "\n".join(
        [
            "sync:",
            "  remote: local",
            f"  remote_path: {remote_path}",
            "  scopes:",
            "    config: true",
            "    sessions: false",
            "    artifacts: true",
            "    memory: false",
            "    skills: false",
            "    plugins: false",
            "    secrets: false",
        ]
    )


def _append_block(text: str, block: str) -> str:
    if not text.strip():
        return block + "\n"
    separator = "\n\n" if text.endswith("\n") else "\n\n"
    return text.rstrip() + separator + block + "\n"


def _top_level_ranges(lines: list[str]) -> dict[str, tuple[int, int]]:
    starts: list[tuple[str, int]] = []
    for index, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or line[0].isspace() or ":" not in line:
            continue
        key = stripped.split(":", 1)[0].strip()
        if key:
            starts.append((key, index))

    ranges: dict[str, tuple[int, int]] = {}
    for offset, (key, start) in enumerate(starts):
        end = starts[offset + 1][1] if offset + 1 < len(starts) else len(lines)
        ranges[key] = (start, end)
    return ranges


def _join_lines(lines: list[str], original: str) -> str:
    text = "\n".join(lines)
    if original.endswith("\n") or not text:
        return text + "\n"
    return text


def _backup_path(config_path: Path) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return config_path.with_name(f"{config_path.name}.hermes-sync-{stamp}.bak")


if __name__ == "__main__":
    raise SystemExit(main())
