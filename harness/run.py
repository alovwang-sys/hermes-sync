"""Run executable hermes-sync harness scenarios."""

from __future__ import annotations

import hashlib
import json
import threading
import time
from pathlib import Path
from typing import Callable

from .backend_conformance import run_backend_conformance
from .fake_oss import FakeOssServer
from .fake_s3 import FakeS3Server
from .fake_webdav import FakeWebDavServer
from .sync_harness import ScenarioFailure, ScenarioResult, SyncHarness, require

REPO_ROOT = Path(__file__).resolve().parents[1]
BLOCKED_MARKERS = (
    ".env",
    "state.db",
    "state.db-wal",
    "state.db-shm",
    "agent.log",
    "cache.bin",
    "report.log",
    "scratch.txt",
    "scratch.tmp",
    "sync.lock",
    "random.txt",
    "outside-link.txt",
    "provider_credentials.json",
    "watcher-state.json",
)


def _result(scenario_id: str, fn: Callable[[], str]) -> ScenarioResult:
    try:
        detail = fn()
        return ScenarioResult(scenario_id, "complete", detail)
    except Exception as exc:
        return ScenarioResult(scenario_id, "failed", str(exc))


def validate_feature_list() -> dict:
    with (REPO_ROOT / "feature_list.json").open(encoding="utf-8") as handle:
        return json.load(handle)


def run() -> dict:
    validate_feature_list()
    results: list[ScenarioResult] = []
    with SyncHarness.temporary() as harness:
        remote = harness.make_remote("local-folder")
        device_a = harness.make_profile("device-a", remote)
        device_b = harness.make_profile("device-b", remote)
        harness.seed_phase1_fixture(device_a)

        def remote_payload(remote_path: Path) -> str:
            objects = harness.list_remote_objects(remote_path)
            chunks = [json.dumps(objects, sort_keys=True)]
            for obj in objects:
                content = harness.read_remote_object_content(
                    remote_path, obj["scope"], obj["object_id"]
                )
                chunks.append(content.decode("utf-8", errors="replace"))
            return "\n".join(chunks)

        def remote_object_by_path(remote_path: Path, logical_path: str) -> dict:
            matches = [
                obj
                for obj in harness.list_remote_objects(remote_path)
                if obj["logical_path"] == logical_path
            ]
            require(len(matches) == 1, f"remote object not found once: {logical_path}")
            return matches[0]

        def write_fake_oss_config(profile: Path, server: FakeOssServer, prefix: str) -> None:
            (profile / "config.yaml").write_text(
                "plugins:\n"
                "  enabled:\n"
                "    - hermes-sync\n"
                "sync:\n"
                "  remote: oss\n"
                f"  bucket: {server.bucket}\n"
                f"  endpoint: {server.endpoint}\n"
                f"  prefix: {prefix}\n"
                "  unsigned: true\n"
                "  path_style: true\n"
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

        def write_fake_r2_config(profile: Path, server: FakeS3Server, prefix: str) -> None:
            (profile / "config.yaml").write_text(
                "plugins:\n"
                "  enabled:\n"
                "    - hermes-sync\n"
                "sync:\n"
                "  remote: r2\n"
                f"  bucket: {server.bucket}\n"
                f"  endpoint: {server.endpoint}\n"
                f"  prefix: {prefix}\n"
                "  unsigned: true\n"
                "  path_style: true\n"
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

        def write_fake_webdav_config(profile: Path, server: FakeWebDavServer, prefix: str) -> None:
            (profile / "config.yaml").write_text(
                "plugins:\n"
                "  enabled:\n"
                "    - hermes-sync\n"
                "sync:\n"
                "  remote: webdav\n"
                f"  url: {server.endpoint}\n"
                f"  prefix: {prefix}\n"
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

        def plugin_manifest_loads() -> str:
            manager = harness.load_plugin_manager(device_a)
            loaded = manager._plugins.get("hermes-sync")
            require(loaded is not None, "hermes-sync plugin was not discovered")
            require(loaded.enabled, "hermes-sync plugin was discovered but not enabled")
            require("sync" in manager._plugin_commands, "/sync command was not registered")
            return "manifest loaded through Hermes PluginManager"

        def slash_status_readonly() -> str:
            before = harness.snapshot_user_tree(device_a)
            output = harness.run_slash_status(device_a)
            after = harness.snapshot_user_tree(device_a)
            require(before == after, "slash status changed user profile data outside sync metadata")
            harness.assert_remote_empty(remote)
            for marker in BLOCKED_MARKERS:
                require(marker not in output, f"slash status reported excluded marker {marker}")
            require("Read-only scan" in output, "slash status did not report read-only scan")
            return "/sync status changed only plugin metadata"

        def slash_router_parity() -> str:
            slash_output = harness.run_slash_status(device_a)
            direct = harness.direct_status(device_a)
            require(str(direct["scan"]["object_count"]) in slash_output, "slash output diverged from status scan count")
            require(direct["actions"] == {"uploaded": 0, "downloaded": 0, "imported": 0, "deleted": 0}, "direct status ran sync actions")
            return "/sync status uses the shared status implementation"

        def tool_schema_registration() -> str:
            harness.load_plugin_manager(device_a)
            from tools.registry import registry

            for name in (
                "sync_status",
                "sync_now",
                "sync_list_conflicts",
                "sync_restore_version",
            ):
                entry = registry.get_entry(name)
                require(entry is not None, f"{name} was not registered")
                require(entry.schema.get("name") == name, f"{name} schema has wrong name")
            return "sync tool schemas registered"

        def tool_readonly_status() -> str:
            before = harness.snapshot_user_tree(device_a)
            data = harness.run_tool_status(device_a)
            after = harness.snapshot_user_tree(device_a)
            require(before == after, "sync_status changed user profile data outside sync metadata")
            harness.assert_remote_empty(remote)
            require(data["read_only"] is True, "sync_status did not report read-only mode")
            require(data["actions"] == {"uploaded": 0, "downloaded": 0, "imported": 0, "deleted": 0}, "sync_status ran sync actions")
            payload = json.dumps(data, sort_keys=True)
            for marker in BLOCKED_MARKERS:
                require(marker not in payload, f"sync_status reported excluded marker {marker}")
            return "sync_status changed only plugin metadata"

        def setup_creates_device_identity() -> str:
            first = harness.direct_status(device_a)
            second = harness.direct_status(device_a)
            require(first["device"]["device_id"] == second["device"]["device_id"], "device_id was not stable")
            require((device_a / "sync" / "device.json").exists(), "device.json was not created")
            return "device.json created with stable identity"

        def manifest_schema_created() -> str:
            harness.direct_status(device_a)
            tables = harness.manifest_tables(device_a)
            required = {
                "objects": {"scope", "object_id", "logical_path", "content_hash", "dirty", "deleted", "conflict_state"},
                "revisions": {"revision_id", "scope", "object_id", "content_hash", "tombstone"},
                "dirty_queue": {"scope", "object_id", "reason", "status"},
                "tombstones": {"scope", "object_id", "logical_path", "deleted_at"},
                "conflicts": {"conflict_id", "scope", "object_id", "local_rev", "remote_rev", "state"},
            }
            for table, columns in required.items():
                require(table in tables, f"manifest table missing: {table}")
                missing = columns - set(tables[table])
                require(not missing, f"{table} missing columns: {sorted(missing)}")
            return "manifest.sqlite schema is queryable"

        def manifest_excludes_blocked_paths() -> str:
            harness.direct_status(device_a)
            paths = harness.manifest_object_paths(device_a)
            payload = json.dumps(paths)
            for marker in BLOCKED_MARKERS:
                require(marker not in payload, f"manifest included excluded marker {marker}")
            return "manifest contains no blocked path rows"

        def path_allowlist() -> str:
            data = harness.direct_status(device_a)
            paths = {obj["logical_path"] for obj in data["scan"]["objects"]}
            require("artifacts/report.txt" in paths, "allowed artifact was not scanned")
            require("skills/example/SKILL.md" in paths, "allowed skill was not scanned")
            require("memories/notes.json" in paths, "allowed memory file was not scanned")
            require("random.txt" not in paths, "top-level non-scope file was scanned")
            return "scan is limited to explicit scopes"

        def ignore_rules() -> str:
            data = harness.direct_status(device_a)
            payload = json.dumps(data, sort_keys=True)
            for marker in BLOCKED_MARKERS:
                require(marker not in payload, f"status payload included excluded marker {marker}")
            require(data["scan"]["blocked_count"] >= 5, "excluded fixture paths were not counted")
            return "excluded paths are counted but not reported"

        def traversal_rejection() -> str:
            harness.validate_traversal_rejected(device_a)
            return "profile-relative traversal paths are rejected"

        def symlink_escape_rejection() -> str:
            data = harness.direct_status(device_a)
            reasons = data["scan"]["blocked_reasons"]
            require(reasons.get("symlink_escape", 0) >= 1, "symlink escape was not rejected")
            paths = {obj["logical_path"] for obj in data["scan"]["objects"]}
            require("artifacts/outside-link.txt" not in paths, "symlink escape was scanned")
            return "symlink escapes are rejected before hashing"

        def empty_profiles() -> str:
            data = harness.direct_status(device_b)
            require(data["dirty_object_count"] == 0, "empty profile reported dirty objects")
            require(data["actions"] == {"uploaded": 0, "downloaded": 0, "imported": 0, "deleted": 0}, "empty status ran sync actions")
            return "empty isolated profile reports no dirty objects"

        def config_export() -> str:
            config_remote = harness.make_remote("config-export")
            profile = harness.make_profile("config-export-source", config_remote)
            result = harness.run_push(profile, config_remote)
            require(result["status"] == "ok", "config export push did not complete")
            remote_objects = harness.list_remote_objects(config_remote)
            config_objects = [
                obj
                for obj in remote_objects
                if obj["scope"] == "config" and obj["logical_path"] == "config.yaml"
            ]
            require(len(config_objects) == 1, "config.yaml was not exported once")
            content = harness.read_remote_object_content(
                config_remote,
                config_objects[0]["scope"],
                config_objects[0]["object_id"],
            ).decode("utf-8")
            require("plugins:" in content and "sync:" in content, "exported config content was incomplete")
            outbox_paths = harness.sync_stage_paths(profile, "outbox")
            expected_content = f"config/{config_objects[0]['object_id']}/content"
            require(expected_content in outbox_paths, "config content was not staged in outbox")
            return "non-secret config is staged and uploaded as a config object"

        def secret_exclusion() -> str:
            secret_remote = harness.make_remote("secret-exclusion")
            profile = harness.make_profile("secret-source", secret_remote)
            (profile / "config.yaml").write_text(
                "plugins:\n"
                "  enabled:\n"
                "    - hermes-sync\n"
                "sync:\n"
                "  remote: local\n"
                f"  remote_path: {secret_remote}\n"
                "api_key: example-redacted\n"
                "provider_token: example-redacted\n",
                encoding="utf-8",
            )
            (profile / ".env").write_text("TOKEN=example-redacted\n", encoding="utf-8")
            (profile / "provider_credentials.json").write_text("{}\n", encoding="utf-8")
            result = harness.run_push(profile, secret_remote)
            require(result["status"] == "ok", "secret exclusion push did not complete")
            require(result["actions"]["uploaded"] == 0, "secret-like config was uploaded")
            require(not harness.list_remote_objects(secret_remote), "secret scenario remote was not empty")
            paths = harness.manifest_object_paths(profile)
            payload = json.dumps(paths, sort_keys=True)
            for marker in (".env", "provider_credentials.json", "api_key", "provider_token"):
                require(marker not in payload, f"manifest included secret marker {marker}")
            return "secret-like config keys and credential files are skipped everywhere"

        def configured_scope_disable_sessions() -> str:
            scoped_remote = harness.make_remote("configured-scope-disable-sessions")
            profile = harness.make_profile("sessions-disabled-source", scoped_remote)
            harness.seed_session_fixture(profile)
            artifact = profile / "artifacts" / "scope-check.txt"
            artifact.parent.mkdir(exist_ok=True)
            artifact.write_text("Artifact scope remains enabled.\n", encoding="utf-8")
            (profile / "config.yaml").write_text(
                "plugins:\n"
                "  enabled:\n"
                "    - hermes-sync\n"
                "sync:\n"
                "  remote: local\n"
                f"  remote_path: {scoped_remote}\n"
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
            status = harness.direct_status(profile)
            require("sessions" not in status["scan"]["scope_counts"], "status ignored sessions=false")
            push = harness.run_push(profile, scoped_remote)
            require(push["status"] == "ok", "configured scope push did not complete")
            remote_objects = harness.list_remote_objects(scoped_remote)
            scopes = {obj["scope"] for obj in remote_objects}
            paths = {obj["logical_path"] for obj in remote_objects}
            require("sessions" not in scopes, "sessions=false still uploaded session snapshots")
            require("artifacts/scope-check.txt" in paths, "enabled artifact scope was not uploaded")
            return "configured disabled session scope is honored by status and push"

        def db_file_exclusion() -> str:
            db_remote = harness.make_remote("db-file-exclusion")
            profile = harness.make_profile("db-source", db_remote)
            for name in ("state.db", "state.db-wal", "state.db-shm"):
                (profile / name).write_bytes(b"not a real database\n")
            result = harness.run_push(profile, db_remote)
            require(result["status"] == "ok", "db exclusion push did not complete")
            payload = remote_payload(db_remote)
            for marker in ("state.db", "state.db-wal", "state.db-shm"):
                require(marker not in payload, f"remote included database marker {marker}")
            paths = harness.manifest_object_paths(profile)
            require(not any("state.db" in path for path in paths), "manifest included database file")
            return "state.db, WAL, and SHM files are never uploaded as files"

        def artifact_push_pull() -> str:
            artifact_remote = harness.make_remote("artifact-push-pull")
            source = harness.make_profile("artifact-source", artifact_remote)
            target = harness.make_profile("artifact-target", artifact_remote)
            artifact = source / "artifacts" / "tool-output.txt"
            artifact.parent.mkdir(exist_ok=True)
            artifact.write_text("Sanitized tool artifact.\n", encoding="utf-8")
            push = harness.run_push(source, artifact_remote)
            require(push["status"] == "ok", "artifact push did not complete")
            pull = harness.run_pull(target, artifact_remote)
            require(pull["status"] == "ok", "artifact pull did not complete")
            imported = target / "artifacts" / "tool-output.txt"
            require(imported.read_text(encoding="utf-8") == "Sanitized tool artifact.\n", "artifact content did not round-trip")
            return "text artifact pushes from one profile and pulls into another"

        def memory_skills_plugins_push_pull() -> str:
            scoped_remote = harness.make_remote("memory-skills-plugins")
            source = harness.make_profile("msp-source", scoped_remote)
            target = harness.make_profile("msp-target", scoped_remote)
            for profile in (source, target):
                (profile / "config.yaml").write_text(
                    "plugins:\n"
                    "  enabled:\n"
                    "    - hermes-sync\n"
                    "sync:\n"
                    "  remote: local\n"
                    f"  remote_path: {scoped_remote}\n"
                    "  scopes:\n"
                    "    config: true\n"
                    "    sessions: false\n"
                    "    memory: true\n"
                    "    artifacts: true\n"
                    "    skills: true\n"
                    "    plugins: true\n"
                    "    secrets: false\n",
                    encoding="utf-8",
                )

            memory = source / "memories" / "MEMORY.md"
            memory.parent.mkdir(exist_ok=True)
            memory.write_text("Remember this sanitized note.\n", encoding="utf-8")
            (source / "memories" / "MEMORY.md.lock").write_text("runtime lock\n", encoding="utf-8")

            skill = source / "skills" / "custom" / "SKILL.md"
            skill.parent.mkdir(parents=True, exist_ok=True)
            skill.write_text("# Custom skill\n\nSanitized skill body.\n", encoding="utf-8")
            hub_state = source / "skills" / ".hub"
            hub_state.mkdir(parents=True, exist_ok=True)
            (hub_state / "taps.json").write_text("{}\n", encoding="utf-8")
            (source / "skills" / ".curator_state").write_text("{}\n", encoding="utf-8")

            plugin = source / "plugins" / "demo"
            plugin.mkdir(parents=True, exist_ok=True)
            (plugin / "plugin.yaml").write_text(
                "name: demo\nversion: 0.0.1\ndescription: sanitized demo\n",
                encoding="utf-8",
            )
            (plugin / "__init__.py").write_text("SECRET_TOKEN = 'blocked'\n", encoding="utf-8")

            push = harness.run_push(source, scoped_remote)
            require(push["status"] == "ok", "memory/skills/plugins push did not complete")
            remote_objects = harness.list_remote_objects(scoped_remote)
            remote_paths = {obj["logical_path"] for obj in remote_objects}
            require("memories/MEMORY.md" in remote_paths, "memory file was not uploaded")
            require("skills/custom/SKILL.md" in remote_paths, "skill file was not uploaded")
            require("plugins/demo/plugin.yaml" in remote_paths, "plugin manifest was not uploaded")
            for marker in (
                "MEMORY.md.lock",
                "skills/.hub/taps.json",
                "skills/.curator_state",
                "plugins/demo/__init__.py",
                "SECRET_TOKEN",
            ):
                require(marker not in remote_payload(scoped_remote), f"unsafe scoped file leaked: {marker}")

            pull = harness.run_pull(target, scoped_remote)
            require(pull["status"] == "ok", "memory/skills/plugins pull did not complete")
            require((target / "memories" / "MEMORY.md").read_text(encoding="utf-8") == "Remember this sanitized note.\n", "memory content did not round-trip")
            require((target / "skills" / "custom" / "SKILL.md").read_text(encoding="utf-8") == "# Custom skill\n\nSanitized skill body.\n", "skill content did not round-trip")
            require((target / "plugins" / "demo" / "plugin.yaml").exists(), "plugin manifest did not import")
            require(not (target / "plugins" / "demo" / "__init__.py").exists(), "plugin executable file imported")
            return "memory, skills, and plugin manifests push and pull while runtime/plugin code stays local"

        def runtime_file_exclusion() -> str:
            runtime_remote = harness.make_remote("runtime-file-exclusion")
            profile = harness.make_profile("runtime-source", runtime_remote)
            (profile / "artifacts").mkdir(exist_ok=True)
            (profile / "artifacts" / "report.txt").write_text("Allowed artifact.\n", encoding="utf-8")
            for dirname, filename in (
                ("logs", "agent.log"),
                ("cache", "cache.bin"),
                ("tmp", "scratch.tmp"),
                ("locks", "sync.lock"),
                ("sync", "watcher-state.json"),
            ):
                folder = profile / dirname
                folder.mkdir(exist_ok=True)
                (folder / filename).write_text("runtime only\n", encoding="utf-8")
            for filename in ("tool.log", "scratch.tmp", "runtime.lock"):
                (profile / "artifacts" / filename).write_text("runtime only\n", encoding="utf-8")
            result = harness.run_push(profile, runtime_remote)
            require(result["status"] == "ok", "runtime exclusion push did not complete")
            remote_paths = {obj["logical_path"] for obj in harness.list_remote_objects(runtime_remote)}
            require("artifacts/report.txt" in remote_paths, "allowed artifact was not uploaded")
            for marker in (
                "agent.log",
                "cache.bin",
                "scratch.tmp",
                "sync.lock",
                "watcher-state.json",
                "tool.log",
                "runtime.lock",
            ):
                require(marker not in json.dumps(sorted(remote_paths)), f"runtime marker was uploaded: {marker}")
            return "runtime logs, caches, tmp files, locks, and watcher state stay local"

        def session_snapshot() -> str:
            session_remote = harness.make_remote("session-snapshot")
            source = harness.make_profile("session-source", session_remote)
            target = harness.make_profile("session-target", session_remote)
            session_id = harness.seed_session_fixture(source)
            push = harness.run_push(source, session_remote)
            require(push["status"] == "ok", "session snapshot push did not complete")
            remote_objects = harness.list_remote_objects(session_remote)
            session_objects = [obj for obj in remote_objects if obj["scope"] == "sessions"]
            require(len(session_objects) == 1, "session snapshot was not uploaded once")
            metadata = session_objects[0]
            require(metadata["logical_path"].startswith("sessions/snapshots/"), "session snapshot logical path is not app-aware")
            content = harness.read_remote_object_content(
                session_remote, metadata["scope"], metadata["object_id"]
            )
            snapshot = json.loads(content.decode("utf-8"))
            require(snapshot["session"]["id"] == session_id, "session id did not export")
            require(len(snapshot["messages"]) == 2, "session messages did not export")
            require("state.db" not in remote_payload(session_remote), "database filename leaked into remote payload")

            pull = harness.run_pull(target, session_remote)
            require(pull["status"] == "ok", "session snapshot pull did not complete")
            require(not (target / "state.db").exists(), "pull created a state.db file")
            imported_snapshots = harness.sync_stage_paths(target, "sessions")
            require(
                f"{metadata['object_id']}/snapshot.json" in imported_snapshots,
                "pulled session snapshot was not stored as plugin history",
            )
            return "session snapshot JSON moves through sync without copying SQLite files"

        def local_remote_object_round_trip() -> str:
            from hermes_sync.manifest import revision_id, utc_now
            from hermes_sync.remotes import LocalFolderBackend, RemoteObjectMetadata

            roundtrip_remote = harness.make_remote("round-trip")
            backend = LocalFolderBackend(roundtrip_remote)
            content = b"Local backend round trip fixture.\n"
            object_id = hashlib.sha256(b"artifacts:roundtrip.txt").hexdigest()
            content_hash = hashlib.sha256(content).hexdigest()
            remote_rev = revision_id("artifacts", object_id, content_hash)
            metadata = RemoteObjectMetadata(
                scope="artifacts",
                object_id=object_id,
                logical_path="artifacts/roundtrip.txt",
                content_hash=content_hash,
                remote_rev=remote_rev,
                size_bytes=len(content),
                mtime=0.0,
                updated_at=utc_now(),
                source_device_id="harness",
            )
            backend.upload_object(metadata, content)
            listed = backend.list_objects()
            require(len(listed) == 1, "uploaded object was not listed")
            downloaded = backend.download_object("artifacts", object_id)
            require(downloaded.content == content, "downloaded content did not match upload")
            backend.put_tombstone(metadata)
            require(not backend.list_objects(), "tombstoned object remained active")
            tombstones = backend.list_tombstones()
            require(len(tombstones) == 1, "tombstone was not listed")
            return "local-folder backend uploads, lists, downloads, and tombstones one object"

        def backend_conformance() -> str:
            from hermes_sync.remotes import LocalFolderBackend

            conformance_remote = harness.make_remote("backend-conformance-local")
            return run_backend_conformance(
                backend_name="local-folder",
                backend_factory=lambda: LocalFolderBackend(conformance_remote),
                remote_root=conformance_remote,
            )

        def oss_backend_conformance() -> str:
            from hermes_sync.remotes import OssBackend

            with FakeOssServer() as server:
                detail = run_backend_conformance(
                    backend_name="oss-fake",
                    backend_factory=lambda: OssBackend(
                        bucket=server.bucket,
                        endpoint=server.endpoint,
                        prefix="hermes-sync/conformance",
                        unsigned=True,
                        path_style=True,
                    ),
                    remote_root=None,
                )
                server.assert_protocol_covered()
                return detail + " with fake OSS protocol coverage"

        def oss_sync_config_round_trip() -> str:
            from hermes_sync.remotes import OssBackend

            with FakeOssServer() as server:
                prefix = "hermes-sync/e2e"
                source = harness.make_profile("oss-source")
                target = harness.make_profile("oss-target")
                write_fake_oss_config(source, server, prefix)
                write_fake_oss_config(target, server, prefix)
                artifact = source / "artifacts" / "oss.txt"
                artifact.parent.mkdir(exist_ok=True)
                artifact.write_text("OSS fake round trip fixture.\n", encoding="utf-8")

                push = harness.run_push(source)
                require(push["status"] == "ok", "OSS-configured push did not complete")
                backend = OssBackend(
                    bucket=server.bucket,
                    endpoint=server.endpoint,
                    prefix=prefix,
                    unsigned=True,
                    path_style=True,
                )
                remote_paths = {metadata.logical_path for metadata in backend.list_objects()}
                require("artifacts/oss.txt" in remote_paths, "OSS remote did not receive artifact")
                require("config.yaml" in remote_paths, "OSS remote did not receive safe config")

                pull = harness.run_pull(target)
                require(pull["status"] == "ok", "OSS-configured pull did not complete")
                imported = target / "artifacts" / "oss.txt"
                require(imported.exists(), "OSS pull did not import artifact")
                require(
                    imported.read_text(encoding="utf-8") == "OSS fake round trip fixture.\n",
                    "OSS imported content did not match",
                )
                payload = json.dumps([metadata.as_dict() for metadata in backend.list_objects()], sort_keys=True)
                for marker in BLOCKED_MARKERS:
                    require(marker not in payload, f"OSS remote metadata included excluded marker {marker}")
                server.assert_protocol_covered()
            return "remote: oss config pushes and pulls through a fake OSS service without secrets"

        def webdav_backend_conformance() -> str:
            from hermes_sync.remotes import WebDavBackend

            with FakeWebDavServer() as server:
                detail = run_backend_conformance(
                    backend_name="webdav-fake",
                    backend_factory=lambda: WebDavBackend(
                        base_url=server.endpoint,
                        prefix="hermes-sync/conformance",
                    ),
                    remote_root=None,
                )
                server.assert_protocol_covered()
                return detail + " with fake WebDAV protocol coverage"

        def webdav_sync_config_round_trip() -> str:
            from hermes_sync.remotes import WebDavBackend

            with FakeWebDavServer() as server:
                prefix = "hermes-sync/e2e"
                source = harness.make_profile("webdav-source")
                target = harness.make_profile("webdav-target")
                write_fake_webdav_config(source, server, prefix)
                write_fake_webdav_config(target, server, prefix)
                artifact = source / "artifacts" / "webdav.txt"
                artifact.parent.mkdir(exist_ok=True)
                artifact.write_text("WebDAV fake round trip fixture.\n", encoding="utf-8")

                push = harness.run_push(source)
                require(push["status"] == "ok", "WebDAV-configured push did not complete")
                backend = WebDavBackend(base_url=server.endpoint, prefix=prefix)
                remote_paths = {metadata.logical_path for metadata in backend.list_objects()}
                require("artifacts/webdav.txt" in remote_paths, "WebDAV remote did not receive artifact")
                require("config.yaml" in remote_paths, "WebDAV remote did not receive safe config")

                pull = harness.run_pull(target)
                require(pull["status"] == "ok", "WebDAV-configured pull did not complete")
                imported = target / "artifacts" / "webdav.txt"
                require(imported.exists(), "WebDAV pull did not import artifact")
                require(
                    imported.read_text(encoding="utf-8") == "WebDAV fake round trip fixture.\n",
                    "WebDAV imported content did not match",
                )
                payload = json.dumps([metadata.as_dict() for metadata in backend.list_objects()], sort_keys=True)
                for marker in BLOCKED_MARKERS:
                    require(marker not in payload, f"WebDAV remote metadata included excluded marker {marker}")
                server.assert_protocol_covered()
            return "remote: webdav config pushes and pulls through a fake WebDAV service without secrets"

        def s3_backend_conformance() -> str:
            from hermes_sync.remotes import S3CompatibleBackend

            with FakeS3Server() as server:
                detail = run_backend_conformance(
                    backend_name="s3-fake",
                    backend_factory=lambda: S3CompatibleBackend(
                        bucket=server.bucket,
                        endpoint=server.endpoint,
                        prefix="hermes-sync/conformance",
                        unsigned=True,
                        path_style=True,
                    ),
                    remote_root=None,
                )
                server.assert_protocol_covered()
                return detail + " with fake S3 protocol coverage"

        def r2_sync_config_round_trip() -> str:
            from hermes_sync.remotes import S3CompatibleBackend

            with FakeS3Server() as server:
                prefix = "hermes-sync/r2-e2e"
                source = harness.make_profile("r2-source")
                target = harness.make_profile("r2-target")
                write_fake_r2_config(source, server, prefix)
                write_fake_r2_config(target, server, prefix)
                artifact = source / "artifacts" / "r2.txt"
                artifact.parent.mkdir(exist_ok=True)
                artifact.write_text("R2 fake round trip fixture.\n", encoding="utf-8")

                push = harness.run_push(source)
                require(push["status"] == "ok", "R2-configured push did not complete")
                backend = S3CompatibleBackend(
                    bucket=server.bucket,
                    endpoint=server.endpoint,
                    prefix=prefix,
                    unsigned=True,
                    path_style=True,
                )
                remote_paths = {metadata.logical_path for metadata in backend.list_objects()}
                require("artifacts/r2.txt" in remote_paths, "R2 remote did not receive artifact")
                require("config.yaml" in remote_paths, "R2 remote did not receive safe config")

                pull = harness.run_pull(target)
                require(pull["status"] == "ok", "R2-configured pull did not complete")
                imported = target / "artifacts" / "r2.txt"
                require(imported.exists(), "R2 pull did not import artifact")
                require(
                    imported.read_text(encoding="utf-8") == "R2 fake round trip fixture.\n",
                    "R2 imported content did not match",
                )
                payload = json.dumps([metadata.as_dict() for metadata in backend.list_objects()], sort_keys=True)
                for marker in BLOCKED_MARKERS:
                    require(marker not in payload, f"R2 remote metadata included excluded marker {marker}")
                server.assert_protocol_covered()
            return "remote: r2 config pushes and pulls through a fake S3-compatible service without secrets"

        def outbox_processing() -> str:
            result = harness.run_push(device_a, remote)
            require(result["status"] == "ok", "push did not complete")
            require(result["actions"]["uploaded"] >= 2, "push did not upload config and artifact objects")
            require(result["staging"]["outbox"] >= 2, "push did not stage outbox objects")
            remote_objects = harness.list_remote_objects(remote)
            remote_paths = {obj["logical_path"] for obj in remote_objects}
            require("config.yaml" in remote_paths, "allowed config was not uploaded")
            require("artifacts/report.txt" in remote_paths, "allowed artifact was not uploaded")
            require("skills/example/SKILL.md" in remote_paths, "enabled skill scope was not uploaded")
            require("memories/notes.json" in remote_paths, "enabled memory scope was not uploaded")
            payload = json.dumps(remote_objects, sort_keys=True)
            for marker in BLOCKED_MARKERS:
                require(marker not in payload, f"remote metadata included excluded marker {marker}")
            manifest_rows = harness.manifest_objects(device_a)
            dirty = [row for row in manifest_rows if row["dirty"]]
            require(not dirty, "push left uploaded objects dirty in manifest")
            outbox_paths = harness.sync_stage_paths(device_a, "outbox")
            require(any(path.endswith("/metadata.json") for path in outbox_paths), "outbox metadata was not staged")
            require(any(path.endswith("/content") for path in outbox_paths), "outbox content was not staged")
            return "push stages outbox after scan, uploads allowed objects, and marks manifest clean"

        def inbox_staging_before_import() -> str:
            result = harness.run_pull(device_b, remote)
            require(result["status"] == "ok", "pull did not complete")
            require(result["actions"]["downloaded"] >= 2, "pull did not download remote objects")
            require(result["actions"]["imported"] >= 1, "pull did not import the remote artifact")
            phase_names = [phase["name"] for phase in result["phases"]]
            require(phase_names.index("stage_inbox") < phase_names.index("import"), "pull imported before inbox staging")
            require((device_b / "artifacts" / "report.txt").exists(), "remote artifact was not imported")
            inbox_paths = harness.sync_stage_paths(device_b, "inbox")
            require(any(path.endswith("/metadata.json") for path in inbox_paths), "inbox metadata was not staged")
            require(any(path.endswith("/content") for path in inbox_paths), "inbox content was not staged")
            return "pull stages inbox before applying imports"

        def push_idempotent() -> str:
            result = harness.run_push(device_a, remote)
            require(result["status"] == "ok", "second push did not complete")
            require(result["actions"]["uploaded"] == 0, "second push uploaded extra objects")
            require(result["staging"]["outbox"] == 0, "second push restaged clean objects")
            return "second push created no additional remote changes"

        def pull_idempotent() -> str:
            result = harness.run_pull(device_b, remote)
            require(result["status"] == "ok", "second pull did not complete")
            require(result["actions"]["downloaded"] == 0, "second pull downloaded extra objects")
            require(result["actions"]["imported"] == 0, "second pull imported extra objects")
            require(result["staging"]["inbox"] == 0, "second pull restaged clean objects")
            return "second pull created no additional local changes"

        def once_idempotent() -> str:
            once_artifact = device_a / "artifacts" / "once.txt"
            once_artifact.write_text("Once idempotency fixture.\n", encoding="utf-8")
            first = harness.run_once(device_a, remote)
            require(first["status"] == "ok", "first once did not complete")
            require(first["actions"]["uploaded"] >= 1, "first once did not upload the new artifact")
            second = harness.run_once(device_a, remote)
            require(second["status"] == "ok", "second once did not complete")
            require(second["actions"] == {"uploaded": 0, "downloaded": 0, "imported": 0, "deleted": 0}, "second once made extra changes")
            require(second["staging"]["outbox"] == 0, "second once restaged outbox objects")
            require(second["staging"]["inbox"] == 0, "second once restaged inbox objects")
            return "second once run makes no changes"

        def tombstone_delete_propagation() -> str:
            tombstone_remote = harness.make_remote("tombstone-delete")
            source = harness.make_profile("tombstone-source", tombstone_remote)
            target = harness.make_profile("tombstone-target", tombstone_remote)
            artifact = source / "artifacts" / "delete-me.txt"
            artifact.parent.mkdir(exist_ok=True)
            artifact.write_text("Tombstone fixture.\n", encoding="utf-8")
            push = harness.run_push(source, tombstone_remote)
            require(push["status"] == "ok", "initial tombstone push failed")
            pull = harness.run_pull(target, tombstone_remote)
            require(pull["status"] == "ok", "initial tombstone pull failed")
            require((target / "artifacts" / "delete-me.txt").exists(), "target artifact was not imported")

            artifact.unlink()
            delete_push = harness.run_push(source, tombstone_remote)
            require(delete_push["status"] == "ok", "delete push failed")
            require(delete_push["actions"]["deleted"] == 1, "delete push did not upload one tombstone")
            active_paths = {obj["logical_path"] for obj in harness.list_remote_objects(tombstone_remote)}
            require("artifacts/delete-me.txt" not in active_paths, "tombstoned object remained active")
            tombstones = harness.list_remote_tombstones(tombstone_remote)
            require(len(tombstones) == 1, "remote tombstone was not listed")
            delete_pull = harness.run_pull(target, tombstone_remote)
            require(delete_pull["status"] == "ok", "delete pull failed")
            require(delete_pull["actions"]["deleted"] == 1, "delete pull did not delete one local file")
            require(not (target / "artifacts" / "delete-me.txt").exists(), "target artifact survived tombstone pull")
            second_pull = harness.run_pull(target, tombstone_remote)
            require(second_pull["actions"]["deleted"] == 0, "second tombstone pull was not idempotent")
            return "deletes propagate through explicit remote and manifest tombstones"

        def text_conflict() -> str:
            conflict_remote = harness.make_remote("text-conflict")
            source = harness.make_profile("text-conflict-source", conflict_remote)
            target = harness.make_profile("text-conflict-target", conflict_remote)
            artifact = source / "artifacts" / "conflict.txt"
            artifact.parent.mkdir(exist_ok=True)
            artifact.write_text("Base text.\n", encoding="utf-8")
            harness.run_push(source, conflict_remote)
            harness.run_pull(target, conflict_remote)

            artifact.write_text("Remote text.\n", encoding="utf-8")
            harness.run_push(source, conflict_remote)
            target_artifact = target / "artifacts" / "conflict.txt"
            target_artifact.write_text("Local text.\n", encoding="utf-8")
            pull = harness.run_pull(target, conflict_remote)
            require(pull["status"] == "ok", "conflict pull failed")
            require(target_artifact.read_text(encoding="utf-8") == "Remote text.\n", "remote text did not win")
            conflicts = harness.list_conflicts(target)
            require(len(conflicts) == 1, "text conflict was not recorded once")
            tool_conflicts = harness.run_tool_conflicts(target)
            require(len(tool_conflicts["conflicts"]) == 1, "sync_list_conflicts did not report the text conflict")
            slash_conflicts = harness.run_slash_sync(target, "conflicts")
            require(conflicts[0]["conflict_id"][:12] in slash_conflicts, "/sync conflicts did not show the text conflict")
            conflict_path = target / conflicts[0]["conflict_path"]
            require(conflict_path.exists(), "text conflict copy was not written")
            require(conflict_path.read_text(encoding="utf-8") == "Local text.\n", "text conflict copy lost local content")
            require(conflicts[0]["strategy"] == "remote_wins_preserve_local_text", "text conflict strategy was not recorded")
            return "concurrent text edits preserve a local conflict copy and record a pending conflict"

        def binary_conflict() -> str:
            conflict_remote = harness.make_remote("binary-conflict")
            source = harness.make_profile("binary-conflict-source", conflict_remote)
            target = harness.make_profile("binary-conflict-target", conflict_remote)
            artifact = source / "artifacts" / "conflict.bin"
            artifact.parent.mkdir(exist_ok=True)
            artifact.write_bytes(b"\x00base\xff")
            harness.run_push(source, conflict_remote)
            harness.run_pull(target, conflict_remote)

            artifact.write_bytes(b"\x00remote\xff")
            harness.run_push(source, conflict_remote)
            target_artifact = target / "artifacts" / "conflict.bin"
            target_artifact.write_bytes(b"\x00local\xff")
            pull = harness.run_pull(target, conflict_remote)
            require(pull["status"] == "ok", "binary conflict pull failed")
            require(target_artifact.read_bytes() == b"\x00remote\xff", "remote binary did not win")
            conflicts = harness.list_conflicts(target)
            require(len(conflicts) == 1, "binary conflict was not recorded once")
            conflict_path = target / conflicts[0]["conflict_path"]
            require(conflict_path.exists(), "binary conflict copy was not written")
            require(conflict_path.read_bytes() == b"\x00local\xff", "binary conflict copy lost local content")
            require(conflicts[0]["strategy"] == "remote_wins_preserve_local_binary", "binary conflict strategy was not recorded")
            return "concurrent binary edits preserve a conflict copy while remote content wins"

        def json_structured_merge() -> str:
            merge_remote = harness.make_remote("json-structured-merge")
            source = harness.make_profile("json-merge-source", merge_remote)
            target = harness.make_profile("json-merge-target", merge_remote)
            artifact = source / "artifacts" / "settings.json"
            artifact.parent.mkdir(exist_ok=True)
            artifact.write_text(
                json.dumps(
                    {
                        "local": "base",
                        "remote": "base",
                        "shared": {"enabled": True},
                    },
                    indent=2,
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )
            harness.run_push(source, merge_remote)
            harness.run_pull(target, merge_remote)

            artifact.write_text(
                json.dumps(
                    {
                        "local": "base",
                        "remote": "remote-change",
                        "shared": {"enabled": True},
                    },
                    indent=2,
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )
            harness.run_push(source, merge_remote)
            target_artifact = target / "artifacts" / "settings.json"
            target_artifact.write_text(
                json.dumps(
                    {
                        "local": "local-change",
                        "remote": "base",
                        "shared": {"enabled": True},
                    },
                    indent=2,
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )

            pull = harness.run_pull(target, merge_remote)
            require(pull["status"] == "ok", "JSON merge pull failed")
            merged = json.loads(target_artifact.read_text(encoding="utf-8"))
            require(merged["local"] == "local-change", "JSON merge lost local change")
            require(merged["remote"] == "remote-change", "JSON merge lost remote change")
            require(not harness.list_conflicts(target), "JSON structured merge created a conflict")
            push = harness.run_push(target, merge_remote)
            require(push["actions"]["uploaded"] == 1, "JSON merged content was not pushed")
            metadata = remote_object_by_path(merge_remote, "artifacts/settings.json")
            remote_merged = json.loads(
                harness.read_remote_object_content(
                    merge_remote, metadata["scope"], metadata["object_id"]
                ).decode("utf-8")
            )
            require(remote_merged == merged, "remote did not receive JSON merged content")
            return "non-overlapping JSON object edits merge and push as a new head"

        def yaml_config_merge() -> str:
            merge_remote = harness.make_remote("yaml-config-merge")
            source = harness.make_profile("yaml-merge-source", merge_remote)
            target = harness.make_profile("yaml-merge-target", merge_remote)
            harness.run_push(source, merge_remote)
            harness.run_pull(target, merge_remote)

            source_config = source / "config.yaml"
            target_config = target / "config.yaml"
            base_config = target_config.read_text(encoding="utf-8")
            source_config.write_text(
                base_config + "\nworker:\n  interval_seconds: 5\n",
                encoding="utf-8",
            )
            harness.run_push(source, merge_remote)
            target_config.write_text(
                base_config + "\nui:\n  density: compact\n",
                encoding="utf-8",
            )

            pull = harness.run_pull(target, merge_remote)
            require(pull["status"] == "ok", "YAML config merge pull failed")
            merged_text = target_config.read_text(encoding="utf-8")
            require("worker:" in merged_text and "interval_seconds: 5" in merged_text, "YAML merge lost remote worker config")
            require("ui:" in merged_text and "density: compact" in merged_text, "YAML merge lost local UI config")
            require("remote_path:" in merged_text, "YAML merge lost sync remote path")
            require(not harness.list_conflicts(target), "YAML config merge created a conflict")
            push = harness.run_push(target, merge_remote)
            require(push["actions"]["uploaded"] == 1, "YAML merged config was not pushed")
            metadata = remote_object_by_path(merge_remote, "config.yaml")
            remote_text = harness.read_remote_object_content(
                merge_remote, metadata["scope"], metadata["object_id"]
            ).decode("utf-8")
            require("worker:" in remote_text and "ui:" in remote_text, "remote did not receive YAML merged config")
            require("watcher-state.json" not in remote_payload(merge_remote), "YAML merge leaked watcher state")
            return "non-overlapping YAML config edits merge without syncing runtime state"

        def text_three_way_merge() -> str:
            merge_remote = harness.make_remote("text-three-way-merge")
            source = harness.make_profile("text-merge-source", merge_remote)
            target = harness.make_profile("text-merge-target", merge_remote)
            artifact = source / "artifacts" / "merge.txt"
            artifact.parent.mkdir(exist_ok=True)
            artifact.write_text(
                "title\n"
                "local line: base\n"
                "remote line: base\n"
                "footer\n",
                encoding="utf-8",
            )
            harness.run_push(source, merge_remote)
            harness.run_pull(target, merge_remote)

            artifact.write_text(
                "title\n"
                "local line: base\n"
                "remote line: changed remotely\n"
                "footer\n",
                encoding="utf-8",
            )
            harness.run_push(source, merge_remote)
            target_artifact = target / "artifacts" / "merge.txt"
            target_artifact.write_text(
                "title\n"
                "local line: changed locally\n"
                "remote line: base\n"
                "footer\n",
                encoding="utf-8",
            )

            pull = harness.run_pull(target, merge_remote)
            require(pull["status"] == "ok", "text merge pull failed")
            merged_text = target_artifact.read_text(encoding="utf-8")
            require("local line: changed locally" in merged_text, "text merge lost local line")
            require("remote line: changed remotely" in merged_text, "text merge lost remote line")
            require(not harness.list_conflicts(target), "text three-way merge created a conflict")
            push = harness.run_push(target, merge_remote)
            require(push["actions"]["uploaded"] == 1, "text merged artifact was not pushed")
            metadata = remote_object_by_path(merge_remote, "artifacts/merge.txt")
            remote_text = harness.read_remote_object_content(
                merge_remote, metadata["scope"], metadata["object_id"]
            ).decode("utf-8")
            require(remote_text == merged_text, "remote did not receive text merged content")
            return "non-overlapping text edits merge and push as a new head"

        def restore_previous_version() -> str:
            restore_remote = harness.make_remote("restore-version")
            profile = harness.make_profile("restore-source", restore_remote)
            artifact = profile / "artifacts" / "restore.txt"
            artifact.parent.mkdir(exist_ok=True)
            artifact.write_text("Version one.\n", encoding="utf-8")
            harness.run_push(profile, restore_remote)
            metadata = remote_object_by_path(restore_remote, "artifacts/restore.txt")
            first_rev = metadata["remote_rev"]

            artifact.write_text("Version two.\n", encoding="utf-8")
            harness.run_push(profile, restore_remote)
            second_metadata = remote_object_by_path(restore_remote, "artifacts/restore.txt")
            require(second_metadata["remote_rev"] != first_rev, "second version did not create a new revision")
            revisions = harness.list_revisions(profile, metadata["object_id"])
            revision_ids = {row["revision_id"] for row in revisions}
            require(first_rev in revision_ids and second_metadata["remote_rev"] in revision_ids, "version history missed a revision")

            restore = harness.run_tool_restore_version(
                profile,
                object_id=metadata["object_id"],
                version_id=first_rev,
                scope="artifacts",
            )
            require(restore["status"] == "ok", "restore_version did not complete")
            require(artifact.read_text(encoding="utf-8") == "Version one.\n", "restore did not write the previous content")
            push = harness.run_push(profile, restore_remote)
            require(push["actions"]["uploaded"] == 1, "restored version was not pushed as the new head")
            restored_content = harness.read_remote_object_content(
                restore_remote, metadata["scope"], metadata["object_id"]
            ).decode("utf-8")
            require(restored_content == "Version one.\n", "remote did not receive restored content")
            return "previous artifact versions restore from local sync history and push cleanly"

        def continuous_sync() -> str:
            continuous_remote = harness.make_remote("continuous-sync")
            profile = harness.make_profile("continuous-source", continuous_remote)
            artifact = profile / "artifacts" / "continuous.txt"
            artifact.parent.mkdir(exist_ok=True)
            artifact.write_text("Continuous fixture.\n", encoding="utf-8")
            result = harness.run_continuous(
                profile,
                continuous_remote,
                interval_seconds=0.01,
                max_cycles=1,
                run_immediately=False,
            )
            require(result["status"] == "ok", "continuous worker failed")
            require(result["actions"]["uploaded"] >= 1, "continuous worker did not upload the changed artifact")
            remote_paths = {obj["logical_path"] for obj in harness.list_remote_objects(continuous_remote)}
            require("artifacts/continuous.txt" in remote_paths, "continuous artifact was not uploaded")
            require("sync/watcher-state.json" not in remote_payload(continuous_remote), "watcher state leaked to remote")
            return "continuous worker syncs an allowed change after one interval"

        def pause_state_local_only() -> str:
            pause_remote = harness.make_remote("pause-state")
            profile = harness.make_profile("pause-source", pause_remote)
            pause_output = harness.run_slash_sync(profile, "pause")
            require("paused" in pause_output.lower(), "/sync pause did not report paused state")
            artifact = profile / "artifacts" / "paused.txt"
            artifact.parent.mkdir(exist_ok=True)
            artifact.write_text("Paused fixture.\n", encoding="utf-8")
            result = harness.run_continuous(
                profile,
                pause_remote,
                interval_seconds=0.0,
                max_cycles=1,
            )
            require(result["status"] == "ok", "paused continuous worker failed")
            require(result["paused_cycles"] == 1, "paused worker did not skip the cycle")
            require(not harness.list_remote_objects(pause_remote), "paused worker uploaded data")
            state = harness.scheduler_state(profile)
            require(state["paused"] is True, "pause state was not stored locally")
            require((profile / "sync" / "watcher-state.json").exists(), "watcher state file was not written")
            require("watcher-state.json" not in remote_payload(pause_remote), "pause state leaked to remote")
            return "pause state remains local and prevents continuous uploads"

        def hook_wake_debounce() -> str:
            hook_remote = harness.make_remote("hook-wake-debounce")
            profile = harness.make_profile("hook-source", hook_remote)
            session_id = harness.seed_session_fixture(profile)
            artifact = profile / "artifacts" / "hook.txt"
            artifact.parent.mkdir(exist_ok=True)
            artifact.write_text("Hook wake fixture.\n", encoding="utf-8")

            harness.invoke_hook(profile, "on_session_end", session_id=session_id)
            for _ in range(2):
                harness.invoke_hook(
                    profile,
                    "post_tool_call",
                    tool_name="write_file",
                    args={"path": str(artifact)},
                    result='{"status":"ok"}',
                    session_id=session_id,
                    task_id="hook-task",
                )

            pending = harness.scheduler_state(profile)
            require(pending["pending_wake_count"] >= 3, "hook events did not record pending wakeups")
            require(session_id in pending["pending_sessions"], "session hook did not record a pending session")
            require("artifacts/hook.txt" in pending["pending_artifacts"], "tool hook did not record the artifact path")

            result = harness.run_continuous(
                profile,
                hook_remote,
                interval_seconds=0.0,
                max_cycles=1,
                debounce_seconds=0.01,
                poll_mtime=False,
                sync_on_idle=False,
            )
            require(result["status"] == "ok", "debounced worker failed")
            require(result["sync_cycles"] == 1, "debounced wake burst did not produce exactly one sync cycle")
            require(result["debounced_cycles"] == 1, "worker did not debounce the pending wake burst")
            remote_paths = {obj["logical_path"] for obj in harness.list_remote_objects(hook_remote)}
            require("artifacts/hook.txt" in remote_paths, "hook artifact was not uploaded")
            require(any(path.startswith("sessions/snapshots/") for path in remote_paths), "session snapshot was not uploaded")
            drained = harness.scheduler_state(profile)
            require(drained["pending_wake_count"] == 0, "pending wake count was not drained")
            require("watcher-state.json" not in remote_payload(hook_remote), "watcher state leaked to remote")
            return "session and tool hooks wake the worker and debounce into one sync cycle"

        def mtime_polling_reconcile() -> str:
            poll_remote = harness.make_remote("mtime-polling")
            profile = harness.make_profile("mtime-source", poll_remote)
            initial = harness.run_continuous(
                profile,
                poll_remote,
                interval_seconds=0.0,
                max_cycles=1,
                debounce_seconds=0.0,
                poll_mtime=True,
                sync_on_idle=False,
            )
            require(initial["status"] == "ok", "initial mtime baseline sync failed")
            require(harness.scheduler_state(profile)["mtime_poll_initialized"], "mtime baseline was not initialized")

            time.sleep(0.01)
            artifact = profile / "artifacts" / "external-edit.txt"
            artifact.parent.mkdir(exist_ok=True)
            artifact.write_text("External edit fixture.\n", encoding="utf-8")
            for dirname, filename in (
                ("logs", "agent.log"),
                ("cache", "cache.bin"),
                ("tmp", "scratch.tmp"),
                ("locks", "sync.lock"),
                ("sync", "watcher-state.json"),
            ):
                folder = profile / dirname
                folder.mkdir(exist_ok=True)
                (folder / filename).write_text("runtime only\n", encoding="utf-8")

            result = harness.run_continuous(
                profile,
                poll_remote,
                interval_seconds=0.0,
                max_cycles=1,
                debounce_seconds=0.0,
                poll_mtime=True,
                sync_on_idle=False,
            )
            require(result["status"] == "ok", "mtime polling worker failed")
            require(result["poll_changes"] >= 1, "mtime polling did not detect the external edit")
            require(result["sync_cycles"] == 1, "mtime polling did not run one reconcile cycle")
            remote_paths = {obj["logical_path"] for obj in harness.list_remote_objects(poll_remote)}
            require("artifacts/external-edit.txt" in remote_paths, "polled artifact was not uploaded")
            payload = remote_payload(poll_remote)
            for marker in ("agent.log", "cache.bin", "scratch.tmp", "sync.lock", "watcher-state.json"):
                require(marker not in payload, f"runtime marker leaked through mtime polling: {marker}")
            return "allowlisted mtime polling reconciles external edits without runtime state"

        def sync_lock_single_flight() -> str:
            lock_remote = harness.make_remote("single-flight")
            profile = harness.make_profile("single-flight-source", lock_remote)
            artifact = profile / "artifacts" / "single-flight.txt"
            artifact.parent.mkdir(exist_ok=True)
            artifact.write_text("Single flight fixture.\n", encoding="utf-8")
            harness.note_tool_changed(
                profile,
                tool_name="write_file",
                artifact_paths=["artifacts/single-flight.txt"],
            )

            import hermes_sync.scheduler as scheduler_mod

            original_run_once = scheduler_mod.run_once
            first_started = threading.Event()
            release_first = threading.Event()
            results: list[dict] = []
            failures: list[str] = []

            def slow_run_once(profile_arg, remote_arg):
                first_started.set()
                release_first.wait(1.0)
                return original_run_once(profile_arg, remote_arg)

            def worker() -> None:
                try:
                    results.append(
                        harness.run_continuous(
                            profile,
                            lock_remote,
                            interval_seconds=0.0,
                            max_cycles=1,
                            debounce_seconds=0.0,
                            poll_mtime=False,
                            sync_on_idle=False,
                        )
                    )
                except Exception as exc:
                    failures.append(str(exc))

            scheduler_mod.run_once = slow_run_once
            try:
                first = threading.Thread(target=worker)
                first.start()
                require(first_started.wait(1.0), "first worker did not enter sync")
                second = threading.Thread(target=worker)
                second.start()
                time.sleep(0.02)
                release_first.set()
                first.join(1.0)
                second.join(1.0)
            finally:
                release_first.set()
                scheduler_mod.run_once = original_run_once

            require(not failures, f"single-flight worker failed: {failures}")
            require(len(results) == 2, "single-flight did not collect both worker results")
            require(sum(result["sync_cycles"] for result in results) == 1, "workers ran overlapping sync cycles")
            require(sum(result["locked_cycles"] for result in results) >= 1, "second worker did not observe the local sync lock")
            require(not (profile / "sync" / "sync.lock").exists(), "local sync lock was left behind")
            remote_paths = {obj["logical_path"] for obj in harness.list_remote_objects(lock_remote)}
            require("artifacts/single-flight.txt" in remote_paths, "single-flight artifact was not uploaded")
            require("sync.lock" not in remote_payload(lock_remote), "sync lock leaked to remote")
            return "local sync lock prevents overlapping continuous sync cycles"

        def pause_resume_drains_pending() -> str:
            resume_remote = harness.make_remote("pause-resume")
            profile = harness.make_profile("pause-resume-source", resume_remote)
            pause_output = harness.run_slash_sync(profile, "pause")
            require("paused" in pause_output.lower(), "pause command did not pause the worker")
            artifact = profile / "artifacts" / "pending.txt"
            artifact.parent.mkdir(exist_ok=True)
            artifact.write_text("Pending while paused.\n", encoding="utf-8")
            harness.invoke_hook(
                profile,
                "post_tool_call",
                tool_name="write_file",
                args={"path": "artifacts/pending.txt"},
                result='{"status":"ok"}',
                session_id="paused-session",
            )

            paused = harness.run_continuous(
                profile,
                resume_remote,
                interval_seconds=0.0,
                max_cycles=1,
                debounce_seconds=0.0,
                poll_mtime=False,
                sync_on_idle=False,
            )
            require(paused["paused_cycles"] == 1, "paused worker did not skip pending work")
            require(not harness.list_remote_objects(resume_remote), "paused worker uploaded pending data")
            pending = harness.scheduler_state(profile)
            require(pending["pending_wake_count"] >= 1, "pending wake was not retained while paused")

            resume_output = harness.run_slash_sync(profile, "resume")
            require("resumed" in resume_output.lower(), "resume command did not resume the worker")
            resumed = harness.run_continuous(
                profile,
                resume_remote,
                interval_seconds=0.0,
                max_cycles=1,
                debounce_seconds=0.0,
                poll_mtime=False,
                sync_on_idle=False,
            )
            require(resumed["sync_cycles"] == 1, "resume did not drain pending work with one sync")
            remote_paths = {obj["logical_path"] for obj in harness.list_remote_objects(resume_remote)}
            require("artifacts/pending.txt" in remote_paths, "pending artifact was not uploaded after resume")
            drained = harness.scheduler_state(profile)
            require(drained["pending_wake_count"] == 0, "pending wake was not cleared after resume drain")
            require("watcher-state.json" not in remote_payload(resume_remote), "pause state leaked to remote")
            return "paused wake events stay local and resume drains them with one sync"

        scenarios: list[tuple[str, Callable[[], str]]] = [
            ("plugin_manifest_loads", plugin_manifest_loads),
            ("slash_status_readonly", slash_status_readonly),
            ("slash_router_parity", slash_router_parity),
            ("tool_schema_registration", tool_schema_registration),
            ("tool_readonly_status", tool_readonly_status),
            ("setup_creates_device_identity", setup_creates_device_identity),
            ("manifest_schema_created", manifest_schema_created),
            ("manifest_excludes_blocked_paths", manifest_excludes_blocked_paths),
            ("path_allowlist", path_allowlist),
            ("ignore_rules", ignore_rules),
            ("traversal_rejection", traversal_rejection),
            ("symlink_escape_rejection", symlink_escape_rejection),
            ("empty_profiles", empty_profiles),
            ("config_export", config_export),
            ("secret_exclusion", secret_exclusion),
            ("configured_scope_disable_sessions", configured_scope_disable_sessions),
            ("db_file_exclusion", db_file_exclusion),
            ("artifact_push_pull", artifact_push_pull),
            ("memory_skills_plugins_push_pull", memory_skills_plugins_push_pull),
            ("runtime_file_exclusion", runtime_file_exclusion),
            ("session_snapshot", session_snapshot),
            ("local_remote_object_round_trip", local_remote_object_round_trip),
            ("backend_conformance", backend_conformance),
            ("oss_backend_conformance", oss_backend_conformance),
            ("oss_sync_config_round_trip", oss_sync_config_round_trip),
            ("webdav_backend_conformance", webdav_backend_conformance),
            ("webdav_sync_config_round_trip", webdav_sync_config_round_trip),
            ("s3_backend_conformance", s3_backend_conformance),
            ("r2_sync_config_round_trip", r2_sync_config_round_trip),
            ("outbox_processing", outbox_processing),
            ("inbox_staging_before_import", inbox_staging_before_import),
            ("push_idempotent", push_idempotent),
            ("pull_idempotent", pull_idempotent),
            ("once_idempotent", once_idempotent),
            ("tombstone_delete_propagation", tombstone_delete_propagation),
            ("text_conflict", text_conflict),
            ("binary_conflict", binary_conflict),
            ("json_structured_merge", json_structured_merge),
            ("yaml_config_merge", yaml_config_merge),
            ("text_three_way_merge", text_three_way_merge),
            ("restore_previous_version", restore_previous_version),
            ("continuous_sync", continuous_sync),
            ("pause_state_local_only", pause_state_local_only),
            ("hook_wake_debounce", hook_wake_debounce),
            ("mtime_polling_reconcile", mtime_polling_reconcile),
            ("sync_lock_single_flight", sync_lock_single_flight),
            ("pause_resume_drains_pending", pause_resume_drains_pending),
        ]
        for scenario_id, fn in scenarios:
            results.append(_result(scenario_id, fn))

        trace_path = harness.write_trace(results)
        status = "completed" if all(r.status == "complete" for r in results) else "failed"
        return {
            "status": status,
            "harness": "phase5",
            "scenario_count": len(results),
            "trace": str(trace_path),
            "results": [r.as_dict() for r in results],
        }


def main() -> int:
    result = run()
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["status"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
