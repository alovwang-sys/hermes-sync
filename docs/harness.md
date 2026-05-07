# Harness Process

The harness is the repeatable process used to test `hermes-sync` without
touching a real Hermes profile or real remote account.

It borrows the same discipline used by official OpenAI tool harness guidance:
parse the requested operation, validate the target, apply the operation in a
controlled workspace, record success or failure, and make atomicity explicit.

## Goals

- Exercise sync behavior with isolated profiles.
- Prove ignore rules before any remote backend is trusted.
- Make conflict and tombstone behavior reproducible.
- Capture enough trace data to debug sync decisions.
- Keep secrets and production profiles out of all tests.

## Tracking

Harness scope and progress are tracked in two companion documents:

- `feature_list.json` is the machine-readable feature inventory, progress
  source, and required execution contract.
- `docs/feature-list.md` lists harness capabilities required by each product
  feature.
- `docs/progress.md` records current scenario status and the next executable
  work.

Update those files whenever a required scenario is added, removed, renamed, or
completed.

Before any executable harness run, validate the feature contract:

```bash
python3 -m json.tool feature_list.json
```

## Layout

```text
harness/
  README.md
  profiles/
    device-a/
    device-b/
  remotes/
    local-folder/
  fixtures/
    config/
    sessions/
    artifacts/
  traces/
```

The directory above is the intended future test layout. It should be generated
by test setup scripts rather than checked in with real profile data.

## Execution Phases

1. Create two temporary Hermes profiles.
2. Create a temporary local-folder remote.
3. Create temporary fake OSS, fake S3, and fake WebDAV HTTP remotes for backend checks.
4. Seed fixtures into `device-a`.
5. Enable the sync plugin in the temporary profile config.
6. Run `/sync status` or `sync_status` and assert no excluded paths are listed.
7. Run `/sync now` or `sync_now` to execute the first once-style sync.
8. Run the plugin API against `device-b` to pull from the same remote.
9. Compare exported objects, manifests, and trace logs.
10. Re-run `push`, `pull`, and `once` to prove idempotency.
11. Inject concurrent edits and assert conflict output.

## Harness Adapter Contract

A harness adapter wraps Hermes CLI calls and direct plugin APIs:

```python
class SyncHarness:
    def make_profile(self, name: str): ...
    def make_remote(self, name: str): ...
    def seed_fixture(self, profile: str, fixture: str): ...
    def run_sync(self, profile: str, *args: str): ...
    def read_manifest(self, profile: str): ...
    def list_remote_objects(self, remote: str): ...
    def read_trace(self, profile: str): ...
```

The adapter must return structured results:

```json
{
  "status": "completed",
  "command": "sync_now",
  "profile": "device-a",
  "stdout": "",
  "stderr": "",
  "trace_id": "sync-20260505-143022"
}
```

Failures must use `status: "failed"` and include a short actionable error.

## Path Safety

Before any harness run applies a file operation, it must validate:

- path is inside the temporary profile or temporary remote root
- path does not contain traversal such as `../`
- path is not a symlink escape
- path does not match blocked patterns such as `.env`, `*.db`, `*.db-wal`,
  `*.db-shm`, `logs/**`, `cache/**`, `tmp/**`, or `locks/**`

## Atomicity

Default rule: per-object operations may succeed or fail independently, but a
single object import must be atomic.

For example:

- importing one JSON config object either fully updates that object or does
  nothing
- a failed artifact import must leave the previous artifact version readable
- manifest updates must not mark an object clean before the object operation
  succeeds

## Required Scenarios

| Scenario | Expected Result |
| --- | --- |
| Empty profiles | `status` reports no dirty objects. |
| Local remote object round trip | Local-folder backend uploads, lists, downloads, and tombstones an allowed object. |
| Outbox processing | `push` stages outbox objects after scan and manifest updates, then uploads them. |
| Inbox staging before import | `pull` stages remote objects into inbox before applying user-visible imports. |
| Config export | Non-secret config appears in outbox and remote storage. |
| Secret exclusion | `.env`, credential files, and token-like keys are skipped. |
| Configured scope disable sessions | `sync.scopes.sessions: false` prevents status, push, and pull from processing session snapshots while other enabled scopes still sync. |
| DB exclusion | `state.db` and WAL/SHM files are skipped. |
| Artifact push/pull | Text artifact arrives on second device. |
| Memory/skills/plugins push/pull | Memory files, skill files, and plugin manifests arrive on the second device while locks, skill hub state, plugin executable files, and runtime data stay local. |
| Runtime file exclusion | Logs, caches, tmp files, lock files, and watcher state remain local. |
| Session snapshot | Session JSON is exported and stored as plugin-owned history, not database files. |
| Backend conformance | The local-folder reference backend passes reusable object, tombstone, idempotency, and path-safety checks. |
| OSS backend conformance | The OSS backend passes the same conformance suite against a temporary fake OSS service that validates the harness OSS protocol subset. |
| OSS sync config round trip | A `remote: oss` profile pushes and pulls allowed objects through fake OSS without credentials or runtime state. |
| WebDAV backend conformance | The WebDAV backend passes the same conformance suite against a temporary fake WebDAV service that validates the harness WebDAV protocol subset. |
| WebDAV sync config round trip | A `remote: webdav` profile pushes and pulls allowed objects through fake WebDAV without credentials or runtime state. |
| S3 backend conformance | The S3-compatible backend passes the same conformance suite against a temporary fake S3 service that validates the harness S3 protocol subset. |
| R2 sync config round trip | A `remote: r2` profile pushes and pulls allowed objects through fake S3-compatible storage without credentials or runtime state. |
| Idempotent push | Second `push` creates no extra remote changes. |
| Idempotent pull | Second `pull` creates no extra local changes. |
| Idempotent once | Second run makes no changes. |
| Tombstone | Delete creates a tombstone and imports as delete. |
| Text conflict | Conflict file is created when merge fails. |
| Binary conflict | Latest file wins and conflict copy is preserved. |
| JSON structured merge | Non-overlapping JSON object edits merge and push as a new head. |
| YAML config merge | Non-overlapping YAML config edits merge without syncing runtime state. |
| Text three-way merge | Non-overlapping text line edits merge and push as a new head. |
| Restore previous version | A previous artifact version restores from local sync history and can be pushed as the new head. |
| Continuous sync | A bounded continuous worker syncs an allowed change after one interval. |
| Pause state local only | Pause state prevents worker uploads and never enters the remote. |
| Hook wake debounce | `on_session_end` and artifact-producing tool hooks wake the worker, coalesce rapid events, and run one sync cycle. |
| Mtime polling reconcile | Allowlisted config/artifact polling catches external edits without uploading logs, cache, tmp files, locks, or watcher state. |
| Sync lock single-flight | Concurrent wake events do not run overlapping sync cycles and do not upload local lock files. |
| Pause/resume drains pending | Wake events received while paused stay local; resume triggers one catch-up cycle. |

## Traces

Every sync run should produce a structured trace:

```json
{
  "trace_id": "sync-20260505-143022",
  "profile": "default",
  "device_id": "amos-laptop",
  "command": "once",
  "remote": "local",
  "phases": [
    {"name": "scan", "status": "completed", "objects": 12, "duration_ms": 8},
    {"name": "stage_outbox", "status": "completed", "objects": 3, "bytes": 9240},
    {"name": "upload", "status": "completed", "objects": 3, "bytes": 9240, "duration_ms": 120},
    {"name": "import", "status": "completed", "objects": 1, "duration_ms": 4}
  ],
  "metrics": {
    "candidate_objects": 12,
    "dirty_objects": 3,
    "unchanged_objects": 9,
    "hash_reused_objects": 8,
    "uploaded_bytes": 9240
  },
  "conflicts": 0
}
```

Trace records are for debugging and evals. They must not include object content
or secrets. Incremental metrics count objects and bytes only; they are intended
to show whether a run uploaded changed objects or merely scanned clean ones.

## Continuous Sync Harness

Continuous sync should be tested after `once` is reliable:

1. Start the plugin continuous worker through the harness adapter against a
   temporary profile.
2. Add or change one allowed artifact.
3. Wait for one scan interval.
4. Assert the object appears in outbox and then remote.
5. Stop the process cleanly or use a bounded cycle count.
6. Assert no lock files or watcher state were uploaded.

## Auto-Trigger Harness

The continuous auto-trigger harness stays app-aware and standard-library
first. It does not watch or mirror the whole profile directory.

Executable scenarios:

| Scenario | Expected Result |
| --- | --- |
| Hook wake debounce | `on_session_end` and artifact-producing tool hooks wake the worker, coalesce rapid events, and run one sync cycle. |
| Mtime polling reconcile | Allowlisted config/artifact polling catches external edits without uploading logs, cache, tmp files, locks, or watcher state. |
| Sync lock single-flight | Concurrent wake events do not run overlapping sync cycles and do not upload local lock files. |
| Pause/resume drains pending | Wake events received while paused stay local; resume triggers one catch-up cycle. |

These scenarios are part of the executable runner and must continue to prove
that `sync/watcher-state.json`, local lock files, logs, caches, tmp files, and
other runtime-only state never enter manifests or remotes.

## Backend Conformance Harness

Backend conformance is the Phase 5 gate for every `RemoteBackend`
implementation. The runner currently executes the shared suite against the
local-folder reference backend, the OSS backend through a temporary fake OSS
HTTP service, the generic S3/R2 backend through a temporary fake S3 HTTP
service, and the WebDAV backend through a temporary fake WebDAV HTTP service.

The conformance suite verifies:

- empty remotes list no active objects or tombstones
- upload, list, and download preserve metadata and content
- repeated upload and repeated tombstone writes are idempotent
- replacing an object updates one active head without duplicating metadata
- tombstones hide active objects and make downloads fail
- re-uploading an object clears the tombstone and restores the active head
- unsafe scope or object identifiers are rejected without path escape writes

## Fake OSS Protocol Contract

The fake OSS service is a local conformance server, not an Alibaba Cloud
emulator. It validates the standard-library S3-compatible subset that
`OssBackend` uses in the default harness:

- path-style harness requests only: `PUT`, `GET`, and `DELETE` object paths
  under `/<bucket>/<key>`
- ListObjectsV2 requests only at `/<bucket>?list-type=2&prefix=...`
- `x-oss-s3-compat: true` on every request
- `x-amz-content-sha256` matching the exact request payload
- unsigned harness traffic only; `Authorization` and STS token headers are
  rejected by the fake service
- XML ListObjectsV2 responses with prefix filtering and S3-style 404 behavior
  for missing objects

The executable scenarios assert that OSS conformance covers list, put, get,
and delete operations. Passing fake conformance means the OSS backend
implementation is complete against this local contract. It does not claim that
a live Alibaba Cloud account has been exercised.

The OSS harness also runs an end-to-end `remote: oss` config round trip through
the sync engine. It uses `unsigned` and `path_style` only for the local fake
service; live Alibaba Cloud OSS must use local environment credentials and
virtual-hosted style configuration.

## Fake WebDAV Protocol Contract

The fake WebDAV service is a local conformance server, not a full WebDAV
implementation. It validates the standard-library subset that `WebDavBackend`
uses in the default harness:

- `MKCOL` creates missing parent collections before object writes
- `PROPFIND` with `Depth: infinity` lists keys under the configured prefix
- `PUT` writes object metadata and content only when parent collections exist
- `GET` downloads active metadata and content, with 404 for missing objects
- `DELETE` removes tombstone keys or active content explicitly
- unauthenticated harness traffic only; live credentials, when needed, stay in
  local environment variables and are not part of fake conformance

Passing fake WebDAV conformance means the WebDAV backend implementation is
complete against this local contract. It does not claim that a live WebDAV
server has been exercised.

## Fake S3/R2 Protocol Contract

The fake S3 service is a local conformance server for generic S3-compatible
storage, including the `remote: r2` alias. It validates the standard-library
subset that `S3CompatibleBackend` uses in the default harness:

- path-style harness requests only: `PUT`, `GET`, and `DELETE` object paths
  under `/<bucket>/<key>`
- ListObjectsV2 requests only at `/<bucket>?list-type=2&prefix=...`
- `x-amz-content-sha256` matching the exact request payload
- unsigned harness traffic only; `Authorization` and session token headers are
  rejected by the fake service
- OSS-specific `x-oss-s3-compat` headers are rejected so generic S3/R2 behavior
  remains distinct from the Alibaba Cloud OSS backend
- XML ListObjectsV2 responses with prefix filtering and S3-style 404 behavior
  for missing objects

Passing fake S3/R2 conformance means the S3-compatible backend implementation
is complete against this local contract. It does not claim that a live AWS S3,
R2, or other S3-compatible account has been exercised.

Live OSS acceptance is a separate gated command and is not part of the default
runner:

```bash
export HERMES_SYNC_OSS_BUCKET=...
export HERMES_SYNC_OSS_ENDPOINT=https://s3.oss-cn-hangzhou.aliyuncs.com
export HERMES_SYNC_OSS_REGION=cn-hangzhou
python3 -m harness.oss_live_acceptance
```

The command also requires `ALIBABA_CLOUD_ACCESS_KEY_ID` and
`ALIBABA_CLOUD_ACCESS_KEY_SECRET` in the local environment. It creates
temporary profiles, uses a prefix under `hermes-sync-live-acceptance/`, and
deletes that prefix after the run.

Git must pass this same suite before it is eligible for end-to-end sync
scenarios.

## Manual Acceptance Checklist

- `feature_list.json` is valid JSON.
- `/sync status` and `sync_status` are read-only.
- `/sync now` and `sync_now` are idempotent.
- Excluded files never appear in manifest or remote objects.
- A real-looking `.env` fixture never appears in traces.
- A fake `state.db` fixture is never uploaded.
- Conflict files use `name.sync-conflict-YYYYMMDD-HHMMSS.ext`.
- Live OSS acceptance, when run manually with `python3 -m
  harness.oss_live_acceptance`, uses an isolated test prefix and local
  environment credentials; default harness runs never require real OSS
  credentials or network storage.
