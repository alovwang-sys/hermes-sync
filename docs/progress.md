# Progress

Last updated: 2026-05-06

This document tracks implementation and harness progress. Update it whenever a
milestone changes, a harness scenario moves status, `feature_list.json`
changes, or `plugin.yaml` command surface changes.

## Status Values

| Status | Meaning |
| --- | --- |
| Not started | No implementation or executable harness work exists yet. |
| Specified | The contract is documented but not executable. |
| In progress | Code, tests, or harness work has started. |
| Complete | Implementation exists and required harness coverage passes. |
| Blocked | Progress depends on a missing hook, decision, or external dependency. |

## Current Summary

| Area | Status | Notes |
| --- | --- | --- |
| Repository instructions | Complete | `AGENTS.md` is canonical; `AGENT.md` is a compatibility pointer. |
| Plugin metadata | Complete | `plugin.yaml` loads through Hermes `PluginManager` in the isolated harness. |
| Architecture contract | Specified | Object model, backend contract, session snapshot approach, and conflict model are documented. |
| Sync scopes | Specified | Scope allowlist, default exclusions, and import policy are documented. |
| Harness contract | In progress | Process is documented and Phase 2 local-folder snapshot scenarios run with temporary profiles and remotes. |
| Feature inventory | Specified | `feature_list.json` is machine-readable; `docs/feature-list.md` is the human companion. |
| Hermes Agent integration | Specified | Local checkout `/home/amos/project/hermes-agent` at `32ae91091` was reviewed. |
| Executable plugin package | Complete through Phase 4 merge plus Phase 5 OSS fake conformance | `hermes_sync/` registers `/sync` and `sync_*` tools; local-folder sync now covers snapshots, tombstones, conflict records, structured/text merge, version restore, continuous auto-triggering, reusable backend conformance for the reference backend, and OSS via the `RemoteBackend` protocol. |
| Executable harness | Complete through Phase 5 OSS fake conformance | `python3 -m harness.run` validates 41 scenarios in isolated temp roots, including fake OSS backend and `remote: oss` config round trip. |

## Milestones

| Milestone | Status | Exit Criteria |
| --- | --- | --- |
| Phase 0: Documentation and harness contract | Specified | README, architecture, scopes, harness, `feature_list.json`, feature list, progress, and implementation plan stay aligned with `plugin.yaml`. |
| Phase 1: Plugin skeleton | Complete | `/sync status` and `sync_status` run on isolated profiles; manifest schema exists; exclusions are not reported. |
| Phase 2: Local remote MVP | Complete | Local-folder backend, outbox/inbox staging, idempotent `push`, `pull`, and `once`, config/artifact sync, and session snapshots work in the isolated harness. |
| Phase 3: Continuous sync | Complete | Changes sync after one interval, hook wakeups debounce into one cycle, mtime polling reconciles allowlisted edits, and runtime state is never uploaded. |
| Phase 3: Auto-trigger upgrade | Complete | Hook wakeups, debounce, local single-flight locking, allowlisted mtime polling, and pause/resume pending drain are executable in the harness. |
| Phase 4: Conflict and history | Complete through structured/text merge | Concurrent edits, tombstones, conflict listing, structured JSON/YAML merge, text three-way merge, and restore are deterministic for local-folder config/artifact objects. |
| Phase 5: Remote backends | In progress | The reusable backend conformance harness passes against local-folder and OSS fake behavior; live OSS acceptance has a gated runner but has not been run against real cloud storage here; Git, WebDAV, and generic S3/R2 implementations remain future work. |
| Phase 6: Core hook extensions | Blocked | Generic hooks are proposed only after snapshot sync is stable. |

## Harness Scenario Progress

`feature_list.json` contains the canonical executable scenario list. This
table summarizes the main scenario groups for planning.

| Scenario | Contract Status | Test Status | Notes |
| --- | --- | --- | --- |
| Empty profiles | Specified | Complete | `status` reports no dirty objects on an isolated empty profile. |
| Config export | Specified | Complete | Allowed `config.yaml` appears in outbox and local-folder remote. |
| Secret exclusion | Specified | Complete | `.env`, credential files, and token-like config keys are skipped everywhere. |
| DB exclusion | Specified | Complete | `state.db`, WAL, and SHM files are skipped everywhere as files. |
| Artifact push/pull | Specified | Complete | Text artifact arrives on the second device through inbox staging. |
| Runtime file exclusion | Specified | Complete | Logs, caches, tmp files, lock files, and watcher state remain local. |
| Session snapshot | Specified | Complete | Session JSON is exported through read-only SQLite and stored as plugin-owned history instead of syncing SQLite files. |
| Backend conformance | Specified | Complete for local-folder reference | Local-folder backend passes reusable object, tombstone, idempotency, and path-safety conformance checks. |
| OSS backend conformance | Specified | Complete against fake OSS | OSS backend passes the same reusable conformance checks against a temporary in-memory fake OSS HTTP service. |
| OSS sync config round trip | Specified | Complete against fake OSS | A `remote: oss` profile pushes and pulls allowed config/artifact objects through fake OSS without credentials or runtime state. |
| OSS live acceptance | In progress | Gated runner exists; not run by default | `python3 -m harness.oss_live_acceptance` requires an intentionally supplied Alibaba Cloud OSS bucket, isolated prefix, and local environment credentials. |
| Idempotent `once` | Specified | Complete | Second run makes no changes. |
| Tombstone | Specified | Complete | Delete creates a tombstone and imports as delete. |
| Text conflict | Specified | Complete | Overlapping text edits fall back to remote content while a plugin-owned conflict copy preserves local text. |
| Binary conflict | Specified | Complete | Remote content wins and a binary conflict copy is preserved. |
| JSON structured merge | Specified | Complete | Non-overlapping JSON object edits merge and push as a new head. |
| YAML config merge | Specified | Complete | Non-overlapping YAML config edits merge without syncing runtime state. |
| Text three-way merge | Specified | Complete | Non-overlapping text line edits merge and push as a new head. |
| Path traversal rejection | Specified | Complete | Traversal and symlink escapes are rejected before hashing. |
| Manifest updates | Specified | Complete for MVP | Manifest schema exists and object rows move dirty-to-clean after successful push/pull. |
| Outbox/inbox processing | Specified | Complete | Push stages outbox before upload; pull stages inbox before import. |
| Continuous sync | Specified | Complete | Interval loop syncs allowed changes and pause state stays local. |
| Hook wake debounce | Specified | Complete | Hooks wake the worker and coalesce rapid events into one sync cycle. |
| Mtime polling reconcile | Specified | Complete | External allowlisted edits sync without uploading runtime state. |
| Sync lock single-flight | Specified | Complete | Concurrent wake events do not overlap sync cycles. |
| Pause/resume drains pending | Specified | Complete | Paused wake events stay local and resume runs one catch-up cycle. |
| Version restore | Specified | Complete | Previous artifact versions restore from `sync/versions` and push as the new head. |

## Last Executed Harness

Command:

```bash
python3 -m harness.run
```

Result: completed on 2026-05-06.

Completed scenarios:

- `plugin_manifest_loads`
- `slash_status_readonly`
- `slash_router_parity`
- `tool_schema_registration`
- `tool_readonly_status`
- `setup_creates_device_identity`
- `manifest_schema_created`
- `manifest_excludes_blocked_paths`
- `path_allowlist`
- `ignore_rules`
- `traversal_rejection`
- `symlink_escape_rejection`
- `empty_profiles`
- `config_export`
- `secret_exclusion`
- `db_file_exclusion`
- `artifact_push_pull`
- `runtime_file_exclusion`
- `session_snapshot`
- `local_remote_object_round_trip`
- `backend_conformance`
- `oss_backend_conformance`
- `oss_sync_config_round_trip`
- `outbox_processing`
- `inbox_staging_before_import`
- `push_idempotent`
- `pull_idempotent`
- `once_idempotent`
- `tombstone_delete_propagation`
- `text_conflict`
- `binary_conflict`
- `json_structured_merge`
- `yaml_config_merge`
- `text_three_way_merge`
- `restore_previous_version`
- `continuous_sync`
- `pause_state_local_only`
- `hook_wake_debounce`
- `mtime_polling_reconcile`
- `sync_lock_single_flight`
- `pause_resume_drains_pending`

## Immediate Next Work

1. Run the gated live OSS acceptance command for an intentionally supplied
   Alibaba Cloud bucket and isolated prefix.
2. Keep top-level `hermes sync` blocked until Hermes core exposes a generic
   plugin CLI bridge.

## Harness Definition Of Complete

The harness is complete when it can run all required scenarios from
`feature_list.json` and `docs/harness.md` using only temporary profiles and
temporary remotes, can prove idempotent `push`, `pull`, and `once`, and can
show that excluded paths and secrets never enter manifests, remotes, fixtures,
traces, or docs examples.
