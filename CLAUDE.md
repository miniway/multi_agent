# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Multi-Agent Slack Bot system where multiple Slack bots converse with each other. Each bot connects via Socket Mode, responds when @mentioned, calls Claude with a persona defined in a SOUL.md file, and posts the response directly to the Slack channel. Bots can @mention other bots, creating chain reactions. Each agent maintains a persistent workspace with long-term memory, tools reference, and daily conversation logs.

## Commands

```bash
# Install dependencies
uv sync

# Run (loads .env automatically via python-dotenv)
uv run multi-agent

# Or run directly
uv run python run.py

# Run via supervisor
supervisorctl start agent
supervisorctl stop agent
supervisorctl restart agent
```

## Architecture

**Single-process, multi-bot**: All bots run concurrently in one Python process via `asyncio.gather`. Each bot is an independent `AgentBot` instance with its own Slack `AsyncApp` and Socket Mode connection.

**Key flow**: Slack @mention → `AgentBot._handle_message` → resolve mentions → append to conversation history → build system prompt (SOUL.md + TOOLS.md + MEMORY.md + team context + Slack rules) → `PersistentClaude.send()` → extract `<memory>` tags → sanitize for Slack mrkdwn → post to channel → log to daily file.

**Core components** (all in `multi_agent.py`):
- `AgentConfig` — dataclass holding bot tokens, SOUL.md/TOOLS.md content, agent workspace directory, and per-thread conversation history
- `PersistentClaude` — persistent Claude CLI subprocess per agent using stream-json protocol (`-p --input-format stream-json --output-format stream-json`). Sends NDJSON messages via stdin, reads events from stdout. Restarts on process death.
- `_call_claude_api()` — direct Anthropic API backend (used when `ANTHROPIC_API_KEY` is set)
- `AgentBot` — one per bot; owns an `AsyncApp`, `PersistentClaude` instance, registers event handlers, manages loop prevention, caches user names and team info

**Agent workspace** (`agents/{name}/`):
- `SOUL.md` — persona definition (loaded at startup)
- `TOOLS.md` — tools/scripts reference (loaded at startup)
- `MEMORY.md` — long-term memory (loaded on subprocess start, updated via `<memory>` tags)
- `CRON.md` — scheduled tasks (parsed at startup, runs as asyncio tasks)
- `memory/YYYY-MM-DD.md` — daily conversation logs (auto-appended)
- `scripts/` — agent-specific scripts (optional)

**Agent discovery**: `load_agents()` scans environment variables for `AGENT_{NAME}_BOT_TOKEN` patterns. Each agent needs `_BOT_TOKEN`, `_APP_TOKEN`, and optionally `_SOUL`, `_NAME`, `_ENABLED`.

## Configuration

All config is via environment variables (see `.env`):
- `ANTHROPIC_API_KEY` or `CLAUDE_CODE_OAUTH_TOKEN` — one required
- `CLAUDE_MODEL` — defaults to `claude-sonnet-4-20250514`
- `CLAUDE_CLI` — path to claude CLI binary (default `claude`)
- `ALLOWED_TOOLS` — `default` for all tools, or comma-separated list
- `PERMISSION_MODE` — Claude CLI permission mode (`default`, `bypassPermissions`, etc.)
- `MAX_CHAIN_DEPTH` — per-thread bot response limit (default 10)
- `MAX_TURNS` — max agentic turns per CLI call (default 10)
- `CLI_TIMEOUT` — response timeout in seconds (default 300)
- `MAX_MEMORY_ENTRIES` — max entries per agent MEMORY.md (default 50)
- `LOG_DIR` — log directory (default `./logs`)

## Key Design Decisions

- **Persistent subprocess**: Claude CLI runs as a long-lived process per agent using `--input-format stream-json --output-format stream-json --verbose`. First message has cold start (~5-6s), subsequent messages reuse the process (~2-3s). Restarts only when process dies.
- **Stream-json NDJSON protocol**: Messages sent as `{"type":"user","message":{"role":"user","content":"..."}}` via stdin. Responses read as stream-json events, waiting for `{"type":"result"}`.
- **Channel responses**: Bot responses are posted directly to the channel, not as thread replies.
- **Loop prevention**: Per-thread response counter (`MAX_CHAIN_DEPTH`). Stale threads (>200) auto-pruned.
- **Conversation history**: In-memory per thread, capped at 20 messages. Lost on restart.
- **Long-term memory**: `<memory>` tags in responses are extracted, saved to MEMORY.md, stripped before Slack. Loaded into system prompt on subprocess start.
- **Slack mrkdwn**: Post-processing converts `**bold**` → `*bold*`, `## Header` → `*Header*`, `[text](url)` → `<url|text>`, strips XML junk.
- **Cron scheduler**: `CRON.md` parsed at startup → asyncio tasks per schedule. Supports `every Xm/Xh`, `daily HH:MM`, `weekdays HH:MM`. Targets: `channel` or `dm`. Post modes: `always`, `conditional` (suppressed by `<nopost/>` tag), `silent` (log only). `enabled: false` pauses a task. CRON.md is re-read before each execution (hot-reload, no restart needed). Cron responses go through same memory/sanitize pipeline as regular messages.
- **Graceful shutdown**: SIGINT/SIGTERM → cancel cron tasks → save session state to daily log → stop all Claude subprocesses.
- **Supervisor**: Managed via supervisor with `stopasgroup=true` + `killasgroup=true` to prevent zombie processes.
