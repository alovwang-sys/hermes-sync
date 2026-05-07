# Test Deployment

Use this path for a fast local deployment into a Hermes profile. The harness
remains the safety gate; deployment should start with a local-folder remote
before any live cloud backend.

## Install Development Plugin

From this repository:

```bash
python3 scripts/install_dev_plugin.py --profile ~/.hermes
```

For the shortest local smoke-test path, install, enable the plugin, and add a
safe local-folder remote in one command:

```bash
python3 scripts/install_dev_plugin.py --profile ~/.hermes --enable-local
```

That command backs up an existing `config.yaml` before writing changes. If the
profile already has a top-level `sync:` block, the installer leaves that block
unchanged unless you explicitly pass `--replace-sync-config`.

Dry run first if you want to check paths:

```bash
python3 scripts/install_dev_plugin.py --profile ~/.hermes --enable-local --dry-run
```

The installer writes:

```text
~/.hermes/plugins/hermes-sync/plugin.yaml
~/.hermes/plugins/hermes-sync/__init__.py
```

Without `--enable-local`, it does not edit `config.yaml`. It never stores
credentials.

## Minimal Local Remote Config

Add this to the Hermes profile `config.yaml`:

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

With `plugins: true`, only plugin manifests are synced. Plugin executable code,
runtime caches, and dependency folders remain local.

Then use in Hermes:

```text
/sync status
/sync now
/sync conflicts
/sync pause
/sync resume
```

Top-level `hermes sync ...` remains blocked until Hermes core exposes a generic
plugin CLI bridge.

## Runtime Loading

Hermes supports directory plugins through `plugin.yaml` plus `__init__.py`, but
this plugin does not currently provide true hot-plug behavior on its own. A
running Hermes process must either restart or trigger Hermes core plugin
rediscovery before newly installed files and updated `plugins.enabled` entries
register `/sync` and the `sync_*` tools.

For development, the installed shim points back to this repository checkout.
Code changes still depend on Hermes/Python module reload behavior, so restarting
Hermes is the reliable path after changing plugin code.

For a real profile, keep `sessions: false` for the first smoke test. Session
snapshots can include user message text, so enable that scope only after the
remote destination and acceptance criteria are reviewed.

## Real Profile Smoke Script

The repository includes a guarded smoke runner for local deployment testing:

```bash
python3 scripts/real_deployment_smoke.py --allow-real-profile --reset-remote
```

It backs up `~/.hermes/config.yaml`, installs a minimal local remote config,
creates `artifacts/hermes-sync-smoke.txt`, runs `/sync status` and `/sync now`
through Hermes' plugin manager, pulls into `/tmp/hermes-sync-real-target`, and
checks for forbidden paths or secret-like markers in `/tmp/hermes-sync-real-remote`.

## OSS Test Config

Keep credentials out of `config.yaml`. The profile config should contain only
non-secret routing values:

```yaml
plugins:
  enabled:
    - hermes-sync

sync:
  remote: oss
  bucket: your-test-bucket
  endpoint: https://s3.oss-cn-hangzhou.aliyuncs.com
  region: cn-hangzhou
  prefix: hermes-sync/default
  scopes:
    config: true
    sessions: false
    artifacts: true
    memory: false
    skills: false
    plugins: false
    secrets: false
```

Load credentials from the shell or service environment:

```bash
export ALIBABA_CLOUD_ACCESS_KEY_ID=...
export ALIBABA_CLOUD_ACCESS_KEY_SECRET=...
```

For live acceptance, prefer a separate environment file outside the repository,
for example `/tmp/hermes-sync-oss-live.env`, with mode `600`. The acceptance
runner requires an isolated prefix under `hermes-sync-live-acceptance/`.

```bash
python3 -m harness.oss_live_acceptance
```

## Deployment Checklist

- Run `python3 -m harness.run` before deploying a new plugin checkout.
- Install the plugin with `scripts/install_dev_plugin.py`.
- Start with `remote: local` and run `/sync status`.
- Run `/sync now` once and confirm only allowed config/artifact/session objects
  are exchanged.
- Switch to OSS only after local remote behavior is clean.
- Keep `.env`, database files, provider credentials, logs, caches, tmp files,
  lock files, and watcher state out of sync scope.
