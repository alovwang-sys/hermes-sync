# Implementation Plan

## Phase 0: Documentation and Harness Contract

- Keep `AGENTS.md` as the canonical project instruction file.
- Keep `AGENT.md` as a compatibility pointer only.
- Define architecture, sync scopes, and harness behavior.
- Maintain `feature_list.json` as the machine-readable feature inventory,
  progress source, and harness execution contract.
- Maintain `docs/hermes-agent-integration.md` as the local Hermes core
  compatibility record.
- Maintain `docs/feature-list.md` as the product and harness feature matrix.
- Maintain `docs/progress.md` as the current implementation and harness
  progress tracker.
- Keep command examples aligned with `plugin.yaml`.

Exit criteria:

- README links to architecture, harness, feature list, progress, and milestone
  docs.
- `feature_list.json` validates with `python3 -m json.tool`.
- Harness required scenarios are defined in `feature_list.json` and summarized
  in `docs/harness.md` and `docs/progress.md`.
- Every planned command or tool in docs exists in `plugin.yaml` or is clearly
  marked as future work.

## Phase 1: Plugin Skeleton

- Create plugin package structure.
- Register `/sync status`.
- Register `sync_status`.
- Create `device.json`.
- Create `manifest.sqlite`.
- Implement read-only scope scan.
- Do not upload, import, delete, or mutate user data.
- Track top-level `hermes sync status` as blocked until Hermes core exposes
  generic plugin CLI-command registration before argparse parsing.

Exit criteria:

- `/sync status` and `sync_status` run on an empty isolated profile.
- Excluded files are not reported.
- Manifest schema is created and queryable.

## Phase 2: Local Remote MVP

- Implement local folder backend.
- Implement `push`, `pull`, and `once`.
- Export safe config objects.
- Export artifacts.
- Stage push objects under `sync/outbox`.
- Stage pull objects under `sync/inbox` before import.
- Export session snapshots from `state.db` through read-only SQLite queries.
- Import pulled objects into inbox first, then apply.

Exit criteria:

- Two temporary profiles can exchange allowed config, artifacts, and session
  snapshot JSON through a local-folder remote.
- Repeated `push`, `pull`, and `once` runs are idempotent.
- No blocked paths or runtime files enter manifests, remotes, or traces.
- Secret-like config export is guarded with dedicated key-level harness
  coverage.
- Opt-in session snapshots move between profiles without syncing SQLite files.

## Phase 3: Continuous Sync

- Implement a plugin continuous sync worker. Complete with a bounded worker
  around `once`, hook wakeups, debounce, allowlisted mtime polling, and local
  single-flight locking.
- Add watcher or periodic scan loop. Complete with allowlisted mtime polling;
  filesystem watcher integration can come later if it stays app-aware.
- Use `on_session_end` to enqueue snapshots. Complete for local wake state
  without uploading hook state.
- Use `post_tool_call` to detect important artifacts. Complete for
  artifact-producing path hints that resolve inside allowed artifact roots.
- Keep pause/resume state local to the device. Complete with
  `sync/watcher-state.json`.
- Keep `hermes sync --continuous` as future top-level CLI work until the
  generic plugin CLI bridge exists.

Phase 3 auto-trigger scope is complete:

- Hook wakeups for `on_session_end` and artifact-producing tool events.
- Short debounce so bursts of events produce one sync cycle.
- A local single-flight sync lock under plugin-owned state.
- Allowlisted mtime polling for config and artifact scopes as a fallback
  for edits made outside Hermes.
- Keep all worker, lock, debounce, and pending state local under `sync/`.

Exit criteria:

- A changed artifact is pushed automatically.
- Session end creates an outbox snapshot.
- Runtime state is not uploaded.
- Concurrent wake events do not overlap sync cycles.
- Pause stores pending work locally and resume drains it with one catch-up run.

## Phase 4: Conflict and History

- Implement tombstones. Complete for local-folder config/artifact objects.
- Store version metadata and local version contents under `sync/versions`.
- Implement `/sync conflicts`.
- Implement `sync_list_conflicts`.
- Implement `sync_restore_version`.
- Add JSON/YAML structured merge. Complete for non-overlapping object-key
  edits, with conflict-copy fallback for overlapping edits.
- Add text merge and conflict-file fallback. Complete for non-overlapping
  same-line-count UTF-8 artifact edits, with conflict-copy fallback for
  overlapping edits and binary content.
- Keep `hermes sync conflicts` and `hermes sync restore` as future top-level
  CLI work until the generic plugin CLI bridge exists.

Exit criteria:

- Concurrent edits produce deterministic conflict records.
- Non-overlapping JSON/YAML and text edits merge and push as a new head.
- Tombstoned objects do not reappear unexpectedly.
- Restore can recover a previous version.

## Phase 5: Remote Backends

- Add reusable backend conformance harness. Complete for the local-folder
  reference backend.
- Add Alibaba Cloud OSS backend using the OSS S3-compatible API. Complete for
  fake OSS backend conformance and `remote: oss` sync-engine config round trip.
  The implementation-complete contract is explicitly limited to the local fake
  conformance server until live acceptance is run.
- Keep OSS credentials local-only through environment variables; profile config
  may contain bucket, endpoint, region, and prefix but not access keys.
- Add gated live OSS acceptance for an isolated bucket prefix when credentials
  are intentionally supplied. Runner exists as `python3 -m
  harness.oss_live_acceptance`; live execution is specified/manual, requires
  user-provided cloud credentials, and is outside the default harness.
- Add WebDAV backend. Complete for fake WebDAV backend conformance and
  `remote: webdav` sync-engine config round trip. The implementation-complete
  contract is explicitly limited to the local fake conformance server until any
  live WebDAV acceptance is separately specified.
- Add generic S3/R2 backend. Complete for fake S3-compatible backend
  conformance and `remote: r2` sync-engine config round trip. The
  implementation-complete contract is explicitly limited to the local fake
  conformance server until live S3/R2 acceptance is separately specified.
- Add Git backend.
- Keep local folder backend as the reference behavior.
- Add optional end-to-end encryption after backend contracts are stable.

Exit criteria:

- Backend conformance tests pass against local, OSS fake conformance, WebDAV
  fake conformance, generic S3/R2 fake conformance, and Git.
- OSS backend implementation is complete when fake conformance and the
  `remote: oss` fake round trip pass.
- WebDAV backend implementation is complete when fake conformance and the
  `remote: webdav` fake round trip pass.
- S3/R2 backend implementation is complete when fake conformance and the
  `remote: r2` fake round trip pass.
- Live Alibaba Cloud acceptance remains a separate specified/manual gate until
  it is intentionally run against a real isolated bucket prefix.
- Backend errors are surfaced without corrupting manifests.
- Live cloud acceptance is gated, uses an isolated test prefix, and never
  uploads `.env`, database files, logs, caches, tmp files, locks, watcher
  state, or credentials.

## Phase 6: Core Hook Extensions

Only after snapshot sync is stable, propose generic Hermes core hooks:

- `on_message_persisted`
- `on_session_updated`
- `on_artifact_created`

Exit criteria:

- Core changes are generic plugin capabilities.
- No sync policy or backend logic enters Hermes core.
