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
python -m json.tool feature_list.json
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
3. Seed fixtures into `device-a`.
4. Enable the sync plugin in the temporary profile config.
5. Run `/sync status` or `sync_status` and assert no excluded paths are listed.
6. Run `/sync now` or `sync_now` to execute the first once-style sync.
7. Run the plugin API against `device-b` to pull from the same remote.
8. Compare exported objects, manifests, and trace logs.
9. Re-run `push`, `pull`, and `once` to prove idempotency.
10. Inject concurrent edits and assert conflict output.

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
| Config export | Non-secret config appears in outbox. |
| Secret exclusion | `.env` and token-like keys are skipped. |
| DB exclusion | `state.db` and WAL/SHM files are skipped. |
| Artifact push/pull | Text artifact arrives on second device. |
| Session snapshot | Session JSON is exported, not database files. |
| Idempotent once | Second run makes no changes. |
| Tombstone | Delete creates a tombstone and imports as delete. |
| Text conflict | Conflict file is created when merge fails. |
| Binary conflict | Latest file wins and conflict copy is preserved. |

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
    {"name": "scan", "status": "completed", "objects": 12},
    {"name": "push", "status": "completed", "objects": 3},
    {"name": "pull", "status": "completed", "objects": 1},
    {"name": "import", "status": "completed", "objects": 1}
  ],
  "conflicts": 0
}
```

Trace records are for debugging and evals. They must not include object content
or secrets.

## Continuous Sync Harness

Continuous sync should be tested after `once` is reliable:

1. Start the plugin continuous worker through the harness adapter against a
   temporary profile.
2. Add or change one allowed artifact.
3. Wait for one scan interval.
4. Assert the object appears in outbox and then remote.
5. Stop the process cleanly.
6. Assert no lock files or watcher state were uploaded.

## Manual Acceptance Checklist

- `feature_list.json` is valid JSON.
- `/sync status` and `sync_status` are read-only.
- `/sync now` and `sync_now` are idempotent.
- Excluded files never appear in manifest or remote objects.
- A real-looking `.env` fixture never appears in traces.
- A fake `state.db` fixture is never uploaded.
- Conflict files use `name.sync-conflict-YYYYMMDD-HHMMSS.ext`.
