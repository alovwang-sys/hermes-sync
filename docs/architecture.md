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
  versions/
  watcher-state.json
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

The local-folder backend remains the reference backend. The Alibaba Cloud OSS
backend uses the same `RemoteBackend` protocol through the OSS S3-compatible
API. The WebDAV backend uses the same `RemoteBackend` protocol through a small
MKCOL/PROPFIND/PUT/GET/DELETE subset. The generic S3/R2 backend uses the same
S3-compatible API shape without OSS-specific compatibility headers. Git comes
after the object model and conflict rules are stable. Event cursor methods can
be added after session snapshots and tombstone propagation are reliable.

Every backend must pass the shared conformance harness before it is used in
end-to-end sync scenarios. The conformance suite covers active object
upload/list/download, replacement upload, explicit tombstones, idempotent
retries, re-upload after tombstone, and path-safety rejection for unsafe remote
identifiers. The local-folder backend is the reference implementation.

Remote object storage layout is backend-independent:

```text
<prefix>/objects/<scope>/<object_id>/metadata.json
<prefix>/objects/<scope>/<object_id>/content
<prefix>/tombstones/<scope>/<object_id>.json
```

For OSS, `bucket`, `endpoint`, `region`, and `prefix` are non-secret routing
configuration. Access keys and STS tokens are local environment variables only
and must not be stored in synced profile content, fixtures, traces, or docs.
The executable harness covers OSS through an in-memory fake OSS service that
validates the unsigned S3-compatible request subset used by `OssBackend`:
path-style object `PUT`/`GET`/`DELETE`, ListObjectsV2 prefix listing,
`x-oss-s3-compat`, and payload SHA-256 headers. This makes the OSS backend
implementation complete against fake conformance. Live Alibaba Cloud
acceptance is specified as a separate manual gate with a real bucket, local
environment credentials, and an isolated `hermes-sync-live-acceptance/` prefix.

For WebDAV, `url` or `endpoint` and `prefix` are non-secret routing
configuration. Username and password are read only from
`HERMES_SYNC_WEBDAV_USERNAME` and `HERMES_SYNC_WEBDAV_PASSWORD` when needed.
The executable harness covers WebDAV through an in-memory fake WebDAV service
that validates collection creation, recursive listing, object upload/download,
and explicit delete behavior without touching a real WebDAV account.

For generic S3/R2, `bucket`, `endpoint`, `region`, and `prefix` are non-secret
routing configuration. Access keys and session tokens are read only from
`AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, and `AWS_SESSION_TOKEN`. The
executable harness covers generic S3/R2 through an in-memory fake S3-compatible
service that validates path-style object operations, ListObjectsV2 prefix
listing, payload SHA-256 headers, and absence of OSS-only compatibility
headers.

## Session Sync

Version 1 uses snapshots:

1. `push` exports sessions from `state.db` through a read-only SQLite
   connection.
2. The plugin stages deterministic JSON under `sync/outbox`.
3. `push` uploads each snapshot as a `sessions` object.
4. Other devices `pull` the snapshot into `sync/inbox` and store it under
   plugin-owned `sync/sessions/` read-only history.
5. A later `on_session_end` hook can enqueue only the ended session once the
   continuous sync worker exists.

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
| Text or Markdown artifacts | Non-overlapping line edits use a three-way merge from the last common synced version; overlapping edits fall back to remote-wins import with a plugin-owned local conflict copy. |
| JSON or YAML config/artifacts | Non-overlapping object-key edits merge recursively from the last common synced version; overlapping scalar/list edits fall back to conflict preservation. |
| Sessions | Do not merge SQLite files; keep multiple branches or merge events later. |
| Binary artifacts | Last writer wins, with a conflict copy preserved. |
| Skills or plugins | Do not auto-overwrite version conflicts; ask the user. |
| Secrets | Out of scope by default. |

Conflict files are stored under plugin-owned `sync/conflicts/` and use this
format:

```text
name.sync-conflict-YYYYMMDD-HHMMSS.json
```

Successful merge results are written locally, recorded as dirty against the
new remote base revision, and pushed as a new head on the next `push` or
`once`. The merge path keeps the previous local and remote versions under
`sync/versions/` and does not upload conflict copies or runtime metadata.

Deletes are propagated as explicit tombstone metadata in the manifest and
remote backend. A tombstone hides the active remote object, and a later upload
of a recreated object clears the remote tombstone instead of silently
resurrecting deleted content.

Version contents are stored locally under plugin-owned `sync/versions/`.
`sync_restore_version` restores supported config/artifact content from that
local history, marks the restored object dirty when it differs from the remote
head, and leaves session snapshots as read-only history.

Continuous sync state, including pause state, pending session hints, pending
artifact wakeups, debounce state, and mtime polling signatures, is stored only
in `sync/watcher-state.json`. That path is under the blocked plugin-owned
`sync/` tree and must never be uploaded.

The continuous worker uses a hybrid trigger model: Hermes hooks wake the worker
when sessions end or tools create allowed artifacts, a short debounce coalesces
bursts, and allowlisted mtime polling catches config or artifact edits made
outside Hermes. A local single-flight lock under `sync/sync.lock` prevents
overlapping sync cycles. All lock, pending, debounce, and polling state stays
under plugin-owned `sync/` metadata.

## Security Defaults

- `secrets` scope is disabled.
- `sessions`, `memory`, and `skills` are opt-in for real profiles; the local
  smoke config starts with only `config` and `artifacts` enabled.
- `plugins` syncs manifests only; plugin executable code and runtime caches are
  intentionally local-only.
- `.env` and database files are excluded even if a broad scope is enabled.
- Remote backends must not log object content by default.
- Future encrypted sync must be end-to-end; the remote must not hold plaintext
  secret material.
