"""Run executable hermes-sync harness scenarios."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Callable

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

        def outbox_processing() -> str:
            result = harness.run_push(device_a, remote)
            require(result["status"] == "ok", "push did not complete")
            require(result["actions"]["uploaded"] >= 2, "push did not upload config and artifact objects")
            require(result["staging"]["outbox"] >= 2, "push did not stage outbox objects")
            remote_objects = harness.list_remote_objects(remote)
            remote_paths = {obj["logical_path"] for obj in remote_objects}
            require("config.yaml" in remote_paths, "allowed config was not uploaded")
            require("artifacts/report.txt" in remote_paths, "allowed artifact was not uploaded")
            require("skills/example/SKILL.md" not in remote_paths, "unsupported skill scope was uploaded")
            require("memories/notes.json" not in remote_paths, "unsupported memory scope was uploaded")
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
            ("db_file_exclusion", db_file_exclusion),
            ("artifact_push_pull", artifact_push_pull),
            ("runtime_file_exclusion", runtime_file_exclusion),
            ("session_snapshot", session_snapshot),
            ("local_remote_object_round_trip", local_remote_object_round_trip),
            ("outbox_processing", outbox_processing),
            ("inbox_staging_before_import", inbox_staging_before_import),
            ("push_idempotent", push_idempotent),
            ("pull_idempotent", pull_idempotent),
            ("once_idempotent", once_idempotent),
        ]
        for scenario_id, fn in scenarios:
            results.append(_result(scenario_id, fn))

        trace_path = harness.write_trace(results)
        status = "completed" if all(r.status == "complete" for r in results) else "failed"
        return {
            "status": status,
            "harness": "phase2",
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
