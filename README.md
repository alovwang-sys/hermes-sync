# hermes-sync

`hermes-sync` is an Obsidian-style, local-first sync plugin for Hermes Agent
profiles. It keeps sync policy outside Hermes core and synchronizes only
explicitly allowed data scopes.

## Goal

Each device keeps its own local Hermes profile. The plugin exports safe,
app-aware objects into a sync layer, exchanges those objects through a selected
remote backend, and imports them back into other devices without treating the
entire profile directory as a cloud-drive folder.

## Non-Goals

- Do not fork Hermes core.
- Do not sync the whole `~/.hermes` directory.
- Do not file-sync SQLite state databases.
- Do not sync `.env`, API keys, tokens, or secrets by default.
- Do not sync logs, cache, tmp, lock files, or runtime state.
- Do not start with a complex hosted cloud service.

## Plugin Shape

```text
hermes-sync/
  AGENTS.md
  AGENT.md
  README.md
  feature_list.json
  plugin.yaml
  docs/
    architecture.md
    sync-scopes.md
    harness.md
    hermes-agent-integration.md
    feature-list.md
    progress.md
    implementation-plan.md
```

Future implementation modules should follow this shape:

```text
hermes_sync/
  __init__.py
  cli.py
  sync_engine.py
  manifest.py
  scopes.py
  conflicts.py
  crypto.py
  remotes/
    local.py
    git.py
    webdav.py
    s3.py
```

## User Commands

Planned CLI, after Hermes core exposes a generic plugin CLI-command bridge:

```bash
hermes sync setup
hermes sync status
hermes sync push
hermes sync pull
hermes sync once
hermes sync --continuous
hermes sync conflicts
hermes sync restore
```

Planned slash commands:

```text
/sync status
/sync now
/sync pause
/sync conflicts
```

Planned tools:

- `sync_status`
- `sync_now`
- `sync_list_conflicts`
- `sync_restore_version`

## Current Implementation

The implemented surfaces are:

- register `/sync status`
- register `sync_status`
- create `device.json`
- initialize `manifest.sqlite`
- scan configured scopes without uploading or importing anything
- implement a local-folder `RemoteBackend`
- stage outgoing objects under `sync/outbox`
- stage incoming objects under `sync/inbox` before import
- run `push`, `pull`, and `once` for supported config/artifact objects
- keep top-level `hermes sync status` as future work until Hermes core exposes
  a generic plugin CLI-command bridge

## Project Tracking

- `feature_list.json` is the machine-readable feature inventory, progress
  source, and harness execution contract.
- `docs/architecture.md` defines plugin boundaries, object model, backend
  contract, session sync, and conflict model.
- `docs/sync-scopes.md` defines the explicit sync scopes and default
  exclusions.
- `docs/harness.md` defines the executable harness contract and required
  scenarios.
- `docs/hermes-agent-integration.md` records compatibility findings from the
  local Hermes Agent checkout.
- `docs/feature-list.md` is the feature inventory and harness coverage matrix.
- `docs/progress.md` tracks current implementation and harness progress.
- `docs/implementation-plan.md` tracks milestone order and exit criteria.

## References

The documentation style and agent-instruction naming follow OpenAI Codex docs
for `AGENTS.md`. The harness safety model follows OpenAI tool-harness guidance:
validate paths, report failures clearly, and choose atomicity rules explicitly.

- OpenAI Codex `AGENTS.md`: https://developers.openai.com/codex/guides/agents-md
- OpenAI Codex plugin docs: https://developers.openai.com/codex/plugins/build
- OpenAI apply-patch harness guidance: https://developers.openai.com/api/docs/guides/tools-apply-patch
- OpenAI shell tool harness guidance: https://developers.openai.com/api/docs/guides/tools-shell
- OpenAI Docs MCP: https://developers.openai.com/learn/docs-mcp
- Obsidian Headless Sync: https://help.obsidian.md/sync/headless
- Obsidian Sync settings: https://help.obsidian.md/sync/settings
- Obsidian Sync troubleshooting: https://help.obsidian.md/sync/troubleshoot
