# AGENTS.md

## Project Role

This repository is the `hermes-sync` plugin project for Hermes Agent.
Keep sync behavior outside Hermes core unless a generic plugin hook is needed.

The plugin must stay app-aware:

- Do not sync the whole Hermes profile directory.
- Do not sync `state.db`, `state.db-wal`, or `state.db-shm` as files.
- Do not sync `.env`, API keys, tokens, or provider credentials by default.
- Do not include logs, cache, tmp, lock files, or runtime-only state in sync.
- Prefer explicit sync scopes over directory-level mirroring.

## Architecture Boundaries

Hermes core owns:

- sessions and message persistence
- tools and tool records
- configuration loading
- memory providers
- gateway/provider integration
- plugin discovery and hook execution

`hermes-sync` owns:

- sync policy and scope selection
- local manifest and device identity
- remote backend abstraction
- outbox/inbox processing
- session snapshots and later session events
- conflict handling and version history
- continuous sync scheduling

If session-level deltas need stronger guarantees, propose generic core hooks such as
`on_message_persisted`, `on_session_updated`, or `on_artifact_created`. Do not move
sync logic into core.

## Development Rules

- Use Python standard library APIs such as `sqlite3`, `json`, `pathlib`, and
  `hashlib` before introducing dependencies.
- Keep remote backends behind a small `RemoteBackend` protocol.
- Treat local folder sync as the first backend and the reference behavior.
- Make destructive operations explicit with tombstones, never silent deletion.
- Keep secrets out of fixtures, snapshots, manifests, logs, and docs.
- When updating docs, keep command examples aligned with `plugin.yaml`.

## Documentation Rules

- Put user-facing docs in `README.md` and `docs/`.
- Put implementation contracts in `docs/architecture.md` and
  `docs/harness.md`.
- Update `docs/implementation-plan.md` whenever milestone scope changes.
- Include safety notes whenever sync scope, conflict handling, or remote storage
  behavior changes.

## Harness Rules

The sync harness must use isolated test profiles and remotes. Never point a
harness run at a real `~/.hermes` profile unless the user explicitly asks.

The harness must validate:

- path allowlisting and traversal rejection
- ignore rules
- manifest updates
- outbox/inbox processing
- tombstone behavior
- conflict creation
- idempotent `push`, `pull`, and `once`

For any OpenAI API, Codex, Agents SDK, tool, MCP, or official-docs question,
use the OpenAI developer documentation MCP server or official OpenAI docs as the
source of truth.

