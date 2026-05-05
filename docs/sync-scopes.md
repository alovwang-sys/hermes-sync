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
    sessions: true
    memory: true
    artifacts: true
    skills: true
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

Export session snapshots in version 1. Do not sync `state.db` or its WAL/SHM
files. Other devices can import snapshots or show them as read-only history.

### memory

Sync only memory provider data that has an explicit export/import contract. Do
not scrape private provider storage directly.

### artifacts

Sync user-visible outputs such as reports, documents, saved files, and explicit
agent-generated deliverables. Do not sync transient tool logs or caches.

### skills

Sync skill manifests and enablement state. Do not sync dependency directories
until dependency installation is modeled explicitly.

### plugins

Disabled by default. When enabled, sync plugin manifests and enablement state,
not runtime caches or vendored dependency trees.

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
