# hermes-sync

Language: English | [简体中文](README.zh-CN.md)

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
  README.zh-CN.md
  feature_list.json
  plugin.yaml
  scripts/
    install_dev_plugin.py
  docs/
    architecture.md
    sync-scopes.md
    harness.md
    deployment.md
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
- report incremental sync metrics, including dirty/unchanged object counts,
  hash-cache reuse, uploaded bytes, and per-phase timings
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
  backend and an OSS backend backed by a fake OSS harness service that
  validates the local S3-compatible request subset
- support `remote: oss` through the `RemoteBackend` protocol for Alibaba Cloud
  OSS-compatible object storage; this implementation is complete against fake
  conformance, while live Alibaba Cloud acceptance is a specified/manual gate.
  Live credentials are read only from local environment variables
- support `remote: webdav` through the `RemoteBackend` protocol; this
  implementation is complete against fake conformance for MKCOL, PROPFIND,
  PUT, GET, and DELETE behavior
- support `remote: s3` and `remote: r2` through the `RemoteBackend` protocol;
  this implementation is complete against fake conformance for the standard
  S3-compatible request subset
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

## Installation and Usage

`hermes-sync` is currently installed as a development directory plugin. That
means you need a local checkout of this repository, and the installed plugin
shim points back to that checkout. Do not move or delete the checkout after
installing unless you reinstall the plugin.

```bash
git clone https://github.com/alovwang-sys/hermes-sync.git
cd hermes-sync
```

Run the harness before trying a new checkout against a real profile:

```bash
python3 -m harness.run
```

### Quick Local Smoke Test

The shortest safe path installs the plugin, enables it, and writes a local
folder remote configuration:

```bash
python3 scripts/install_dev_plugin.py --profile ~/.hermes --enable-local
```

The command writes:

```text
~/.hermes/plugins/hermes-sync/plugin.yaml
~/.hermes/plugins/hermes-sync/__init__.py
```

It also updates `~/.hermes/config.yaml` with a safe local remote:

```yaml
plugins:
  enabled:
    - hermes-sync

sync:
  remote: local
  remote_path: /tmp/hermes-sync-dev-remote
  scopes:
    config: true
    sessions: false
    artifacts: true
    memory: false
    skills: false
    plugins: false
    secrets: false
```

If `config.yaml` already exists, the installer creates a timestamped backup
before writing. If the profile already has a top-level `sync:` block, the
installer leaves it unchanged unless you explicitly pass
`--replace-sync-config`:

```bash
python3 scripts/install_dev_plugin.py \
  --profile ~/.hermes \
  --enable-local \
  --replace-sync-config
```

Use a different local remote folder when needed:

```bash
python3 scripts/install_dev_plugin.py \
  --profile ~/.hermes \
  --enable-local \
  --remote-path /path/to/hermes-sync-remote
```

### Manual Install

If you want the installer to write only the plugin files and leave
`config.yaml` untouched:

```bash
python3 scripts/install_dev_plugin.py --profile ~/.hermes
```

Then add `hermes-sync` and a `sync:` block to `~/.hermes/config.yaml`
manually:

```yaml
plugins:
  enabled:
    - hermes-sync

sync:
  remote: local
  remote_path: /tmp/hermes-sync-dev-remote
  scopes:
    config: true
    sessions: false
    artifacts: true
    memory: true
    skills: true
    plugins: true
    secrets: false
```

For a first real-profile smoke test, prefer the narrower quick-start scope
settings: `config: true`, `artifacts: true`, and everything else false.

### Runtime Loading

Hermes must restart or trigger plugin rediscovery after a new install/config
change before `/sync` and the `sync_*` tools are registered. The plugin does not
currently guarantee true hot-plug loading inside an already-running Hermes
process. Restarting Hermes is also the reliable path after changing plugin code
because the development shim imports Python modules from this checkout.

### Daily Commands

Use the slash commands inside Hermes:

```text
/sync status
/sync now
/sync conflicts
/sync pause
/sync resume
```

The top-level `hermes sync ...` CLI remains future work until Hermes core
exposes a generic plugin CLI-command bridge.

Registered tools are available to Hermes as:

- `sync_status`
- `sync_now`
- `sync_list_conflicts`
- `sync_restore_version`

### What Syncs

Sync is scope-based, not whole-directory mirroring.

| Scope | Default | Notes |
| --- | --- | --- |
| `config` | enabled | Syncs non-secret config files such as `config.yaml`. Secret-like keys are skipped. |
| `artifacts` | enabled | Syncs allowlisted files under `artifacts/`, `outputs/`, and `reports/`. |
| `sessions` | disabled | Exports read-only JSON snapshots from `state.db`; does not sync SQLite files. Session text may contain user content. |
| `memory` | disabled | Syncs allowlisted files under `memories/`. |
| `skills` | disabled | Syncs skill files while excluding skill runtime state. |
| `plugins` | disabled | Syncs plugin manifests only; plugin executable code and caches stay local. |
| `secrets` | disabled | Not supported by default. Do not enable for real profiles. |

These paths are blocked even if broad scopes are enabled: `.env`, API keys,
tokens, provider credentials, `state.db`, `state.db-wal`, `state.db-shm`, logs,
caches, tmp files, lock files, and plugin-owned `sync/` metadata.

### Two-Device Local Test

Use the same `remote_path` on two isolated profiles to confirm push/pull before
using cloud storage:

```bash
python3 scripts/install_dev_plugin.py \
  --profile /tmp/hermes-device-a \
  --enable-local \
  --remote-path /tmp/hermes-sync-dev-remote

python3 scripts/install_dev_plugin.py \
  --profile /tmp/hermes-device-b \
  --enable-local \
  --remote-path /tmp/hermes-sync-dev-remote
```

Start Hermes with device A, create or edit an allowed artifact, then run:

```text
/sync now
```

Start Hermes with device B and run:

```text
/sync status
/sync now
```

Confirm that only allowed config/artifact objects appear on device B. Runtime
state, databases, credentials, logs, caches, and lock files should not appear.

### Switching to a Cloud Remote

After the local-folder remote works cleanly, use the installer to write the
cloud routing values for you. Keep credentials in environment variables, not in
synced profile config.

For Alibaba Cloud OSS:

```bash
export ALIBABA_CLOUD_ACCESS_KEY_ID=...
export ALIBABA_CLOUD_ACCESS_KEY_SECRET=...

python3 scripts/install_dev_plugin.py \
  --profile ~/.hermes \
  --enable-sync \
  --remote oss \
  --bucket your-hermes-sync-bucket \
  --endpoint https://s3.oss-cn-hangzhou.aliyuncs.com \
  --region cn-hangzhou \
  --prefix hermes-sync/default-profile \
  --replace-sync-config
```

For Cloudflare R2:

```bash
export AWS_ACCESS_KEY_ID=...
export AWS_SECRET_ACCESS_KEY=...

python3 scripts/install_dev_plugin.py \
  --profile ~/.hermes \
  --enable-sync \
  --remote r2 \
  --bucket your-hermes-sync-bucket \
  --endpoint https://account-id.r2.cloudflarestorage.com \
  --prefix default-profile \
  --replace-sync-config
```

For WebDAV:

```bash
export HERMES_SYNC_WEBDAV_USERNAME=...
export HERMES_SYNC_WEBDAV_PASSWORD=...

python3 scripts/install_dev_plugin.py \
  --profile ~/.hermes \
  --enable-sync \
  --remote webdav \
  --url https://webdav.example.com/hermes-sync \
  --prefix default-profile \
  --replace-sync-config
```

Supported remote values are:

- `local` or `local-folder`
- `oss`, `alibaba-oss`, or `aliyun-oss`
- `webdav` or `web-dav`
- `s3` or `s3-compatible`
- `r2` or `cloudflare-r2`

Use `--include-sessions`, `--include-memory`, `--include-skills`, or
`--include-plugin-manifests` only after the first smoke test passes. The
installer always keeps `secrets: false`.

For real cloud remotes, start with a dedicated test bucket/path prefix and keep
`sessions: false` until the destination and data policy are reviewed. Session
snapshots can include user message text.

## WebDAV Remote Configuration

For WebDAV, keep username and password out of `config.yaml` and set them in the
local environment only when the server requires authentication:

```bash
export HERMES_SYNC_WEBDAV_USERNAME=...
export HERMES_SYNC_WEBDAV_PASSWORD=...
```

Profile config contains only non-secret routing data:

```yaml
sync:
  remote: webdav
  url: https://webdav.example.com/hermes-sync
  prefix: default-profile
```

The default harness uses an unauthenticated fake WebDAV service under a
temporary prefix. It validates the protocol subset locally and does not contact
a real WebDAV server.

## S3/R2 Remote Configuration

For generic S3-compatible remotes, including Cloudflare R2, keep access keys
out of `config.yaml` and set them in the local environment:

```bash
export AWS_ACCESS_KEY_ID=...
export AWS_SECRET_ACCESS_KEY=...
export AWS_SESSION_TOKEN=... # optional
```

Profile config contains only non-secret routing data:

```yaml
sync:
  remote: r2
  bucket: your-hermes-sync-bucket
  endpoint: https://account-id.r2.cloudflarestorage.com
  region: auto
  prefix: default-profile
```

Use `remote: s3` for standard S3-compatible services. The default harness uses
an unsigned fake S3-compatible service with a temporary prefix and never
contacts real cloud storage.

## Project Tracking

- `feature_list.json` is the machine-readable feature inventory, progress
  source, and harness execution contract.
- `docs/architecture.md` defines plugin boundaries, object model, backend
  contract, session sync, and conflict model.
- `docs/sync-scopes.md` defines the explicit sync scopes and default
  exclusions.
- `docs/harness.md` defines the executable harness contract and required
  scenarios.
- `docs/deployment.md` defines the development install and test deployment
  checklist.
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
