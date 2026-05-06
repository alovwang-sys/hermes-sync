# Progress

Last updated: 2026-05-07

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
| Manual | The contract is documented with a gated manual runner or checklist and intentionally requires external resources outside the default harness. |
| Blocked | Progress depends on a missing hook, decision, or external dependency. |

## Current Summary

| Area | Status | Notes |
| --- | --- | --- |
| Repository instructions | Complete | `AGENTS.md` is canonical; `AGENT.md` is a compatibility pointer. |
| Plugin metadata | Complete | `plugin.yaml` loads through Hermes `PluginManager` in the isolated harness. |
| Architecture contract | Specified | Object model, backend contract, session snapshot approach, and conflict model are documented. |
| Sync scopes | Complete | Scope allowlist, default exclusions, import policy, and configured scope disabling are covered by the harness. |
| Harness contract | In progress | Process is documented and Phase 5 local-folder, OSS fake, WebDAV fake, and S3/R2 fake scenarios run with temporary profiles and remotes. |
| Feature inventory | Specified | `feature_list.json` is machine-readable; `docs/feature-list.md` is the human companion. |
| Test deployment guide | Complete | `scripts/install_dev_plugin.py` installs the checkout as a directory plugin; `scripts/real_deployment_smoke.py` verifies a guarded real-profile local remote smoke; `docs/deployment.md` documents local-first deployment and OSS credential handling. |
| Hermes Agent integration | Specified | Local checkout `/home/amos/project/hermes-agent` at `32ae91091` was reviewed. |
| Executable plugin package | Complete through Phase 4 merge plus Phase 5 S3/R2 fake conformance | `hermes_sync/` registers `/sync` and `sync_*` tools; local-folder sync now covers config, artifacts, memory, skills, plugin manifests, snapshots, tombstones, conflict records, structured/text merge, version restore, continuous auto-triggering, reusable backend conformance for the reference backend, OSS, WebDAV, and S3/R2 via the `RemoteBackend` protocol. OSS, WebDAV, and S3/R2 implementation completion is against fake conformance only. |
| Executable harness | Complete through Phase 5 S3/R2 fake conformance | `python3 -m harness.run` validates 47 scenarios in isolated temp roots, including configured scope disabling, memory/skills/plugin-manifest round trips, fake OSS/WebDAV/S3 backend protocol conformance, and `remote: oss`/`remote: webdav`/`remote: r2` config round trips. |

## Milestones

| Milestone | Status | Exit Criteria |
| --- | --- | --- |
| Phase 0: Documentation and harness contract | Specified | README, architecture, scopes, harness, `feature_list.json`, feature list, progress, and implementation plan stay aligned with `plugin.yaml`. |
| Phase 1: Plugin skeleton | Complete | `/sync status` and `sync_status` run on isolated profiles; manifest schema exists; exclusions are not reported. |
| Phase 2: Local remote MVP | Complete | Local-folder backend, outbox/inbox staging, idempotent `push`, `pull`, and `once`, config/artifact sync, and session snapshots work in the isolated harness. |
| Phase 3: Continuous sync | Complete | Changes sync after one interval, hook wakeups debounce into one cycle, mtime polling reconciles allowlisted edits, and runtime state is never uploaded. |
| Phase 3: Auto-trigger upgrade | Complete | Hook wakeups, debounce, local single-flight locking, allowlisted mtime polling, and pause/resume pending drain are executable in the harness. |
| Phase 4: Conflict and history | Complete through structured/text merge | Concurrent edits, tombstones, conflict listing, structured JSON/YAML merge, text three-way merge, and restore are deterministic for local-folder config/artifact objects. |
| Phase 5: Remote backends | In progress | The reusable backend conformance harness passes against local-folder, OSS fake behavior, WebDAV fake behavior, and S3/R2 fake behavior, including backend protocol subsets; live Alibaba Cloud acceptance is specified/manual and has not been run against real cloud storage here; Git remains future work. |
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
| OSS backend conformance | Specified | Complete against fake OSS | OSS backend passes the same reusable conformance checks against a temporary in-memory fake OSS HTTP service that validates path-style requests, ListObjectsV2, required compatibility headers, and payload hashes. |
| OSS sync config round trip | Specified | Complete against fake OSS | A `remote: oss` profile pushes and pulls allowed config/artifact objects through fake OSS without credentials or runtime state. |
| WebDAV backend conformance | Specified | Complete against fake WebDAV | WebDAV backend passes the same reusable conformance checks against a temporary in-memory fake WebDAV HTTP service that validates MKCOL, PROPFIND, PUT, GET, and DELETE behavior. |
| WebDAV sync config round trip | Specified | Complete against fake WebDAV | A `remote: webdav` profile pushes and pulls allowed config/artifact objects through fake WebDAV without credentials or runtime state. |
| S3 backend conformance | Specified | Complete against fake S3 | The generic S3-compatible backend passes the same reusable conformance checks against a temporary in-memory fake S3 HTTP service that validates the unsigned S3-compatible subset. |
| R2 sync config round trip | Specified | Complete against fake S3 | A `remote: r2` profile pushes and pulls allowed config/artifact objects through fake S3-compatible storage without credentials or runtime state. |
| OSS live acceptance | Manual | Gated runner exists; not run by default | `python3 -m harness.oss_live_acceptance` requires an intentionally supplied Alibaba Cloud OSS bucket, isolated prefix, and local environment credentials. |
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

Result: completed on 2026-05-07.

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
- `configured_scope_disable_sessions`
- `db_file_exclusion`
- `artifact_push_pull`
- `memory_skills_plugins_push_pull`
- `runtime_file_exclusion`
- `session_snapshot`
- `local_remote_object_round_trip`
- `backend_conformance`
- `oss_backend_conformance`
- `oss_sync_config_round_trip`
- `webdav_backend_conformance`
- `webdav_sync_config_round_trip`
- `s3_backend_conformance`
- `r2_sync_config_round_trip`
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

1. Add Git fake conformance before any live backend acceptance.
2. Run the gated live OSS acceptance command manually for an intentionally
   supplied Alibaba Cloud bucket and isolated prefix.
3. Keep top-level `hermes sync` blocked until Hermes core exposes a generic
   plugin CLI bridge.

## Harness Definition Of Complete

The harness is complete when it can run all required scenarios from
`feature_list.json` and `docs/harness.md` using only temporary profiles and
temporary remotes, can prove idempotent `push`, `pull`, and `once`, and can
show that excluded paths and secrets never enter manifests, remotes, fixtures,
traces, or docs examples.
