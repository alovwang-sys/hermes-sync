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
- `feature_list.json` validates with `python -m json.tool`.
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
- Export session snapshots from `state.db`.
- Import pulled objects into inbox first, then apply.

Exit criteria:

- Two temporary profiles can exchange allowed artifacts.
- Session snapshots move between profiles without syncing SQLite files.
- Running `once` twice is idempotent.

## Phase 3: Continuous Sync

- Implement a plugin continuous sync worker.
- Add watcher or periodic scan loop.
- Use `on_session_end` to enqueue snapshots.
- Use `post_tool_call` to detect important artifacts.
- Keep pause/resume state local to the device.
- Keep `hermes sync --continuous` as future top-level CLI work until the
  generic plugin CLI bridge exists.

Exit criteria:

- A changed artifact is pushed automatically.
- Session end creates an outbox snapshot.
- Runtime state is not uploaded.

## Phase 4: Conflict and History

- Implement tombstones.
- Store version metadata.
- Implement `/sync conflicts`.
- Implement `sync_list_conflicts`.
- Implement `sync_restore_version`.
- Add JSON config merge.
- Add text merge and conflict-file fallback.
- Keep `hermes sync conflicts` and `hermes sync restore` as future top-level
  CLI work until the generic plugin CLI bridge exists.

Exit criteria:

- Concurrent edits produce deterministic conflict records.
- Tombstoned objects do not reappear unexpectedly.
- Restore can recover a previous version.

## Phase 5: Remote Backends

- Add Git backend.
- Add WebDAV backend.
- Add S3/R2 backend.
- Keep local folder backend as the reference behavior.
- Add optional end-to-end encryption after backend contracts are stable.

Exit criteria:

- Backend conformance tests pass against local, Git, WebDAV, and S3/R2.
- Backend errors are surfaced without corrupting manifests.

## Phase 6: Core Hook Extensions

Only after snapshot sync is stable, propose generic Hermes core hooks:

- `on_message_persisted`
- `on_session_updated`
- `on_artifact_created`

Exit criteria:

- Core changes are generic plugin capabilities.
- No sync policy or backend logic enters Hermes core.
