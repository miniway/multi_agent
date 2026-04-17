# Multi-Agent Slack Bot System

A lightweight system where multiple Slack bots converse with each other, each powered by Claude with its own persona defined in a SOUL.md file. Supports two backends: direct Anthropic API (fast, ~2-5s) or persistent Claude CLI subprocess with OAuth.

Each bot:
- Responds when @mentioned in a channel or via DM
- Calls Claude via direct API or persistent CLI subprocess (auto-selected based on auth config)
- Can @mention other bots, triggering chain reactions
- Maintains persistent workspace (MEMORY.md, TOOLS.md, daily logs)

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

# 3. Create workspace for your agent
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
| `AGENTS_DIR` | `./agents` | Base directory for agent workspaces |
| `MAX_CHAIN_DEPTH` | `10` | Max bot responses per thread (loop prevention) |
| `MAX_MEMORY_ENTRIES` | `50` | Max entries in each agent's MEMORY.md |
| `CLI_TIMEOUT` | `300` | Claude CLI response timeout (seconds) |
| `MAX_TURNS` | `10` | Max agentic turns per Claude CLI call |
| `ALLOWED_TOOLS` | `WebSearch,WebFetch,Read` | Comma-separated tool list, or `default` for all tools |
| `PERMISSION_MODE` | `default` | Claude CLI permission mode (`default`, `acceptEdits`, `dontAsk`, `bypassPermissions`) |
| `LOG_DIR` | `./logs` | Log file directory |

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
CLAUDE_CLI=/usr/local/bin/claude
ALLOWED_TOOLS=default
PERMISSION_MODE=bypassPermissions
MAX_TURNS=20
CLI_TIMEOUT=300

AGENT_MY_AGENT_ENABLED=true
AGENT_MY_AGENT_NAME=My Agent
AGENT_MY_AGENT_BOT_TOKEN=xoxb-...
AGENT_MY_AGENT_APP_TOKEN=xapp-...
AGENT_MY_AGENT_SOUL=./agents/my-agent/SOUL.md
```

## Adding an Agent

1. Create a workspace directory under `agents/`:

```
agents/
└── my-agent/
    ├── SOUL.md       # Persona (required)
    ├── TOOLS.md      # Tools, scripts, references (auto-created if missing)
    ├── MEMORY.md     # Long-term memory (auto-created, agent-managed)
    ├── CRON.md       # Scheduled tasks (optional)
    ├── scripts/      # Agent-specific scripts
    └── memory/       # Daily conversation logs (auto-created)
        └── 2026-04-12.md
```

2. Write a `SOUL.md` that defines the agent's persona. This becomes the system prompt for Claude calls. The system automatically appends team roster, Slack formatting rules, and memory instructions.

3. Optionally add a `TOOLS.md` with agent-specific references (API keys, scripts, workflows).

4. Add environment variables to `.env` and run `uv run multi-agent`.

## Agent Workspace

Each agent has a persistent workspace directory with:

- **SOUL.md** — persona, role, principles, knowledge references
- **TOOLS.md** — local tools, scripts, API references, workflows
- **MEMORY.md** — long-term memory across conversations. Agents save memories using `<memory>...</memory>` tags in responses. Tags are stripped before posting to Slack and appended to MEMORY.md. Loaded into system prompt on subprocess start. Capped at `MAX_MEMORY_ENTRIES`.
- **memory/** — daily conversation logs (`YYYY-MM-DD.md`). User messages and agent responses are auto-logged with timestamps.
- **CRON.md** — scheduled recurring tasks (optional). Parsed on startup.
- **scripts/** — agent-specific scripts (optional)

## Scheduled Tasks (CRON.md)

Each agent can have a `CRON.md` to define recurring tasks:

```markdown
## Morning Briefing
- schedule: weekdays 09:00
- dm: U0ARFUDADUJ
- prompt: Prepare today's morning briefing
- post: always

## Health Check
- schedule: every 5m
- channel: C090L76SYLA
- prompt: Check system health. If everything is normal, respond with <nopost/>. Only report errors.
- post: conditional
```

**Schedule types:** `every 30m`, `every 2h`, `daily 09:00`, `weekdays 08:30`

**Target:** `channel: CHANNEL_ID` (post to channel) or `dm: USER_ID` (DM to user)

**Post modes:**
- `always` — always post (default)
- `conditional` — post unless agent returns `<nopost/>` (e.g. only report errors)
- `silent` — never post to Slack, log only

**Enable/disable:** Add `- enabled: false` to pause a task without removing it. Edit CRON.md while running — changes are picked up before each execution, no restart needed.

**Slack `/cron` command:** Manage tasks without editing files:
```
/cron list                                           — show all tasks
/cron show <name>                                    — task details
/cron add name | schedule | target | prompt [| post] — add task
/cron enable <name>                                  — resume
/cron disable <name>                                 — pause
/cron delete <name>                                  — remove
```
DM target: use `dm:USER_ID` (e.g. `/cron add Report | daily 18:00 | dm:U0AR... | Send report`)

## Slack App Setup

Each agent requires its own Slack App with Socket Mode enabled. See the **[detailed setup guide](docs/slack-app-setup.md)** with app manifest, scopes reference, and troubleshooting.

Quick summary:
1. Create a Slack App **from manifest** at [api.slack.com/apps](https://api.slack.com/apps)
2. Copy **Bot Token** (`xoxb-...`) from OAuth & Permissions
3. Generate **App-Level Token** (`xapp-...`) with `connections:write` scope
4. Add tokens to `.env`

Repeat for each agent.

## How It Works

- **Single process, multi-bot**: all bots run concurrently via `asyncio.gather`. Each bot is an independent `AgentBot` instance with its own Slack `AsyncApp` and Socket Mode connection.
- **Persistent CLI subprocess**: each agent keeps one `claude -p --input-format stream-json --output-format stream-json` process alive. Messages are sent as NDJSON via stdin, responses read from stdout. Cold start on first message (~5-6s), subsequent messages reuse the process (~2-3s). Restarts automatically if the process dies. Each subprocess runs in its agent workspace directory, isolating it from project-level `CLAUDE.md`.
- **Direct API**: if `ANTHROPIC_API_KEY` is set, uses Anthropic API directly (no CLI subprocess).
- **Chain reactions**: if a bot's response @mentions another bot, that bot picks up and responds.
- **Loop prevention**: per-thread response counter capped at `MAX_CHAIN_DEPTH`. Bots ignore their own messages. Stale threads pruned automatically.
- **Slack formatting**: responses are post-processed to convert markdown to Slack mrkdwn (`**bold**` → `*bold*`, `## Header` → `*Header*`, `[text](url)` → `<url|text>`). Hallucinated XML tags are stripped.
- **Memory**: `<memory>` tags in responses are extracted, saved to MEMORY.md, and stripped before posting to Slack. Daily conversation logs are written to `memory/YYYY-MM-DD.md`.
- **Cron scheduler**: `CRON.md` in agent workspace defines recurring tasks. Supports intervals (`every 30m`), fixed times (`daily 09:00`, `weekdays 08:30`), channel/DM targets, and conditional posting (`<nopost/>`).
- **Graceful shutdown**: SIGINT/SIGTERM cancels cron tasks, saves session state to daily log, and cleanly stops all Claude subprocesses.

## Running with Supervisor

```ini
[program:agent]
command=/usr/local/bin/uv run multi-agent
directory=/path/to/multi_agent
autostart=true
autorestart=false
stopasgroup=true
killasgroup=true
stderr_logfile=./logs/agent.err.log
stdout_logfile=./logs/agent.out.log
```

## Special Thanks

- Dylan Ko (고영혁) — general idea and inspiration
- [juchanhwang/my-harness](https://github.com/juchanhwang/my-harness) — agent persona definitions
