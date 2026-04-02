# Multi-Agent Slack Bot System

A lightweight system where multiple Slack bots converse with each other, each powered by Claude via the `claude` CLI (print mode) with its own persona defined in a SOUL.md file.

Each bot:
- Responds when @mentioned in a channel or via DM
- Calls Claude via `claude -p` CLI subprocess (with hooks disabled for speed)
- Can @mention other bots, triggering chain reactions

## Prerequisites

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) package manager
- [Claude Code CLI](https://claude.ai/code) (`claude`) installed and authenticated
- Slack Bot/App tokens for each agent (Socket Mode enabled)

## Quick Start

```bash
# 1. Install dependencies
uv sync

# 2. Create .env from template
cp .env.example .env
# Edit .env with your actual tokens

# 3. Create SOUL.md files for your agents
mkdir -p agents/friday
# See "Configuring SOUL.md" below

# 4. Run
uv run multi-agent
```

## Environment Variables

Copy `.env.example` to `.env` and fill in the values.

**Global settings:**

| Variable | Default | Description |
|----------|---------|-------------|
| `ANTHROPIC_API_KEY` | — | Claude API key (used by CLI auth) |
| `CLAUDE_CODE_OAUTH_TOKEN` | — | OAuth token (used by CLI auth) |
| `CLAUDE_MODEL` | `claude-sonnet-4-20250514` | Model to use |
| `CLAUDE_CLI` | `claude` | Path to Claude Code CLI binary |
| `MAX_TOKENS` | `4096` | Max response tokens |
| `AGENTS_DIR` | `./agents` | Base directory for SOUL.md files |
| `MAX_CHAIN_DEPTH` | `10` | Max bot responses per thread (loop prevention) |

**Per-agent settings** (replace `{NAME}` with agent identifier, e.g. `FRIDAY`):

| Variable | Required | Description |
|----------|----------|-------------|
| `AGENT_{NAME}_BOT_TOKEN` | Yes | Slack Bot token (`xoxb-...`) |
| `AGENT_{NAME}_APP_TOKEN` | Yes | Slack App token (`xapp-...`) |
| `AGENT_{NAME}_SOUL` | No | Path to SOUL.md (defaults to `./agents/{name}/SOUL.md`) |
| `AGENT_{NAME}_NAME` | No | Display name (defaults to titlecased `{NAME}`) |

## Configuring SOUL.md

Each agent needs a `SOUL.md` file that defines its persona, role, and behavior. Place it at the path specified by `AGENT_{NAME}_SOUL` (or the default `./agents/{name}/SOUL.md`).

**Example directory structure:**

```
agents/
├── friday/
│   └── SOUL.md
├── black-widow/
│   └── SOUL.md
└── hulk/
    └── SOUL.md
```

**Example `agents/friday/SOUL.md`:**

```markdown
# FRIDAY — Product Owner

You are FRIDAY, the Product Owner of this team.

## Responsibilities
- Break down user requests into actionable tasks
- Assign tasks to the appropriate team member
- Track progress and ensure quality

## Personality
- Professional but approachable
- Decisive and organized
- Focuses on delivering value

## Team
- @Black Widow — UI/UX Designer
- @Hulk — Backend Engineer
```

The SOUL.md content becomes the system prompt for that agent's Claude calls. The system automatically appends a team roster and communication rules.

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

- All bots run concurrently in a single Python process via `asyncio`
- When a bot is @mentioned, it builds a conversation from thread history, calls Claude via `claude -p` CLI subprocess, and posts the response directly to the channel
- The CLI is invoked with hooks/plugins disabled (`--settings '{"hooks":{}}' --setting-sources '' --disable-slash-commands`) for ~6s response time instead of ~50s
- If the response @mentions another bot, that bot picks up and responds (chain reaction)
- Loop prevention: each thread has a max response count (`MAX_CHAIN_DEPTH`)
- Conversation history is kept in-memory (last 20 messages per thread), lost on restart
