"""Hermes sync plugin registration."""

from __future__ import annotations

from .cli import handle_sync_command
from .tools import (
    SYNC_LIST_CONFLICTS_SCHEMA,
    SYNC_NOW_SCHEMA,
    SYNC_RESTORE_VERSION_SCHEMA,
    SYNC_STATUS_SCHEMA,
    sync_list_conflicts_tool,
    sync_now_tool,
    sync_restore_version_tool,
    sync_status_tool,
)


def register(ctx) -> None:
    ctx.register_command(
        "sync",
        handler=handle_sync_command,
        description="Hermes app-aware sync status and controls.",
        args_hint="status|now|pause|conflicts",
    )
    ctx.register_tool(
        name="sync_status",
        toolset="sync",
        schema=SYNC_STATUS_SCHEMA,
        handler=sync_status_tool,
        description=SYNC_STATUS_SCHEMA["description"],
    )
    ctx.register_tool(
        name="sync_now",
        toolset="sync",
        schema=SYNC_NOW_SCHEMA,
        handler=sync_now_tool,
        description=SYNC_NOW_SCHEMA["description"],
    )
    ctx.register_tool(
        name="sync_list_conflicts",
        toolset="sync",
        schema=SYNC_LIST_CONFLICTS_SCHEMA,
        handler=sync_list_conflicts_tool,
        description=SYNC_LIST_CONFLICTS_SCHEMA["description"],
    )
    ctx.register_tool(
        name="sync_restore_version",
        toolset="sync",
        schema=SYNC_RESTORE_VERSION_SCHEMA,
        handler=sync_restore_version_tool,
        description=SYNC_RESTORE_VERSION_SCHEMA["description"],
    )
