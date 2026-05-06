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
| Manual | Behavior is specified with a gated manual runner or checklist and intentionally requires user-provided external resources outside the default harness. |
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
| Slash command router | 1 | `/sync status`, `/sync now`, `/sync pause`, `/sync resume`, `/sync conflicts` | Complete | `/sync` routes status, now, pause/resume, and conflict listing to shared behavior. |
| Local folder backend | 2 | `RemoteBackend` | Complete | Temporary local remote receives only allowed objects. |
| Outbox and inbox | 2 | `sync_engine` | Complete | Push writes outbox objects; pull stages remote objects before import. |
| `push` | 2 | `/sync now`, future `hermes sync push` | Complete | Repeated push is idempotent and updates manifest revisions correctly. |
| `pull` | 2 | `/sync now`, future `hermes sync pull` | Complete | Repeated pull is idempotent and imports through inbox. |
| `once` | 2 | `/sync now`, `sync_now` tool, future `hermes sync once` | Complete | Second run makes no changes and leaves traces clean of secrets. |
| Config sync | 2 | `config` scope | Complete | Allowed config syncs; key-level secret-like config is skipped. |
| Artifact sync | 2 | `artifacts` scope | Complete | Text artifacts move between two temporary profiles; runtime files stay local. |
| Memory, skills, and plugin manifests | 2 | `memory`, `skills`, `plugins` scopes | Complete | Memory files, skill files, and plugin manifests round-trip when enabled; locks, skill hub state, plugin code, and runtime files stay local. |
| Session snapshots | 2 | opt-in `sessions` scope, future `on_session_end` | Complete | Session JSON is exported through read-only SQLite only when enabled; `state.db`, WAL, and SHM files are never synced. |
| Tombstones | 4 | Manifest and remote metadata | Complete | Deletes create tombstones and import as deletes without silent loss. |
| Conflict listing | 4 | `/sync conflicts`, `sync_list_conflicts` tool, future `hermes sync conflicts` | Complete | Concurrent overlapping edits produce deterministic conflict records. |
| Conflict files and merge | 4 | `sync/conflicts/`, structured merge, text merge | Complete | JSON/YAML and non-overlapping text edits merge; overlapping text and binary edits create `name.sync-conflict-YYYYMMDD-HHMMSS.ext` copies under plugin-owned state. |
| Version restore | 4 | `sync_restore_version` tool, future `hermes sync restore` | Complete | Previous versions can be restored without corrupting manifest state. |
| Continuous sync | 3 | continuous worker, future `hermes sync --continuous` | Complete | File changes sync after one interval; runtime lock files are not uploaded. |
| Continuous auto-trigger | 3 | worker wake, debounce, local sync lock, allowlisted mtime polling | Complete | Hook wakeups and mtime polling trigger sync promptly without profile-wide mirroring. |
| Pause and resume | 3 | `/sync pause`, future CLI pause/resume | Complete | Pause state remains local to the device and is never uploaded. |
| Backend conformance | 5 | RemoteBackend conformance, local-folder, OSS fake conformance, WebDAV fake conformance, S3/R2 fake conformance, future Git backend | In progress | Local-folder, OSS fake, WebDAV fake, and S3/R2 fake backends pass reusable object, tombstone, idempotency, path-safety, and backend protocol-subset checks; future backends must pass the same suite. |
| Alibaba Cloud OSS backend implementation | 5 | `remote: oss`, `OssBackend`, S3-compatible OSS subset | Complete | OSS backend implementation complete against fake conformance: fake OSS conformance and `remote: oss` config round trip pass without real credentials. |
| WebDAV backend implementation | 5 | `remote: webdav`, `WebDavBackend`, WebDAV subset | Complete | WebDAV backend implementation complete against fake conformance: fake WebDAV conformance and `remote: webdav` config round trip pass without real credentials. |
| S3/R2 backend implementation | 5 | `remote: s3`, `remote: r2`, `S3CompatibleBackend`, S3-compatible subset | Complete | S3/R2 backend implementation complete against fake conformance: fake S3 conformance and `remote: r2` config round trip pass without real credentials. |
| Live Alibaba Cloud OSS acceptance | 5 | `harness.oss_live_acceptance`, real Alibaba Cloud bucket, isolated prefix | Manual | Live Alibaba Cloud acceptance specified/manual: gated runner exists outside the default harness and requires intentional bucket, prefix, and local environment credentials. |
| Core hook proposal | 6 | Hermes core plugin hooks | Blocked | Only generic hooks are proposed; sync policy remains in this plugin. |

## Harness Features

| Feature | Status | Required Behavior |
| --- | --- | --- |
| Temporary profile factory | Complete | Creates isolated profile roots and refuses real `~/.hermes` by default. |
| Temporary remote factory | Complete | Creates isolated local-folder remotes and temporary fake OSS/S3/WebDAV HTTP remotes under the system temp directory. |
| Fixture seeding | Complete through Phase 4 merge plus Phase 3 auto-trigger | Seeds config, artifacts, excluded files, sanitized session fixtures, conflicts, structured/text merges, tombstones, restore fixtures, hook wakeups, mtime edits, and paused pending work without secrets. |
| Path guard | Complete | Rejects traversal, absolute escapes, and symlink escapes. |
| Ignore-rule assertions | Complete | Proves blocked patterns never enter manifest, traces, or remote objects. |
| Command adapter | In progress | Runs plugin APIs with structured results; top-level CLI waits on Hermes core. |
| Manifest inspector | Complete | Reads `manifest.sqlite` and reports object state without mutation. |
| Remote inspector | Complete | Lists remote objects and tombstones without importing them. |
| Trace capture | In progress | Records phases, counts, and errors without object content or secrets. |
| Scenario runner | Complete through Phase 5 S3/R2 fake conformance | Runs required scenarios from `docs/harness.md` repeatably. |
| Idempotency checks | Complete | Re-runs `push`, `pull`, and `once` and asserts no extra changes. |
| Tombstone verifier | Complete | Confirms delete propagation uses explicit tombstones. |
| Conflict injector | Complete | Creates concurrent edits and verifies merge results, conflict records, and conflict files. |
| Continuous sync supervisor | Complete | Runs a bounded continuous sync loop and verifies pause state stays local. |
| Auto-trigger supervisor | Complete | Verifies debounce, single-flight locking, allowlisted mtime polling, and pause/resume pending drain. |
| Backend conformance runner | Complete for local-folder, fake OSS, fake WebDAV, and fake S3/R2 | Reuses the same suite for local, OSS, Git, WebDAV, and generic S3/R2 backends; fake OSS, fake WebDAV, and fake S3 also validate their protocol subsets used by the harness. |

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
