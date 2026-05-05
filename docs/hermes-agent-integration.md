# Hermes Agent Integration

This document records the compatibility check against the local Hermes core
checkout at `/home/amos/project/hermes-agent`.

Reviewed checkout: `32ae91091`

## What Works Now

- Directory plugins are supported through `plugin.yaml` plus `__init__.py`
  with `register(ctx)`.
- Standalone user plugins are opt-in through `plugins.enabled`.
- Plugin hooks are usable for sync triggers. Current hooks include
  `post_tool_call`, `on_session_end`, `pre_llm_call`, `post_llm_call`,
  `on_session_start`, and approval hooks.
- Plugin slash commands are usable through `ctx.register_command(...)`.
  CLI and gateway both dispatch plugin-registered slash commands.
- Plugin tools are usable through `ctx.register_tool(...)`, which delegates to
  the global tool registry.
- Session data is profile-aware and stored in SQLite `state.db`; sync can
  export session snapshots by reading SQLite data, but must never sync
  `state.db`, `state.db-wal`, or `state.db-shm` as files.

## Blocked Surface

Top-level `hermes sync ...` is not available from a standalone plugin yet.
`PluginContext.register_cli_command(...)` records CLI command metadata, but
`hermes_cli/main.py` currently wires only memory plugin CLI commands into
argparse before parsing.

This needs a generic Hermes core extension: register general plugin CLI
commands before argparse parses subcommands. That extension belongs in Hermes
core because it is a generic plugin capability, not sync-specific logic.

Until that exists, the first executable surfaces should be:

- `/sync status`
- `/sync now`
- `/sync pause`
- `/sync conflicts`
- `sync_status`
- `sync_now`
- `sync_list_conflicts`
- `sync_restore_version`

## Harness Implications

The harness must execute against temporary profiles and temporary remotes. It
must validate:

- plugin disabled and enabled states
- plugin slash command registration
- plugin tool registration
- path allowlisting before sync actions
- SQLite session snapshot export without SQLite file sync
- idempotent `push`, `pull`, and `once`
- tombstone and conflict behavior

The machine-readable feature and harness contract is `feature_list.json`.
