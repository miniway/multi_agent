# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Multi-Agent Slack Bot system where multiple Slack bots converse with each other. Each bot connects via Socket Mode, responds when @mentioned, calls Claude with a persona defined in a SOUL.md file, and posts the response directly to the Slack channel. Bots can @mention other bots, creating chain reactions. Supports hybrid backends: direct Anthropic API (fast, ~2-5s) or Claude CLI subprocess with OAuth (~6s).

## Commands

```bash
# Install dependencies
uv sync

# Run (loads .env automatically via python-dotenv)
uv run multi-agent

# Or run directly
uv run python run.py
```

## Architecture

**Single-process, multi-bot**: All bots run concurrently in one Python process via `asyncio.gather`. Each bot is an independent `AgentBot` instance with its own Slack `AsyncApp` and Socket Mode connection.

**Key flow**: Slack @mention → `AgentBot._handle_message` → resolve mentions to names → append to per-thread conversation history → build system prompt (SOUL.md + team context) → async `call_claude()` → post response directly to Slack channel.

**Core components** (all in `multi_agent.py`):
- `AgentConfig` — dataclass holding bot tokens, SOUL.md content, and per-thread conversation history
- `call_claude()` — hybrid dispatcher: uses `_call_claude_api()` (direct Anthropic API) when `ANTHROPIC_API_KEY` is set, otherwise falls back to `_call_claude_sdk()` (Claude CLI subprocess with hooks disabled)
- `AgentBot` — one per bot; owns an `AsyncApp`, registers `app_mention`, DM, and reaction handlers, manages loop prevention counters

**Agent discovery**: `load_agents()` scans environment variables for `AGENT_{NAME}_BOT_TOKEN` patterns. Each agent needs three env vars: `_BOT_TOKEN`, `_APP_TOKEN`, and optionally `_SOUL` (path to SOUL.md) and `_NAME` (display name).

## Configuration

All config is via environment variables (see `.env.example`):
- `ANTHROPIC_API_KEY` or `CLAUDE_CODE_OAUTH_TOKEN` — one required (OAuth used via Claude CLI auth)
- `CLAUDE_MODEL` — defaults to `claude-sonnet-4-20250514`
- `CLAUDE_CLI` — path to claude CLI binary (default `claude`)
- `MAX_CHAIN_DEPTH` — per-thread bot response limit (loop prevention, default 10)
- `AGENTS_DIR` — directory for SOUL.md files (default `./agents`)

## Key Design Decisions

- **Hybrid backend**: `ANTHROPIC_API_KEY` → direct Anthropic SDK (`anthropic.AsyncAnthropic`, ~2-5s). `CLAUDE_CODE_OAUTH_TOKEN` only → Claude CLI subprocess (`claude -p --output-format json` with hooks disabled, ~6s). The `call_claude()` function dispatches automatically based on which env var is set.
- **Channel responses**: Bot responses are posted directly to the channel, not as thread replies.
- **Loop prevention**: Per-thread response counter (`MAX_CHAIN_DEPTH`). Bots ignore their own messages but process messages from other bots.
- **Conversation history**: Stored in-memory per thread (`channel:thread_ts`), capped at 20 messages. Lost on restart.
- **SOUL.md**: Each agent's persona/role definition. The system prompt combines SOUL.md content with a team roster and system rules.
- **Non-blocking reactions**: Typing indicator (👀 emoji) add/remove uses `asyncio.create_task()` to avoid blocking the response flow.
