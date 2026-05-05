"""Run the first executable hermes-sync harness scenarios."""

from __future__ import annotations

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
        ]
        for scenario_id, fn in scenarios:
            results.append(_result(scenario_id, fn))

        trace_path = harness.write_trace(results)
        status = "completed" if all(r.status == "complete" for r in results) else "failed"
        return {
            "status": status,
            "harness": "phase1",
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
