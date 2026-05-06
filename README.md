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
  scheduler.py
  session_snapshots.py
  manifest.py
  scopes.py
  conflicts.py
  crypto.py
  remotes/
    local.py
    oss.py
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
- register `/sync now`, `/sync pause`, `/sync resume`, and `/sync conflicts`
- register `sync_status`
- register `sync_now`, `sync_list_conflicts`, and `sync_restore_version`
- create `device.json`
- initialize `manifest.sqlite`
- scan configured scopes without uploading or importing anything
- implement a local-folder `RemoteBackend`
- stage outgoing objects under `sync/outbox`
- stage incoming objects under `sync/inbox` before import
- run `push`, `pull`, and `once` for supported config, artifact, and session
  snapshot objects
- export session snapshots from `state.db` through read-only SQLite queries and
  store pulled snapshots under plugin-owned `sync/sessions/` history
- propagate deletes through explicit manifest and remote tombstones
- merge non-overlapping JSON/YAML object edits and UTF-8 text line edits, with
  pending conflict records and plugin-owned conflict copies for overlapping
  text and binary artifact edits
- store local version history under `sync/versions` and restore previous
  artifact/config versions through `sync_restore_version`
- run a bounded continuous sync worker and keep pause/watcher state under
  plugin-owned local sync metadata
- wake the continuous worker from session/tool hooks, debounce bursts, reconcile
  allowlisted config/artifact mtimes, and prevent overlapping sync cycles with
  a local plugin-owned lock
- run reusable backend conformance checks against the local-folder reference
  backend and an OSS backend backed by a fake OSS harness service
- support `remote: oss` through the `RemoteBackend` protocol for Alibaba Cloud
  OSS-compatible object storage; live credentials are read only from local
  environment variables
- keep top-level `hermes sync ...` as future work until Hermes core exposes a
  generic plugin CLI-command bridge

## OSS Remote Configuration

For Alibaba Cloud OSS, keep credentials out of `config.yaml` and set them in
the local environment:

```bash
export ALIBABA_CLOUD_ACCESS_KEY_ID=...
export ALIBABA_CLOUD_ACCESS_KEY_SECRET=...
export ALIBABA_CLOUD_SECURITY_TOKEN=... # optional STS token
```

Profile config contains only non-secret routing data:

```yaml
sync:
  remote: oss
  bucket: your-hermes-sync-bucket
  endpoint: https://s3.oss-cn-hangzhou.aliyuncs.com
  region: cn-hangzhou
  prefix: hermes-sync/default-profile
```

The default harness uses an unsigned fake OSS service with a temporary prefix.
Do not use the harness-only `unsigned` or `path_style` settings for a real OSS
bucket. For new Alibaba Cloud OSS users in Chinese mainland regions, a custom
domain may be required for data API operations; keep that domain in
`endpoint` and keep credentials in local environment variables.

Gated live acceptance is available only when you intentionally provide a real
bucket and local credentials:

```bash
export HERMES_SYNC_OSS_BUCKET=...
export HERMES_SYNC_OSS_ENDPOINT=https://s3.oss-cn-hangzhou.aliyuncs.com
export HERMES_SYNC_OSS_REGION=cn-hangzhou
python3 -m harness.oss_live_acceptance
```

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
