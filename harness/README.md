# Sync Harness

This directory contains the executable sync harness.

Do not commit real Hermes profile data here. Test setup should create temporary
profiles and remotes under the system temp directory, then write only sanitized
fixtures and traces.

Validate `feature_list.json` before any executable harness run:

```bash
python -m json.tool feature_list.json
```

Run the current harness:

```bash
env PYTHONDONTWRITEBYTECODE=1 python -m harness.run
```

The harness creates temporary Hermes profiles, installs this plugin into those
profiles through Hermes' directory-plugin shape, creates a temporary
local-folder remote, seeds sanitized fixtures, and asserts that `/sync status`
and `sync_status` remain read-only while `push`, `pull`, and `once` exchange
only supported config/artifact objects through `sync/outbox`, `sync/inbox`, and
the temporary remote.

See `docs/harness.md` for the process, adapter contract, required scenarios,
and acceptance checklist.
