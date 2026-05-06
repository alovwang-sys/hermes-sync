#!/usr/bin/env python3
"""Install this checkout as a Hermes directory plugin.

The installer writes only the plugin shim under the selected Hermes profile.
It does not modify config.yaml and does not store remote credentials.
"""

from __future__ import annotations

import argparse
import os
import shutil
from pathlib import Path


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
    args = parser.parse_args()

    profile = Path(args.profile).expanduser().resolve()
    repo = Path(args.repo).expanduser().resolve()
    plugin_dir = profile / "plugins" / "hermes-sync"
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
        return 0

    plugin_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(manifest, plugin_dir / "plugin.yaml")
    (plugin_dir / "__init__.py").write_text(shim, encoding="utf-8")

    print(f"Installed hermes-sync development plugin to {plugin_dir}")
    print("Add `hermes-sync` to plugins.enabled in the profile config.yaml before use.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
