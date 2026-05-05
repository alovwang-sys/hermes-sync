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
| Harness contract | In progress | Process is documented and Phase 2 local-folder MVP scenarios run with temporary profiles and remotes. |
| Feature inventory | Specified | `feature_list.json` is machine-readable; `docs/feature-list.md` is the human companion. |
| Hermes Agent integration | Specified | Local checkout `/home/amos/project/hermes-agent` at `32ae91091` was reviewed. |
| Executable plugin package | Complete through Phase 2 MVP | `hermes_sync/` registers `/sync` and `sync_*` tools; `sync_status` is read-only and `sync_now` runs one local-folder cycle for supported config/artifact scopes. |
| Executable harness | Complete through Phase 2 MVP | `python -m harness.run` validates 19 scenarios in isolated temp roots. |

## Milestones

| Milestone | Status | Exit Criteria |
| --- | --- | --- |
| Phase 0: Documentation and harness contract | Specified | README, architecture, scopes, harness, `feature_list.json`, feature list, progress, and implementation plan stay aligned with `plugin.yaml`. |
| Phase 1: Plugin skeleton | Complete | `/sync status` and `sync_status` run on isolated profiles; manifest schema exists; exclusions are not reported. |
| Phase 2: Local remote MVP | In progress | Local-folder backend, outbox/inbox staging, and idempotent `push`, `pull`, and `once` work for allowed config/artifact objects; session snapshots remain next. |
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
| Config export | Specified | Complete for MVP | Allowed `config.yaml` appears in outbox and local-folder remote; dedicated key-level secret scenario remains. |
| Secret exclusion | Specified | Not started | `.env` and token-like keys are skipped everywhere. |
| DB exclusion | Specified | Not started | `state.db`, WAL, and SHM files are skipped everywhere. |
| Artifact push/pull | Specified | Complete for MVP | Text artifact arrives on second device through inbox staging; runtime-file-specific scenario remains. |
| Session snapshot | Specified | Not started | Session JSON is exported instead of SQLite files. |
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
- `local_remote_object_round_trip`
- `outbox_processing`
- `inbox_staging_before_import`
- `push_idempotent`
- `pull_idempotent`
- `once_idempotent`

## Immediate Next Work

1. Add dedicated `config_export` and `secret_exclusion` harness scenarios for
   sanitized config handling.
2. Add dedicated `artifact_push_pull` and `runtime_file_exclusion` scenarios
   around post-tool artifacts.
3. Export session snapshots from SQLite read-only APIs without syncing
   `state.db`, `state.db-wal`, or `state.db-shm`.
4. Implement tombstone propagation and conflict creation on top of the
   local-folder backend.
5. Keep top-level `hermes sync` blocked until Hermes core exposes a generic
   plugin CLI bridge.

## Harness Definition Of Complete

The harness is complete when it can run all required scenarios from
`feature_list.json` and `docs/harness.md` using only temporary profiles and
temporary remotes, can prove idempotent `push`, `pull`, and `once`, and can
show that excluded paths and secrets never enter manifests, remotes, fixtures,
traces, or docs examples.
