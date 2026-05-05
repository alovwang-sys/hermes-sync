"""Isolated harness helpers for phase 1 hermes-sync scenarios."""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import shutil
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterator

REPO_ROOT = Path(__file__).resolve().parents[1]
HERMES_AGENT_ROOT = Path(os.environ.get("HERMES_AGENT_ROOT", "/home/amos/project/hermes-agent"))


class ScenarioFailure(AssertionError):
    pass


@dataclass
class ScenarioResult:
    id: str
    status: str
    detail: str

    def as_dict(self) -> Dict[str, str]:
        return {"id": self.id, "status": self.status, "detail": self.detail}


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ScenarioFailure(message)


class SyncHarness:
    def __init__(self, root: Path):
        self.root = root
        self.profiles_root = root / "profiles"
        self.remotes_root = root / "remotes"
        self.traces_root = root / "traces"
        self.profiles_root.mkdir(parents=True, exist_ok=True)
        self.remotes_root.mkdir(parents=True, exist_ok=True)
        self.traces_root.mkdir(parents=True, exist_ok=True)

    @classmethod
    @contextlib.contextmanager
    def temporary(cls) -> Iterator["SyncHarness"]:
        with tempfile.TemporaryDirectory(prefix="hermes-sync-harness-") as tmp:
            yield cls(Path(tmp))

    def make_remote(self, name: str) -> Path:
        remote = self.remotes_root / name
        remote.mkdir(parents=True, exist_ok=True)
        self._assert_inside_harness(remote)
        return remote

    def make_profile(self, name: str, remote: Path | None = None) -> Path:
        profile = self.profiles_root / name
        profile.mkdir(parents=True, exist_ok=True)
        self._assert_inside_harness(profile)
        if remote is not None:
            self._assert_inside_harness(remote)
        self._install_plugin(profile)
        self._write_profile_config(profile, remote)
        return profile

    def seed_phase1_fixture(self, profile: Path) -> None:
        self._assert_inside_harness(profile)
        (profile / "artifacts").mkdir(exist_ok=True)
        (profile / "artifacts" / "report.txt").write_text(
            "Phase 1 artifact fixture.\n", encoding="utf-8"
        )
        (profile / "skills" / "example").mkdir(parents=True, exist_ok=True)
        (profile / "skills" / "example" / "SKILL.md").write_text(
            "# Example Skill\n\nSanitized harness fixture.\n", encoding="utf-8"
        )
        (profile / "memories").mkdir(exist_ok=True)
        (profile / "memories" / "notes.json").write_text(
            '{"notes": ["sanitized"]}\n', encoding="utf-8"
        )
        (profile / "random.txt").write_text("not in an explicit scope\n", encoding="utf-8")

        (profile / ".env").write_text("PLACEHOLDER=not-used\n", encoding="utf-8")
        (profile / "artifacts" / ".env").write_text("PLACEHOLDER=not-used\n", encoding="utf-8")
        (profile / "artifacts" / "state.db").write_bytes(b"not a real database\n")
        (profile / "artifacts" / "report.log").write_text("runtime only\n", encoding="utf-8")
        (profile / "artifacts" / "scratch.tmp").write_text("runtime only\n", encoding="utf-8")
        (profile / "artifacts" / "cache").mkdir(exist_ok=True)
        (profile / "artifacts" / "cache" / "cache.bin").write_text("runtime only\n", encoding="utf-8")
        for name in ("state.db", "state.db-wal", "state.db-shm"):
            (profile / name).write_bytes(b"not a real database\n")
        for dirname, filename in (
            ("logs", "agent.log"),
            ("cache", "cache.bin"),
            ("tmp", "scratch.txt"),
            ("locks", "sync.lock"),
        ):
            folder = profile / dirname
            folder.mkdir(exist_ok=True)
            (folder / filename).write_text("runtime only\n", encoding="utf-8")

        outside = self.root / "outside"
        outside.mkdir(exist_ok=True)
        outside_file = outside / "outside.txt"
        outside_file.write_text("outside profile\n", encoding="utf-8")
        symlink = profile / "artifacts" / "outside-link.txt"
        try:
            symlink.symlink_to(outside_file)
        except OSError:
            pass

    def load_plugin_manager(self, profile: Path):
        self._assert_inside_harness(profile)
        self._ensure_python_paths()
        with self.hermes_env(profile):
            import hermes_cli.plugins as plugins_mod

            plugins_mod._plugin_manager = plugins_mod.PluginManager()
            manager = plugins_mod.get_plugin_manager()
            manager.discover_and_load(force=True)
            return manager

    @contextlib.contextmanager
    def hermes_env(self, profile: Path) -> Iterator[None]:
        old = os.environ.get("HERMES_HOME")
        os.environ["HERMES_HOME"] = str(profile)
        try:
            yield
        finally:
            if old is None:
                os.environ.pop("HERMES_HOME", None)
            else:
                os.environ["HERMES_HOME"] = old

    def run_slash_status(self, profile: Path) -> str:
        manager = self.load_plugin_manager(profile)
        handler = manager._plugin_commands["sync"]["handler"]
        with self.hermes_env(profile):
            return handler("status")

    def run_tool_status(self, profile: Path) -> Dict[str, Any]:
        self.load_plugin_manager(profile)
        with self.hermes_env(profile):
            from tools.registry import registry

            return json.loads(registry.dispatch("sync_status", {}))

    def direct_status(self, profile: Path) -> Dict[str, Any]:
        self._ensure_python_paths()
        with self.hermes_env(profile):
            from hermes_sync.status import get_status

            return get_status()

    def run_push(self, profile: Path, remote: Path | None = None) -> Dict[str, Any]:
        self._ensure_python_paths()
        self._assert_inside_harness(profile)
        if remote is not None:
            self._assert_inside_harness(remote)
        with self.hermes_env(profile):
            from hermes_sync.sync_engine import run_push

            return run_push(profile, remote)

    def run_pull(self, profile: Path, remote: Path | None = None) -> Dict[str, Any]:
        self._ensure_python_paths()
        self._assert_inside_harness(profile)
        if remote is not None:
            self._assert_inside_harness(remote)
        with self.hermes_env(profile):
            from hermes_sync.sync_engine import run_pull

            return run_pull(profile, remote)

    def run_once(self, profile: Path, remote: Path | None = None) -> Dict[str, Any]:
        self._ensure_python_paths()
        self._assert_inside_harness(profile)
        if remote is not None:
            self._assert_inside_harness(remote)
        with self.hermes_env(profile):
            from hermes_sync.sync_engine import run_once

            return run_once(profile, remote)

    def snapshot_user_tree(self, profile: Path) -> Dict[str, Any]:
        result: Dict[str, Any] = {}
        for path in sorted(profile.rglob("*")):
            rel = path.relative_to(profile)
            if rel.parts and rel.parts[0] == "sync":
                continue
            key = rel.as_posix()
            if path.is_symlink():
                result[key] = {"type": "symlink", "target": os.readlink(path)}
            elif path.is_file():
                result[key] = {
                    "type": "file",
                    "sha256": self._file_hash(path),
                    "size": path.stat().st_size,
                }
            elif path.is_dir():
                result[key] = {"type": "dir"}
        return result

    def manifest_tables(self, profile: Path) -> Dict[str, list[str]]:
        self._ensure_python_paths()
        with self.hermes_env(profile):
            from hermes_sync.manifest import manifest_tables

            return manifest_tables(profile)

    def manifest_object_paths(self, profile: Path) -> list[str]:
        import sqlite3

        db = profile / "sync" / "manifest.sqlite"
        if not db.exists():
            return []
        conn = sqlite3.connect(str(db))
        try:
            return [row[0] for row in conn.execute("SELECT logical_path FROM objects")]
        finally:
            conn.close()

    def manifest_objects(self, profile: Path) -> list[Dict[str, Any]]:
        import sqlite3

        db = profile / "sync" / "manifest.sqlite"
        if not db.exists():
            return []
        conn = sqlite3.connect(str(db))
        conn.row_factory = sqlite3.Row
        try:
            return [
                {key: row[key] for key in row.keys()}
                for row in conn.execute("SELECT * FROM objects ORDER BY scope, logical_path")
            ]
        finally:
            conn.close()

    def list_remote_objects(self, remote: Path) -> list[Dict[str, Any]]:
        self._ensure_python_paths()
        self._assert_inside_harness(remote)
        from hermes_sync.remotes import LocalFolderBackend

        return [metadata.as_dict() for metadata in LocalFolderBackend(remote).list_objects()]

    def list_remote_tombstones(self, remote: Path) -> list[Dict[str, Any]]:
        self._ensure_python_paths()
        self._assert_inside_harness(remote)
        from hermes_sync.remotes import LocalFolderBackend

        return [metadata.as_dict() for metadata in LocalFolderBackend(remote).list_tombstones()]

    def sync_stage_paths(self, profile: Path, stage: str) -> list[str]:
        root = profile / "sync" / stage
        if not root.exists():
            return []
        return sorted(path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file())

    def validate_traversal_rejected(self, profile: Path) -> None:
        self._ensure_python_paths()
        with self.hermes_env(profile):
            from hermes_sync.scopes import PathSafetyError, validate_profile_relative_path

            try:
                validate_profile_relative_path(profile, "../outside")
            except PathSafetyError as exc:
                require(exc.code == "traversal", "traversal was rejected with the wrong reason")
            else:
                raise ScenarioFailure("traversal path was accepted")

    def assert_remote_empty(self, remote: Path) -> None:
        require(remote.exists(), "remote was not created")
        require(not any(remote.iterdir()), "remote was modified by a read-only scenario")

    def write_trace(self, results: list[ScenarioResult]) -> Path:
        trace = {
            "status": "completed" if all(r.status == "complete" for r in results) else "failed",
            "scenario_count": len(results),
            "scenarios": [r.as_dict() for r in results],
        }
        path = self.traces_root / "phase2-trace.json"
        path.write_text(json.dumps(trace, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return path

    def _install_plugin(self, profile: Path) -> None:
        plugin_dir = profile / "plugins" / "hermes-sync"
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

    def _write_profile_config(self, profile: Path, remote: Path | None) -> None:
        remote_line = f"  remote_path: {remote}\n" if remote is not None else ""
        (profile / "config.yaml").write_text(
            "plugins:\n"
            "  enabled:\n"
            "    - hermes-sync\n"
            "sync:\n"
            "  remote: local\n"
            f"{remote_line}"
            "  scopes:\n"
            "    config: true\n"
            "    sessions: true\n"
            "    memory: true\n"
            "    artifacts: true\n"
            "    skills: true\n"
            "    plugins: false\n"
            "    secrets: false\n",
            encoding="utf-8",
        )

    def _assert_inside_harness(self, path: Path) -> None:
        resolved = path.resolve()
        root = self.root.resolve()
        try:
            resolved.relative_to(root)
        except ValueError as exc:
            raise ScenarioFailure(f"path escapes harness root: {path}") from exc

    def _ensure_python_paths(self) -> None:
        for path in (str(REPO_ROOT), str(HERMES_AGENT_ROOT)):
            if path not in sys.path:
                sys.path.insert(0, path)

    @staticmethod
    def _file_hash(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
