# OpenAI Reference Notes

These notes record which official OpenAI docs influenced this project setup.

## AGENTS.md

OpenAI Codex reads `AGENTS.md` files as project instructions and layers them
from global scope down to the current working directory. This project therefore
uses `AGENTS.md` as the canonical instruction file and keeps `AGENT.md` only as
a compatibility note.

Source: https://developers.openai.com/codex/guides/agents-md

## Plugin Packaging

OpenAI Codex plugin docs use a stable kebab-case plugin name and a manifest
under `.codex-plugin/plugin.json`. Hermes has its own plugin system, so this
project keeps the Hermes-facing manifest in `plugin.yaml`; if this project later
needs to ship as a Codex plugin too, add the Codex manifest separately rather
than overloading the Hermes manifest.

Source: https://developers.openai.com/codex/plugins/build

## Harness Design

OpenAI tool harness guidance emphasizes parsing tool operations, validating
paths, applying operations in a controlled workspace, returning one result per
operation, and explicitly deciding atomicity behavior. The sync harness mirrors
that pattern for profile and remote operations.

Source: https://developers.openai.com/api/docs/guides/tools-apply-patch

OpenAI shell tool guidance also emphasizes non-interactive execution,
preserving non-zero outputs, returning timeout outcomes with partial output, and
keeping network access tightly scoped. The sync harness follows the same
practice for command adapter results and trace capture.

Source: https://developers.openai.com/api/docs/guides/tools-shell

## Docs MCP

OpenAI provides a read-only Docs MCP server for developer documentation. Future
OpenAI-related changes in this repository should use that MCP server or official
OpenAI docs as the source of truth.

Source: https://developers.openai.com/learn/docs-mcp
