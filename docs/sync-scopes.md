# Sync Scopes

The plugin syncs by scope rather than by directory. Each scope has an exporter,
an importer, ignore rules, and conflict behavior.

## Default Configuration

```yaml
sync:
  enabled: true
  remote: local
  remote_path: /path/to/local-folder-remote
  device_name: amos-laptop

  scopes:
    config: true
    sessions: false
    memory: false
    artifacts: true
    skills: false
    plugins: false
    secrets: false

  exclude:
    - logs/**
    - cache/**
    - tmp/**
    - locks/**
    - "*.db"
    - "*.db-wal"
    - "*.db-shm"
    - ".env"
```

## Scope Rules

### config

Sync non-secret configuration only. Device-local overrides must win for fields
that are inherently machine-specific, such as local paths, preferred shell, or
provider credentials.

### sessions

Export session snapshots in version 1 through read-only SQLite queries. Do not
sync `state.db` or its WAL/SHM files. Other devices store pulled snapshots as
plugin-owned read-only history under `sync/sessions/`.

Disabled by default for real profiles. Enable it only after reviewing that
session text is acceptable to copy to the selected remote.

### memory

Disabled by default. When enabled, sync allowlisted memory files under
`memories/`; lock files and runtime state stay local.

### artifacts

Sync user-visible outputs such as reports, documents, saved files, and explicit
agent-generated deliverables. Do not sync transient tool logs or caches.

### skills

Disabled by default. When enabled, sync allowlisted skill files under `skills/`.
Skill hub state, curator state, logs, locks, caches, tmp files, and secret-like
paths stay local.

### plugins

Disabled by default. When enabled, sync plugin manifests only
(`plugin.yaml`, `plugin.yml`, or `plugin.json`). Plugin Python code,
`__pycache__`, runtime caches, dependency directories, and vendored trees stay
local.

### secrets

Disabled by default. Any future support must use end-to-end encryption and must
require explicit user opt-in.

### logs/cache/runtime

Never sync these. They are local observability and runtime details.

## Import Policy

Imports must be explicit and reversible:

- update `manifest.sqlite` before applying user-visible changes
- preserve previous versions where practical
- create tombstones for deletes
- surface conflicts through `/sync conflicts` and `sync_list_conflicts`
- avoid silent overwrites for plugins, skills, and config keys with unclear
  ownership
