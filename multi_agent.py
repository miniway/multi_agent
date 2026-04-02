"""
Multi-Agent Slack Bot System
============================
A lightweight system where Slack bots converse with each other.

Each bot:
- Responds when @mentioned
- Calls Claude via CLI subprocess based on its role defined in SOUL.md
- Posts responses to Slack channel (may @mention other bots -> chain reaction)

Usage:
  1. Configure tokens in .env
  2. Place SOUL.md files in agents/ directory
  3. Run: uv run multi-agent
"""

import os
import re
import asyncio
import logging
import time
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional

from slack_bolt.async_app import AsyncApp
from slack_bolt.adapter.socket_mode.async_handler import AsyncSocketModeHandler
from slack_sdk.web.async_client import AsyncWebClient
import json as _json

import anthropic
from claude_agent_sdk import query, ClaudeAgentOptions, AssistantMessage, TextBlock

# ============================================================
# Logging
# ============================================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)-12s] %(levelname)-7s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("multi-agent")

# ============================================================
# Configuration
# ============================================================
MODEL = os.environ.get("CLAUDE_MODEL", "claude-sonnet-4-20250514")
MAX_TOKENS = int(os.environ.get("MAX_TOKENS", "4096"))
AGENTS_DIR = Path(os.environ.get("AGENTS_DIR", "./agents"))

# Loop prevention
MAX_CHAIN_DEPTH = int(os.environ.get("MAX_CHAIN_DEPTH", "10"))  # Max bot responses per thread
COOLDOWN_SECONDS = float(os.environ.get("COOLDOWN_SECONDS", "2.0"))  # Delay between responses

# ============================================================
# Agent Definition
# ============================================================
@dataclass
class AgentConfig:
    """One Slack Bot = One Agent"""
    name: str                    # Display name (e.g. "FRIDAY")
    bot_token: str               # xoxb-...
    app_token: str               # xapp-...
    soul_path: Path              # Path to SOUL.md
    bot_user_id: str = ""        # Set at runtime
    soul: str = ""               # SOUL.md content (loaded at runtime)
    conversation_history: dict = field(default_factory=dict)  # channel_id -> messages


def load_agents() -> list[AgentConfig]:
    """
    Load agent configurations from environment variables.

    Env var pattern:
      AGENT_FRIDAY_BOT_TOKEN=xoxb-...
      AGENT_FRIDAY_APP_TOKEN=xapp-...
      AGENT_FRIDAY_SOUL=./agents/friday/SOUL.md

      AGENT_BLACK_WIDOW_BOT_TOKEN=xoxb-...
      AGENT_BLACK_WIDOW_APP_TOKEN=xapp-...
      AGENT_BLACK_WIDOW_SOUL=./agents/black-widow/SOUL.md
    """
    agents = []

    # Find AGENT_*_BOT_TOKEN patterns in env vars
    agent_names = set()
    for key in os.environ:
        match = re.match(r"AGENT_(.+)_BOT_TOKEN", key)
        if match:
            agent_names.add(match.group(1))

    for name_key in sorted(agent_names):
        bot_token = os.environ.get(f"AGENT_{name_key}_BOT_TOKEN", "")
        app_token = os.environ.get(f"AGENT_{name_key}_APP_TOKEN", "")
        soul_file = os.environ.get(
            f"AGENT_{name_key}_SOUL",
            str(AGENTS_DIR / name_key.lower().replace("_", "-") / "SOUL.md")
        )
        display_name = os.environ.get(
            f"AGENT_{name_key}_NAME",
            name_key.replace("_", " ").title()
        )

        if not bot_token or not app_token:
            logger.warning(f"Agent {name_key}: missing tokens, skipping")
            continue

        soul_path = Path(soul_file)
        soul_content = ""
        if soul_path.exists():
            soul_content = soul_path.read_text(encoding="utf-8")
            logger.info(f"Agent {display_name}: SOUL.md loaded ({len(soul_content)} chars)")
        else:
            logger.warning(f"Agent {display_name}: SOUL.md not found ({soul_path})")

        agents.append(AgentConfig(
            name=display_name,
            bot_token=bot_token,
            app_token=app_token,
            soul_path=soul_path,
            soul=soul_content,
        ))

    return agents


# ============================================================
# Claude Backend (hybrid: Anthropic API or claude-agent-sdk)
# ============================================================
# ANTHROPIC_API_KEY → direct API (~2-5s, fast)
# CLAUDE_CODE_OAUTH_TOKEN → claude-agent-sdk subprocess (~6s with optimised CLI flags)
USE_DIRECT_API = bool(os.environ.get("ANTHROPIC_API_KEY"))

if USE_DIRECT_API:
    _anthropic_client = anthropic.AsyncAnthropic()
else:
    _anthropic_client = None

CLAUDE_CLI = os.environ.get("CLAUDE_CLI", "claude")
_FAST_CLI_FLAGS = [
    "--settings", '{"hooks":{}}',
    "--setting-sources", "",
    "--disable-slash-commands",
]


def _format_prompt(messages: list[dict]) -> str:
    """Format conversation history into a single prompt string for CLI mode."""
    parts = []
    for msg in messages[:-1]:
        role = "User" if msg["role"] == "user" else "Assistant"
        parts.append(f"[{role}]: {msg['content']}")

    last_message = messages[-1]["content"] if messages else ""
    if parts:
        context = "\n".join(parts)
        return f"Previous conversation:\n{context}\n\nCurrent message:\n{last_message}"
    return last_message


async def _call_claude_api(system_prompt: str, messages: list[dict]) -> str:
    """Call Claude via Anthropic API directly (fast, requires ANTHROPIC_API_KEY)."""
    try:
        response = await _anthropic_client.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=system_prompt,
            messages=messages,
        )
        return "".join(
            block.text for block in response.content if block.type == "text"
        ) or "No response from Claude."
    except Exception as e:
        logger.error(f"Anthropic API error: {e}")
        return f"API call failed: {str(e)[:200]}"


async def _call_claude_sdk(system_prompt: str, messages: list[dict]) -> str:
    """Call Claude via claude-agent-sdk (uses CLAUDE_CODE_OAUTH_TOKEN).

    Spawns `claude -p` subprocess with hooks disabled for speed.
    """
    prompt = _format_prompt(messages)
    cmd = [
        CLAUDE_CLI, "-p",
        "--model", MODEL,
        "--system-prompt", system_prompt,
        "--output-format", "json",
        "--max-turns", "1",
        *_FAST_CLI_FLAGS,
        prompt,
    ]

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()

        if proc.returncode != 0:
            err = stderr.decode().strip()[:200]
            logger.error(f"Claude CLI error (exit {proc.returncode}): {err}")
            return f"CLI error: {err}"

        data = _json.loads(stdout.decode())
        return data.get("result", "") or "No response from Claude."
    except Exception as e:
        logger.error(f"Claude CLI error: {e}")
        return f"CLI call failed: {str(e)[:200]}"


async def call_claude(system_prompt: str, messages: list[dict]) -> str:
    """Call Claude using the best available backend.

    ANTHROPIC_API_KEY set → direct Anthropic API (~2-5s)
    Otherwise → claude CLI subprocess with OAuth (~6s)
    """
    if USE_DIRECT_API:
        return await _call_claude_api(system_prompt, messages)
    return await _call_claude_sdk(system_prompt, messages)


# ============================================================
# Slack Bot Runner (per agent)
# ============================================================
class AgentBot:
    """A single Slack Bot instance"""

    def __init__(self, config: AgentConfig, all_agents: list[AgentConfig]):
        self.config = config
        self.all_agents = all_agents  # Other bots info (for mention mapping)

        self.app = AsyncApp(token=config.bot_token)
        self.handler: Optional[AsyncSocketModeHandler] = None

        # Per-thread bot response counter (loop prevention)
        self._thread_counter: dict[str, int] = {}

        self._register_handlers()

    def _register_handlers(self):
        """Register Slack event handlers"""

        @self.app.event("app_mention")
        async def handle_mention(event, say, client):
            await self._handle_message(event, say, client)

        @self.app.event("message")
        async def handle_message(event, say, client):
            # Only handle DMs (channel messages use app_mention)
            if event.get("channel_type") == "im":
                await self._handle_message(event, say, client)

        @self.app.event("reaction_added")
        async def handle_reaction_added(event, logger):
            pass  # Acknowledge but ignore

        @self.app.event("reaction_removed")
        async def handle_reaction_removed(event, logger):
            pass  # Acknowledge but ignore

    async def _handle_message(self, event: dict, say, client: AsyncWebClient):
        """Main message handling logic"""

        text = event.get("text", "")
        user = event.get("user", "")
        channel = event.get("channel", "")
        thread_ts = event.get("thread_ts", event.get("ts", ""))

        # Ignore own messages
        if user == self.config.bot_user_id:
            return

        # Ignore own bot_message subtype (allow other bots)
        if event.get("bot_id") and event.get("bot_id") == await self._get_own_bot_id(client):
            return

        # Loop prevention: limit responses per thread
        counter_key = f"{channel}:{thread_ts}"
        self._thread_counter[counter_key] = self._thread_counter.get(counter_key, 0) + 1
        if self._thread_counter[counter_key] > MAX_CHAIN_DEPTH:
            logger.warning(f"[{self.config.name}] Thread {counter_key} max depth reached, ignoring")
            return

        t0 = time.monotonic()

        # Typing indicator (non-blocking)
        asyncio.create_task(
            client.reactions_add(channel=channel, name="eyes", timestamp=event["ts"])
        )

        # Resolve @mentions to display names
        readable_text = await self._resolve_mentions(text, client)
        t1 = time.monotonic()

        # Build conversation history
        history_key = f"{channel}:{thread_ts}"
        if history_key not in self.config.conversation_history:
            self.config.conversation_history[history_key] = []

        history = self.config.conversation_history[history_key]
        history.append({"role": "user", "content": readable_text})

        # Keep only last 20 messages
        if len(history) > 20:
            history = history[-20:]
            self.config.conversation_history[history_key] = history

        # Build system prompt
        system_prompt = self._build_system_prompt()

        # Call Claude via agent SDK
        logger.info(f"[{self.config.name}] Calling Claude: {readable_text[:80]}...")
        t2 = time.monotonic()
        response_text = await call_claude(system_prompt, history)
        t3 = time.monotonic()

        # Append response to history
        history.append({"role": "assistant", "content": response_text})

        # Send response to Slack (directly in channel, not as thread reply)
        await say(
            text=response_text,
            channel=channel,
        )
        t4 = time.monotonic()

        # Remove typing reaction (non-blocking)
        asyncio.create_task(
            client.reactions_remove(channel=channel, name="eyes", timestamp=event["ts"])
        )

        logger.info(
            f"[{self.config.name}] Timing: mention_resolve={t1-t0:.1f}s, "
            f"claude={t3-t2:.1f}s, slack_post={t4-t3:.1f}s, total={t4-t0:.1f}s"
        )

        logger.info(f"[{self.config.name}] Response sent ({len(response_text)} chars)")

    def _build_system_prompt(self) -> str:
        """Build system prompt from SOUL.md + team context"""

        # Other agents info
        team_info = "\n".join(
            f"- @{a.name}: {a.soul[:200].split(chr(10))[0] if a.soul else '(role undefined)'}"
            for a in self.all_agents
            if a.name != self.config.name
        )

        return f"""{self.config.soul}

---
## Team Members (communicate via @mention on Slack)
{team_info}

## System Rules
- When requesting work from another agent, always use @AgentName format.
- Do not @mention yourself.
- Escalate to Boss after {MAX_CHAIN_DEPTH}+ round-trips in a thread.
"""

    async def _resolve_mentions(self, text: str, client: AsyncWebClient) -> str:
        """Resolve <@U12345> mentions to display names"""
        mentions = re.findall(r"<@(U[A-Z0-9]+)>", text)
        for user_id in mentions:
            # Look up in our agents first
            agent_name = None
            for a in self.all_agents:
                if a.bot_user_id == user_id:
                    agent_name = a.name
                    break

            if not agent_name:
                try:
                    result = await client.users_info(user=user_id)
                    agent_name = result["user"]["real_name"] or result["user"]["name"]
                except Exception:
                    agent_name = user_id

            text = text.replace(f"<@{user_id}>", f"@{agent_name}")

        return text

    async def _get_own_bot_id(self, client: AsyncWebClient) -> Optional[str]:
        """Get own bot_id (cached)"""
        if not hasattr(self, "_own_bot_id"):
            try:
                result = await client.auth_test()
                self._own_bot_id = result.get("bot_id", "")
            except Exception:
                self._own_bot_id = ""
        return self._own_bot_id

    async def start(self):
        """Start bot via Socket Mode"""
        client = AsyncWebClient(token=self.config.bot_token)
        try:
            auth = await client.auth_test()
            self.config.bot_user_id = auth["user_id"]
            logger.info(f"[{self.config.name}] Connected (user_id: {self.config.bot_user_id})")
        except Exception as e:
            logger.error(f"[{self.config.name}] Auth failed: {e}")
            return

        self.handler = AsyncSocketModeHandler(self.app, self.config.app_token)
        await self.handler.start_async()


# ============================================================
# Main
# ============================================================
async def main():
    logger.info("=" * 60)
    logger.info("Multi-Agent Slack Bot System")
    logger.info("=" * 60)

    # Validate Claude credentials (claude-agent-sdk accepts either)
    if not os.environ.get("ANTHROPIC_API_KEY") and not os.environ.get("CLAUDE_CODE_OAUTH_TOKEN"):
        logger.error("Set ANTHROPIC_API_KEY or CLAUDE_CODE_OAUTH_TOKEN environment variable")
        return

    if USE_DIRECT_API:
        logger.info("Claude backend: Anthropic API (fast, direct)")
    else:
        logger.info("Claude backend: Claude CLI subprocess (OAuth)")

    # Load agents
    agents = load_agents()
    if not agents:
        logger.error("No agents found. Check your environment variables.")
        logger.info("Required env var pattern:")
        logger.info("  AGENT_FRIDAY_BOT_TOKEN=xoxb-...")
        logger.info("  AGENT_FRIDAY_APP_TOKEN=xapp-...")
        logger.info("  AGENT_FRIDAY_SOUL=./agents/friday/SOUL.md")
        return

    logger.info(f"Agents loaded: {len(agents)}")
    for a in agents:
        logger.info(f"  - {a.name} (SOUL: {'OK' if a.soul else 'MISSING'})")

    # Create bot instances
    bots = [AgentBot(config=agent, all_agents=agents) for agent in agents]

    # Start all bots concurrently
    logger.info("Starting all bots...")
    await asyncio.gather(*[bot.start() for bot in bots])


if __name__ == "__main__":
    asyncio.run(main())
