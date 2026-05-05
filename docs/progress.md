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
| Executable plugin package | Complete through Phase 2 snapshots | `hermes_sync/` registers `/sync` and `sync_*` tools; `sync_status` is read-only and `sync_now` runs one local-folder cycle for supported config, artifact, and session snapshot scopes. |
| Executable harness | Complete through Phase 2 snapshots | `python -m harness.run` validates 25 scenarios in isolated temp roots. |

## Milestones

| Milestone | Status | Exit Criteria |
| --- | --- | --- |
| Phase 0: Documentation and harness contract | Specified | README, architecture, scopes, harness, `feature_list.json`, feature list, progress, and implementation plan stay aligned with `plugin.yaml`. |
| Phase 1: Plugin skeleton | Complete | `/sync status` and `sync_status` run on isolated profiles; manifest schema exists; exclusions are not reported. |
| Phase 2: Local remote MVP | Complete | Local-folder backend, outbox/inbox staging, idempotent `push`, `pull`, and `once`, config/artifact sync, and session snapshots work in the isolated harness. |
| Phase 3: Continuous sync | Not started | Changes sync after one interval and runtime state is never uploaded. |
| Phase 4: Conflict and history | Not started | Concurrent edits, tombstones, conflict listing, and restore are deterministic. |
| Phase 5: Remote backends | Not started | Git, WebDAV, and S3/R2 pass the same conformance suite as local folder. |
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
| Idempotent `once` | Specified | Complete | Second run makes no changes. |
| Tombstone | Specified | Not started | Delete creates a tombstone and imports as delete. |
| Text conflict | Specified | Not started | Failed merge creates a conflict file. |
| Binary conflict | Specified | Not started | Latest file wins and a conflict copy is preserved. |
| Path traversal rejection | Specified | Complete | Traversal and symlink escapes are rejected before hashing. |
| Manifest updates | Specified | Complete for MVP | Manifest schema exists and object rows move dirty-to-clean after successful push/pull. |
| Outbox/inbox processing | Specified | Complete | Push stages outbox before upload; pull stages inbox before import. |
| Continuous sync | Specified | Not started | Watcher or interval loop syncs allowed changes only. |

## Last Executed Harness

Command:

```bash
env PYTHONDONTWRITEBYTECODE=1 python -m harness.run
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
- `outbox_processing`
- `inbox_staging_before_import`
- `push_idempotent`
- `pull_idempotent`
- `once_idempotent`

## Immediate Next Work

1. Implement tombstone propagation and conflict creation on top of the
   local-folder backend.
2. Add version-history restore coverage once tombstones and conflicts are
   deterministic.
3. Start Phase 3 continuous sync scheduling and keep watcher state local.
4. Keep top-level `hermes sync` blocked until Hermes core exposes a generic
   plugin CLI bridge.

## Harness Definition Of Complete

The harness is complete when it can run all required scenarios from
`feature_list.json` and `docs/harness.md` using only temporary profiles and
temporary remotes, can prove idempotent `push`, `pull`, and `once`, and can
show that excluded paths and secrets never enter manifests, remotes, fixtures,
traces, or docs examples.
