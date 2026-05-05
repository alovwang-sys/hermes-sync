# Architecture

`hermes-sync` is a plugin, not a Hermes core feature. The plugin follows a
local-first design: Hermes remains fully usable offline, and the remote is only
a coordination point for selected objects.

## Responsibilities

Hermes core:

- owns profile layout and runtime state
- persists sessions and messages
- runs tools and LLM calls
- loads configuration and memory providers
- discovers plugins and dispatches hooks

`hermes-sync` plugin:

- reads configured sync scopes
- exports app-aware objects
- records local and remote revisions in a manifest
- pushes local changes through a remote backend
- pulls remote changes into an inbox
- detects conflicts by data type
- stores tombstones and version history
- runs continuous sync when enabled

## Data Model

The plugin tracks objects, not arbitrary files.

Core fields in `manifest.sqlite`:

| Field | Meaning |
| --- | --- |
| `scope` | Logical area such as `config`, `sessions`, or `artifacts`. |
| `object_id` | Stable object identity within a scope. |
| `logical_path` | Human-readable path or object label. |
| `content_hash` | Hash of exported content. |
| `local_rev` | Local revision known by this device. |
| `remote_rev` | Remote revision known by this device. |
| `base_rev` | Common ancestor revision for conflict detection. |
| `mtime` | Source modification time or export time. |
| `deleted` | Tombstone flag. |
| `dirty` | Local object needs push. |
| `conflict_state` | Empty, pending, resolved, or ignored. |

Local plugin state lives under the active Hermes profile:

```text
~/.hermes/sync/
  device.json
  manifest.sqlite
  outbox/
  inbox/
  conflicts/
```

`device.json`:

```json
{
  "device_id": "amos-laptop",
  "profile": "default",
  "last_remote_cursor": null
}
```

## Remote Backend Contract

Remote backends must implement a narrow object/event interface:

```python
class RemoteBackend:
    def list_objects(self): ...
    def upload_object(self, metadata, content): ...
    def download_object(self, scope, object_id): ...
    def put_tombstone(self, metadata): ...
    def list_tombstones(self): ...
```

The first backend should be a local folder backend. Git, WebDAV, and S3/R2 come
after the object model and conflict rules are stable. Event cursor methods can
be added after session snapshots and tombstone propagation are reliable.

## Session Sync

Version 1 uses snapshots:

1. `on_session_end` exports one session from `state.db`.
2. The plugin writes `outbox/session-<id>.json`.
3. `push` uploads the snapshot.
4. Other devices `pull` the snapshot and import it or expose it as read-only
   history.

The plugin must not synchronize `state.db` directly. SQLite WAL files are
runtime coordination files, not portable sync objects.

Version 2 can add event streaming:

- `session_created`
- `message_appended`
- `tool_call_recorded`
- `session_title_updated`
- `session_ended`

Event streaming requires stronger Hermes core hooks and should be introduced
only after snapshot sync is reliable.

## Conflict Model

Conflict handling is scope-specific:

| Type | Strategy |
| --- | --- |
| Text or Markdown artifacts | Three-way merge; failed merge creates a conflict file. |
| JSON or YAML config | Merge by key; conflicting fields keep local and remote variants. |
| Sessions | Do not merge SQLite files; keep multiple branches or merge events later. |
| Binary artifacts | Last writer wins, with a conflict copy preserved. |
| Skills or plugins | Do not auto-overwrite version conflicts; ask the user. |
| Secrets | Out of scope by default. |

Conflict files use this format:

```text
name.sync-conflict-YYYYMMDD-HHMMSS.json
```

## Security Defaults

- `secrets` scope is disabled.
- `.env` and database files are excluded even if a broad scope is enabled.
- Remote backends must not log object content by default.
- Future encrypted sync must be end-to-end; the remote must not hold plaintext
  secret material.
