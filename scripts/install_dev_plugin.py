#!/usr/bin/env python3
"""Install this checkout as a Hermes directory plugin.

By default the installer writes only the plugin shim under the selected Hermes
profile. With --enable-local or --enable-sync it also enables the plugin and
writes a safe sync configuration. It never stores remote credentials.
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
            "Shortcut for --enable-sync --remote local. Also update profile "
            "config.yaml with a safe local-folder remote."
        ),
    )
    parser.add_argument(
        "--enable-sync",
        action="store_true",
        help="Also update profile config.yaml to enable hermes-sync and write sync settings.",
    )
    parser.add_argument(
        "--remote",
        choices=("local", "oss", "webdav", "s3", "r2"),
        default="local",
        help=(
            "Remote backend to configure. Defaults to local. Passing a non-local "
            "remote also enables sync configuration."
        ),
    )
    parser.add_argument(
        "--remote-path",
        default=DEFAULT_REMOTE_PATH,
        help=f"Local remote path for --remote local. Defaults to {DEFAULT_REMOTE_PATH}.",
    )
    parser.add_argument(
        "--bucket",
        help="Cloud bucket name for --remote oss, --remote s3, or --remote r2.",
    )
    parser.add_argument(
        "--endpoint",
        help="Cloud endpoint for --remote oss, --remote s3, or --remote r2.",
    )
    parser.add_argument(
        "--url",
        help="WebDAV URL for --remote webdav.",
    )
    parser.add_argument(
        "--region",
        help="Cloud region. Defaults to cn-hangzhou for OSS, us-east-1 for S3, and auto for R2.",
    )
    parser.add_argument(
        "--prefix",
        default="default-profile",
        help="Remote key/path prefix for cloud backends. Defaults to default-profile.",
    )
    parser.add_argument(
        "--include-sessions",
        action="store_true",
        help="Enable session snapshot sync. Leave off for first real-profile smoke tests.",
    )
    parser.add_argument(
        "--include-memory",
        action="store_true",
        help="Enable memory file sync.",
    )
    parser.add_argument(
        "--include-skills",
        action="store_true",
        help="Enable skill file sync.",
    )
    parser.add_argument(
        "--include-plugin-manifests",
        action="store_true",
        help="Enable plugin manifest sync. Plugin executable code stays local.",
    )
    parser.add_argument(
        "--replace-sync-config",
        action="store_true",
        help="Replace an existing top-level sync: block when used with --enable-sync.",
    )
    args = parser.parse_args()

    configure_sync_requested = args.enable_local or args.enable_sync or args.remote != "local"
    if args.enable_local and args.remote != "local":
        raise SystemExit("--enable-local cannot be combined with a non-local --remote")

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
        if configure_sync_requested:
            sync_request = _sync_config_request_from_args(args)
            print(f"config={profile / 'config.yaml'}")
            print(f"remote={sync_request.remote}")
            for key, value in sync_request.routing.items():
                print(f"{key}={value}")
            if args.replace_sync_config:
                print("replace_sync_config=true")
        return 0

    plugin_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(manifest, plugin_dir / "plugin.yaml")
    (plugin_dir / "__init__.py").write_text(shim, encoding="utf-8")

    print(f"Installed hermes-sync development plugin to {plugin_dir}")
    if configure_sync_requested:
        sync_request = _sync_config_request_from_args(args)
        result = configure_sync(
            profile,
            sync_request=sync_request,
            replace_sync_config=args.replace_sync_config,
        )
        if result.backup_path:
            print(f"Backed up existing config.yaml to {result.backup_path}")
        if result.changed:
            print(
                f"Enabled hermes-sync with {sync_request.remote} remote in "
                f"{profile / 'config.yaml'}"
            )
        for warning in result.warnings:
            print(f"Warning: {warning}")
        for note in sync_request.notes:
            print(f"Note: {note}")
        print("Use `/sync status` in Hermes, then `/sync now` for the first sync.")
    else:
        print(
            "Add `hermes-sync` to plugins.enabled in config.yaml, or rerun with "
            "`--enable-local` or `--enable-sync`."
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


class SyncConfigRequest:
    def __init__(
        self,
        *,
        remote: str,
        routing: dict[str, str],
        scopes: dict[str, bool],
        notes: list[str] | None = None,
    ) -> None:
        self.remote = remote
        self.routing = routing
        self.scopes = scopes
        self.notes = notes or []


def configure_local_sync(
    profile: Path,
    *,
    remote_path: Path,
    replace_sync_config: bool = False,
) -> ConfigUpdateResult:
    return configure_sync(
        profile,
        sync_request=SyncConfigRequest(
            remote="local",
            routing={"remote_path": str(remote_path)},
            scopes=_scope_flags(
                include_sessions=False,
                include_memory=False,
                include_skills=False,
                include_plugin_manifests=False,
            ),
        ),
        replace_sync_config=replace_sync_config,
    )


def configure_sync(
    profile: Path,
    *,
    sync_request: SyncConfigRequest,
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
        sync_request=sync_request,
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


def _sync_config_request_from_args(args: argparse.Namespace) -> SyncConfigRequest:
    remote = "local" if args.enable_local else args.remote
    scopes = _scope_flags(
        include_sessions=bool(args.include_sessions),
        include_memory=bool(args.include_memory),
        include_skills=bool(args.include_skills),
        include_plugin_manifests=bool(args.include_plugin_manifests),
    )
    notes: list[str] = []
    if remote == "local":
        return SyncConfigRequest(
            remote="local",
            routing={"remote_path": str(Path(args.remote_path).expanduser())},
            scopes=scopes,
        )
    if remote == "oss":
        if not args.bucket:
            raise SystemExit("--bucket is required for --remote oss")
        if not args.endpoint:
            raise SystemExit("--endpoint is required for --remote oss")
        notes.append(
            "OSS credentials stay outside config.yaml; set "
            "ALIBABA_CLOUD_ACCESS_KEY_ID and ALIBABA_CLOUD_ACCESS_KEY_SECRET "
            "in the Hermes process environment."
        )
        return SyncConfigRequest(
            remote="oss",
            routing={
                "bucket": args.bucket,
                "endpoint": args.endpoint,
                "region": args.region or "cn-hangzhou",
                "prefix": args.prefix,
            },
            scopes=scopes,
            notes=notes,
        )
    if remote in {"s3", "r2"}:
        if not args.bucket:
            raise SystemExit(f"--bucket is required for --remote {remote}")
        if not args.endpoint:
            raise SystemExit(f"--endpoint is required for --remote {remote}")
        notes.append(
            "S3/R2 credentials stay outside config.yaml; set AWS_ACCESS_KEY_ID "
            "and AWS_SECRET_ACCESS_KEY in the Hermes process environment."
        )
        return SyncConfigRequest(
            remote=remote,
            routing={
                "bucket": args.bucket,
                "endpoint": args.endpoint,
                "region": args.region or ("auto" if remote == "r2" else "us-east-1"),
                "prefix": args.prefix,
            },
            scopes=scopes,
            notes=notes,
        )
    if remote == "webdav":
        url = args.url or args.endpoint
        if not url:
            raise SystemExit("--url is required for --remote webdav")
        notes.append(
            "WebDAV credentials stay outside config.yaml; set "
            "HERMES_SYNC_WEBDAV_USERNAME and HERMES_SYNC_WEBDAV_PASSWORD if "
            "the server requires authentication."
        )
        return SyncConfigRequest(
            remote="webdav",
            routing={"url": url, "prefix": args.prefix},
            scopes=scopes,
            notes=notes,
        )
    raise SystemExit(f"unsupported remote: {remote}")


def _scope_flags(
    *,
    include_sessions: bool,
    include_memory: bool,
    include_skills: bool,
    include_plugin_manifests: bool,
) -> dict[str, bool]:
    return {
        "config": True,
        "sessions": include_sessions,
        "artifacts": True,
        "memory": include_memory,
        "skills": include_skills,
        "plugins": include_plugin_manifests,
        "secrets": False,
    }


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
    sync_request: SyncConfigRequest,
    replace_sync_config: bool,
) -> tuple[str, str | None]:
    block = _sync_block(sync_request)
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


def _sync_block(sync_request: SyncConfigRequest) -> str:
    lines = ["sync:", f"  remote: {sync_request.remote}"]
    for key, value in sync_request.routing.items():
        if value == "":
            continue
        lines.append(f"  {key}: {value}")
    lines.append("  scopes:")
    for key in ("config", "sessions", "artifacts", "memory", "skills", "plugins", "secrets"):
        lines.append(f"    {key}: {_yaml_bool(sync_request.scopes.get(key, False))}")
    return "\n".join(lines)


def _yaml_bool(value: bool) -> str:
    return "true" if value else "false"


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
