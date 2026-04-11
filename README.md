# Multi-Agent Slack Bot System

A lightweight system where multiple Slack bots converse with each other, each powered by Claude with its own persona defined in a SOUL.md file. Supports two backends: direct Anthropic API (fast, ~2-5s) or Claude CLI with OAuth (~6s).

Each bot:
- Responds when @mentioned in a channel or via DM
- Calls Claude via direct API or CLI subprocess (auto-selected based on auth config)
- Can @mention other bots, triggering chain reactions

## Prerequisites

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) package manager
- One of:
  - `ANTHROPIC_API_KEY` — direct API access (recommended, fastest)
  - [Claude Code CLI](https://claude.ai/code) (`claude`) installed and authenticated — uses `CLAUDE_CODE_OAUTH_TOKEN`
- Slack Bot/App tokens for each agent (Socket Mode enabled)

## Quick Start

```bash
# 1. Install dependencies
uv sync

# 2. Create .env from template
cp .env.example .env
# Edit .env with your actual tokens

# 3. Create SOUL.md for your agents
mkdir -p agents/my-agent
# Write agents/my-agent/SOUL.md

# 4. Run
uv run multi-agent
```

## Environment Variables

Copy `.env.example` to `.env` and fill in the values.

**Global settings:**

| Variable | Default | Description |
|----------|---------|-------------|
| `ANTHROPIC_API_KEY` | — | Anthropic API key (enables fast direct API backend) |
| `CLAUDE_CODE_OAUTH_TOKEN` | — | OAuth token (fallback: uses Claude CLI subprocess) |
| `CLAUDE_MODEL` | `claude-sonnet-4-20250514` | Model to use |
| `CLAUDE_CLI` | `claude` | Path to Claude Code CLI binary |
| `MAX_TOKENS` | `4096` | Max response tokens |
| `AGENTS_DIR` | `./agents` | Base directory for SOUL.md files |
| `MAX_CHAIN_DEPTH` | `10` | Max bot responses per thread (loop prevention) |
| `COOLDOWN_SECONDS` | `2.0` | Delay between consecutive responses (seconds) |

**Per-agent settings** (replace `{NAME}` with agent identifier, e.g. `MY_AGENT`):

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `AGENT_{NAME}_ENABLED` | No | `true` | Enable/disable agent (`false`, `0`, `no`, `off` to disable) |
| `AGENT_{NAME}_BOT_TOKEN` | Yes | — | Slack Bot token (`xoxb-...`) |
| `AGENT_{NAME}_APP_TOKEN` | Yes | — | Slack App token (`xapp-...`) |
| `AGENT_{NAME}_SOUL` | No | `./agents/{name}/SOUL.md` | Path to SOUL.md |
| `AGENT_{NAME}_NAME` | No | Titlecased `{NAME}` | Display name |

**Example `.env`:**

```bash
# Enable/disable without removing config
AGENT_MY_AGENT_ENABLED=true
AGENT_MY_AGENT_NAME=My Agent
AGENT_MY_AGENT_BOT_TOKEN=xoxb-...
AGENT_MY_AGENT_APP_TOKEN=xapp-...
AGENT_MY_AGENT_SOUL=./agents/my-agent/SOUL.md
```

## Adding an Agent

1. Create a directory under `agents/`:

```
agents/
└── my-agent/
    └── SOUL.md
```

2. Write a `SOUL.md` that defines the agent's persona. This becomes the system prompt for Claude calls. The system automatically appends a team roster and communication rules.

A typical SOUL.md contains:
- **Core Identity** — role, philosophy, core principles
- **Knowledge references** — paths to domain knowledge files (`~/.claude/knowledge/{role}/`)
- **Task-knowledge mapping** — which knowledge files to consult per task type
- **Communication protocol** — Slack interaction rules, escalation policy
- **Loop prevention rules** — max round-trips, escalation triggers

3. Add environment variables to `.env`:

```bash
AGENT_MY_AGENT_ENABLED=true
AGENT_MY_AGENT_NAME=My Agent
AGENT_MY_AGENT_BOT_TOKEN=xoxb-...
AGENT_MY_AGENT_APP_TOKEN=xapp-...
AGENT_MY_AGENT_SOUL=./agents/my-agent/SOUL.md
```

4. Run `uv run multi-agent`. The agent is auto-discovered from `AGENT_{NAME}_BOT_TOKEN` env vars.

## Slack App Setup

Each agent requires its own Slack App with Socket Mode enabled:

1. Go to [api.slack.com/apps](https://api.slack.com/apps) and create a new app
2. Enable **Socket Mode** (Settings > Socket Mode) — copy the App-Level Token (`xapp-...`)
3. Add **Bot Token Scopes** (OAuth & Permissions):
   - `app_mentions:read`
   - `chat:write`
   - `im:history`
   - `reactions:read`
   - `reactions:write`
   - `users:read`
4. Enable **Event Subscriptions** and subscribe to:
   - `app_mention`
   - `message.im`
5. Install the app to your workspace — copy the Bot Token (`xoxb-...`)
6. Set the tokens in `.env`

Repeat for each agent bot.

## How It Works

- **Single process, multi-bot**: all bots run concurrently via `asyncio.gather`. Each bot is an independent `AgentBot` instance with its own Slack `AsyncApp` and Socket Mode connection.
- **Hybrid backend**: if `ANTHROPIC_API_KEY` is set, uses direct Anthropic API (~2-5s). Otherwise falls back to `claude -p` CLI subprocess with hooks disabled (~6s).
- **Chain reactions**: if a bot's response @mentions another bot, that bot picks up and responds.
- **Loop prevention**: per-thread response counter capped at `MAX_CHAIN_DEPTH`. Bots ignore their own messages.
- **Enable/disable**: each agent can be toggled via `AGENT_{NAME}_ENABLED` without removing config.
- **Agent discovery**: `load_agents()` scans environment variables for `AGENT_{NAME}_BOT_TOKEN` patterns. No registry file needed.
- **Conversation history**: in-memory per thread (last 20 messages), lost on restart.

## Special Thanks

- [juchanhwang/my-harness](https://github.com/juchanhwang/my-harness) — agent persona definitions
