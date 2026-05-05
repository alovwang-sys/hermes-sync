# Feature List

`feature_list.json` is the machine-readable source of truth for the
`hermes-sync` feature surface, progress status, and harness execution gates.
This document is the human-readable companion. Each feature must map to a
project phase, a command or API surface, and harness coverage before it is
considered complete.

## Status Values

| Status | Meaning |
| --- | --- |
| Not started | No implementation exists yet. |
| Specified | Behavior is documented, but no executable implementation exists yet. |
| In progress | Implementation or harness work has started. |
| Complete | Implementation exists and required harness coverage passes. |
| Blocked | Work needs a Hermes core hook, design decision, or external dependency. |

## Product Features

| Feature | Phase | Surface | Status | Required Harness Coverage |
| --- | --- | --- | --- | --- |
| Plugin metadata | 0 | `plugin.yaml` | Complete | Plugin manifest can be loaded by harness fixtures. |
| Setup command | 1 | `hermes sync setup` | Blocked | Requires generic Hermes core plugin CLI-command bridge before argparse parsing. |
| Device identity | 1 | `device.json` | Complete | `setup` creates per-profile device identity in isolated profiles. |
| Manifest database | 1 | `manifest.sqlite` | Complete | Manifest schema is created and queryable; excluded paths are absent. |
| Scope scanner | 1 | `/sync status`, `sync_status` | Complete | Allowlist, ignore rules, traversal rejection, and symlink escape rejection. |
| Status command | 1 | CLI, slash command, `sync_status` tool | Complete | Empty profile reports no dirty objects; status is read-only. |
| Slash command router | 1 | `/sync status`, `/sync now`, `/sync pause`, `/sync conflicts` | In progress | `/sync status` and `/sync now` route to shared behavior; pause/conflicts remain later work. |
| Local folder backend | 2 | `RemoteBackend` | Complete | Temporary local remote receives only allowed objects. |
| Outbox and inbox | 2 | `sync_engine` | Complete | Push writes outbox objects; pull stages remote objects before import. |
| `push` | 2 | `/sync now`, future `hermes sync push` | Complete | Repeated push is idempotent and updates manifest revisions correctly. |
| `pull` | 2 | `/sync now`, future `hermes sync pull` | Complete | Repeated pull is idempotent and imports through inbox. |
| `once` | 2 | `/sync now`, `sync_now` tool, future `hermes sync once` | Complete | Second run makes no changes and leaves traces clean of secrets. |
| Config sync | 2 | `config` scope | In progress | Allowed config syncs; dedicated key-level secret exclusion scenario remains. |
| Artifact sync | 2 | `artifacts` scope | In progress | Text artifacts move between two temporary profiles; runtime-file-specific scenario remains. |
| Session snapshots | 2 | `sessions` scope, `on_session_end` | Not started | Session JSON is exported; `state.db`, WAL, and SHM files are never synced. |
| Tombstones | 4 | Manifest and remote metadata | Not started | Deletes create tombstones and import as deletes without silent loss. |
| Conflict listing | 4 | `/sync conflicts`, `sync_list_conflicts` tool, future `hermes sync conflicts` | Not started | Concurrent edits produce deterministic conflict records. |
| Conflict files | 4 | `conflicts/` | Not started | Failed text merges create `name.sync-conflict-YYYYMMDD-HHMMSS.ext`. |
| Version restore | 4 | `sync_restore_version` tool, future `hermes sync restore` | Not started | Previous versions can be restored without corrupting manifest state. |
| Continuous sync | 3 | continuous worker, future `hermes sync --continuous` | Not started | File changes sync after one interval; runtime lock files are not uploaded. |
| Pause and resume | 3 | `/sync pause`, future CLI pause/resume | Not started | Pause state remains local to the device and is never uploaded. |
| Backend conformance | 5 | Git, WebDAV, S3/R2 backends | Not started | All backends pass the same object, tombstone, and conflict contract tests. |
| Core hook proposal | 6 | Hermes core plugin hooks | Blocked | Only generic hooks are proposed; sync policy remains in this plugin. |

## Harness Features

| Feature | Status | Required Behavior |
| --- | --- | --- |
| Temporary profile factory | Complete | Creates isolated profile roots and refuses real `~/.hermes` by default. |
| Temporary remote factory | Complete | Creates isolated local-folder remotes under the system temp directory. |
| Fixture seeding | In progress | Seeds config, artifacts, and excluded files without secrets; session fixtures remain. |
| Path guard | Complete | Rejects traversal, absolute escapes, and symlink escapes. |
| Ignore-rule assertions | Complete | Proves blocked patterns never enter manifest, traces, or remote objects. |
| Command adapter | In progress | Runs plugin APIs with structured results; top-level CLI waits on Hermes core. |
| Manifest inspector | Complete | Reads `manifest.sqlite` and reports object state without mutation. |
| Remote inspector | Complete | Lists remote objects and tombstones without importing them. |
| Trace capture | In progress | Records phases, counts, and errors without object content or secrets. |
| Scenario runner | Complete through Phase 2 MVP | Runs required scenarios from `docs/harness.md` repeatably. |
| Idempotency checks | Complete | Re-runs `push`, `pull`, and `once` and asserts no extra changes. |
| Tombstone verifier | Not started | Confirms delete propagation uses explicit tombstones. |
| Conflict injector | Not started | Creates concurrent edits and verifies conflict records and files. |
| Continuous sync supervisor | Not started | Starts, observes, and stops continuous sync cleanly. |
| Backend conformance runner | Not started | Reuses the same suite for local, Git, WebDAV, and S3/R2 backends. |

## Completion Gates

A feature is complete only when:

- its behavior is documented in `docs/architecture.md`, `docs/sync-scopes.md`,
  or `docs/harness.md`
- its command or API surface matches `plugin.yaml`
- its progress row in `docs/progress.md` is updated
- harness coverage exists for the relevant safety rules
- no excluded files, secrets, logs, caches, locks, or runtime state appear in
  manifests, remotes, fixtures, traces, or docs examples

The local folder backend remains the reference behavior. Later backends must
match the same object, manifest, tombstone, conflict, and idempotency semantics.
