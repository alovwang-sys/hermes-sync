# Sync Harness

This directory contains the executable sync harness.

Do not commit real Hermes profile data here. Test setup should create temporary
profiles and remotes under the system temp directory, then write only sanitized
fixtures and traces.

Validate `feature_list.json` before any executable harness run:

```bash
python3 -m json.tool feature_list.json
```

Run the current harness:

```bash
python3 -m harness.run
```

The harness creates temporary Hermes profiles, installs this plugin into those
profiles through Hermes' directory-plugin shape, creates a temporary
local-folder remote, seeds sanitized fixtures, and asserts that `/sync status`
and `sync_status` remain read-only while `push`, `pull`, `once`, tombstones,
configured scope disabling, memory/skills/plugin-manifest sync, conflicts,
structured/text merges, version restore, bounded continuous sync, hook wake
debounce, mtime polling, single-flight locking, pause/resume pending drain,
local-folder backend conformance, OSS fake backend conformance, WebDAV fake
backend conformance, S3/R2 fake backend conformance, and fake round trips for
`remote: oss`, `remote: webdav`, and `remote: r2` exchange only supported
objects through plugin-owned staging and temporary remotes.

See `docs/harness.md` for the process, adapter contract, required scenarios,
and acceptance checklist.

The continuous auto-trigger scenarios are executable: hook wake debounce,
allowlisted mtime polling, single-flight sync locking, and pause/resume pending
drain. They are tracked in `feature_list.json` and must keep worker state,
locks, logs, caches, tmp files, and other runtime-only data out of remotes.

The Phase 5 backend conformance scenarios are executable for the local-folder
reference backend, the OSS backend through a temporary fake OSS HTTP service,
the generic S3/R2 backend through a temporary fake S3 HTTP service, and the
WebDAV backend through a temporary fake WebDAV HTTP service. The fake OSS
service validates the unsigned S3-compatible subset used by the harness:
path-style object operations, ListObjectsV2 prefix listing, compatibility
headers, and payload hashes. The fake S3 service validates the same generic
subset while rejecting OSS-only compatibility headers. The fake WebDAV service
validates MKCOL, PROPFIND, PUT, GET, and DELETE behavior. Future Git backends
must pass the same conformance helper before they are used by end-to-end sync
scenarios. Live Alibaba Cloud OSS acceptance is intentionally gated,
specified/manual, and not part of the default harness run; use
`python3 -m harness.oss_live_acceptance` only with an intentional bucket,
isolated prefix, and local environment credentials.
