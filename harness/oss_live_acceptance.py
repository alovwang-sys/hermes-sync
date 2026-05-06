"""Gated live Alibaba Cloud OSS acceptance runner.

This module is intentionally outside the default harness. It requires explicit
environment configuration and uses a temporary profile plus an isolated OSS
prefix so normal harness runs never touch real cloud storage.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path
from typing import Any, Dict

from hermes_sync.remotes import OssBackend

from .sync_harness import SyncHarness, require


REQUIRED_ENV = (
    "HERMES_SYNC_OSS_BUCKET",
    "HERMES_SYNC_OSS_ENDPOINT",
    "HERMES_SYNC_OSS_REGION",
    "ALIBABA_CLOUD_ACCESS_KEY_ID",
    "ALIBABA_CLOUD_ACCESS_KEY_SECRET",
)


def run() -> Dict[str, Any]:
    missing = [name for name in REQUIRED_ENV if not os.environ.get(name)]
    if missing:
        return {
            "status": "skipped",
            "reason": "missing_required_environment",
            "missing": missing,
        }

    bucket = os.environ["HERMES_SYNC_OSS_BUCKET"]
    endpoint = os.environ["HERMES_SYNC_OSS_ENDPOINT"]
    region = os.environ["HERMES_SYNC_OSS_REGION"]
    prefix = os.environ.get("HERMES_SYNC_OSS_PREFIX") or (
        "hermes-sync-live-acceptance/"
        + time.strftime("%Y%m%d-%H%M%S", time.gmtime())
        + "-"
        + uuid.uuid4().hex[:12]
    )
    prefix = prefix.strip("/")
    if not prefix.startswith("hermes-sync-live-acceptance/") or prefix.count("/") < 1:
        return {
            "status": "error",
            "reason": "unsafe_live_acceptance_prefix",
            "message": "HERMES_SYNC_OSS_PREFIX must start with hermes-sync-live-acceptance/",
        }
    backend = OssBackend(bucket=bucket, endpoint=endpoint, region=region, prefix=prefix)
    cleanup_errors: list[str] = []
    try:
        _delete_prefix(backend)
        with SyncHarness.temporary() as harness:
            source = harness.make_profile("oss-live-source")
            target = harness.make_profile("oss-live-target")
            _write_oss_config(source, bucket, endpoint, region, prefix)
            _write_oss_config(target, bucket, endpoint, region, prefix)

            artifact = source / "artifacts" / "oss-live.txt"
            artifact.parent.mkdir(exist_ok=True)
            artifact.write_text("OSS live acceptance fixture.\n", encoding="utf-8")
            (source / ".env").write_text("TOKEN=not-used\n", encoding="utf-8")
            (source / "logs").mkdir(exist_ok=True)
            (source / "logs" / "agent.log").write_text("runtime only\n", encoding="utf-8")

            push = harness.run_push(source)
            require(push["status"] == "ok", "live OSS push did not complete")
            remote_paths = {metadata.logical_path for metadata in backend.list_objects()}
            require("artifacts/oss-live.txt" in remote_paths, "live OSS artifact was not uploaded")
            require("config.yaml" in remote_paths, "live OSS config was not uploaded")
            require(".env" not in json.dumps(sorted(remote_paths)), ".env path leaked to live OSS")
            require("agent.log" not in json.dumps(sorted(remote_paths)), "log path leaked to live OSS")

            pull = harness.run_pull(target)
            require(pull["status"] == "ok", "live OSS pull did not complete")
            imported = target / "artifacts" / "oss-live.txt"
            require(imported.exists(), "live OSS artifact was not imported")
            require(
                imported.read_text(encoding="utf-8") == "OSS live acceptance fixture.\n",
                "live OSS imported content did not match",
            )
            return {
                "status": "completed",
                "bucket": bucket,
                "endpoint": endpoint,
                "region": region,
                "prefix": prefix,
                "actions": {
                    "uploaded": push["actions"]["uploaded"],
                    "downloaded": pull["actions"]["downloaded"],
                    "imported": pull["actions"]["imported"],
                },
            }
    finally:
        try:
            _delete_prefix(backend)
        except Exception as exc:
            cleanup_errors.append(str(exc))
        if cleanup_errors:
            print(json.dumps({"status": "cleanup_warning", "errors": cleanup_errors}, sort_keys=True))


def _write_oss_config(profile: Path, bucket: str, endpoint: str, region: str, prefix: str) -> None:
    (profile / "config.yaml").write_text(
        "plugins:\n"
        "  enabled:\n"
        "    - hermes-sync\n"
        "sync:\n"
        "  remote: oss\n"
        f"  bucket: {bucket}\n"
        f"  endpoint: {endpoint}\n"
        f"  region: {region}\n"
        f"  prefix: {prefix}\n"
        "  scopes:\n"
        "    config: true\n"
        "    sessions: true\n"
        "    memory: true\n"
        "    artifacts: true\n"
        "    skills: true\n"
        "    plugins: false\n"
        "    secrets: false\n",
        encoding="utf-8",
    )


def _delete_prefix(backend: OssBackend) -> None:
    if not backend.prefix:
        raise ValueError("live acceptance cleanup requires a non-empty prefix")
    for key in backend._list_keys(backend.prefix + "/"):  # harness cleanup only
        backend._delete_key(key)


def main() -> int:
    result = run()
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["status"] in {"completed", "skipped"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
