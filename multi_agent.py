"""
Multi-Agent Slack Bot System
============================
A lightweight system where Slack bots converse with each other.

Each bot:
- Responds when @mentioned
- Calls Claude via persistent CLI subprocess or direct API
- Posts responses to Slack channel (may @mention other bots -> chain reaction)
- Maintains persistent workspace (MEMORY.md, TOOLS.md, daily logs)

Usage:
  1. Configure tokens in .env
  2. Place SOUL.md files in agents/ directory
  3. Run: uv run multi-agent
"""

import os
import re
import asyncio
import logging
import signal
import time
from datetime import date
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional

from slack_bolt.async_app import AsyncApp
from slack_bolt.adapter.socket_mode.async_handler import AsyncSocketModeHandler
from slack_sdk.web.async_client import AsyncWebClient
import json as _json

import anthropic

# ============================================================
# Logging
# ============================================================
LOG_DIR = Path(os.environ.get("LOG_DIR", "./logs"))
LOG_DIR.mkdir(parents=True, exist_ok=True)

_log_format = "%(asctime)s [%(name)-12s] %(levelname)-7s %(message)s"
_log_datefmt = "%H:%M:%S"

logging.basicConfig(
    level=logging.INFO,
    format=_log_format,
    datefmt=_log_datefmt,
    handlers=[
        logging.StreamHandler(),  # stderr (console)
        logging.FileHandler(LOG_DIR / "agent.log", encoding="utf-8"),  # file
    ],
)
logger = logging.getLogger("multi-agent")

# ============================================================
# Configuration
# ============================================================
MODEL = os.environ.get("CLAUDE_MODEL", "claude-sonnet-4-20250514")
MAX_TOKENS = int(os.environ.get("MAX_TOKENS", "4096"))
AGENTS_DIR = Path(os.environ.get("AGENTS_DIR", "./agents"))
MAX_MEMORY_ENTRIES = int(os.environ.get("MAX_MEMORY_ENTRIES", "50"))
CLI_TIMEOUT = int(os.environ.get("CLI_TIMEOUT", "300"))  # seconds

# Loop prevention
MAX_CHAIN_DEPTH = int(os.environ.get("MAX_CHAIN_DEPTH", "10"))

# Memory tag pattern
_MEMORY_TAG_RE = re.compile(r"<memory>(.*?)</memory>", re.DOTALL)
# XML junk patterns (hallucinated tool calls, etc.)
_XML_JUNK_RE = re.compile(
    r"<(?:tool_use|tool_name|tool_parameters|tool_function_result|function_call|result)>.*?"
    r"</(?:tool_use|tool_name|tool_parameters|tool_function_result|function_call|result)>",
    re.DOTALL,
)


# ============================================================
# Agent Workspace
# ============================================================
def _ensure_workspace(agent_dir: Path) -> None:
    """Ensure agent workspace directories and files exist."""
    (agent_dir / "memory").mkdir(parents=True, exist_ok=True)

    for fname in ("MEMORY.md", "TOOLS.md"):
        fpath = agent_dir / fname
        if not fpath.exists():
            fpath.write_text("", encoding="utf-8")


def _load_file(path: Path) -> str:
    """Load file content, return empty string if missing."""
    if path.exists():
        return path.read_text(encoding="utf-8").strip()
    return ""


def _append_memory(agent_dir: Path, entry: str) -> None:
    """Append an entry to MEMORY.md, keeping max entries."""
    memory_file = agent_dir / "MEMORY.md"
    timestamp = time.strftime("%Y-%m-%d %H:%M")

    existing = _load_file(memory_file)
    lines = [l for l in existing.split("\n") if l.strip()] if existing else []
    lines.append(f"- [{timestamp}] {entry.strip()}")

    if len(lines) > MAX_MEMORY_ENTRIES:
        lines = lines[-MAX_MEMORY_ENTRIES:]

    memory_file.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _append_daily_log(agent_dir: Path, role: str, content: str) -> None:
    """Append to today's daily log file."""
    today = date.today().isoformat()
    log_file = agent_dir / "memory" / f"{today}.md"

    timestamp = time.strftime("%H:%M")
    entry = f"**[{timestamp}] {role}**: {content[:500]}\n\n"

    with open(log_file, "a", encoding="utf-8") as f:
        f.write(entry)


# ============================================================
# Cron Scheduler
# ============================================================
from datetime import datetime, timedelta

# <nopost/> tag — agent includes this to suppress Slack posting
_NOPOST_TAG_RE = re.compile(r"<nopost\s*/?>", re.DOTALL)


@dataclass
class CronTask:
    """A scheduled recurring task from CRON.md.

    Schedule types:
      - every 30m / every 2h / every 1d  (interval)
      - daily 09:00                       (fixed time, every day)
      - weekdays 09:00                    (Mon-Fri only)

    Target:
      - channel: CHANNEL_ID  (post to channel)
      - dm: USER_ID          (post as DM)

    Post mode:
      - always      (default: always post)
      - conditional (post unless agent returns <nopost/>)
      - silent      (never post, log only)
    """
    name: str
    schedule: str           # raw schedule string
    prompt: str
    channel: str = ""       # Slack channel ID
    dm: str = ""            # Slack user ID for DM
    post: str = "always"    # always | conditional | silent


def _parse_interval(spec: str) -> int:
    """Parse interval string like '30m', '2h', '1d' to seconds."""
    spec = spec.strip().lower()
    m = re.match(r"^(\d+)\s*(s|m|h|d)$", spec)
    if not m:
        raise ValueError(f"Invalid interval: {spec}")
    val, unit = int(m.group(1)), m.group(2)
    return val * {"s": 1, "m": 60, "h": 3600, "d": 86400}[unit]


def _seconds_until(hour: int, minute: int, weekdays_only: bool = False) -> int:
    """Seconds until the next occurrence of HH:MM (local time)."""
    now = datetime.now()
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    if weekdays_only:
        while target.weekday() >= 5:  # 5=Sat, 6=Sun
            target += timedelta(days=1)
    return int((target - now).total_seconds())


def _parse_cron_md(path: Path) -> list[CronTask]:
    """Parse CRON.md into a list of CronTask.

    Format:
        ## Task Name
        - schedule: every 30m | daily 09:00 | weekdays 09:00
        - channel: C090L76SYLA   (or dm: U12345)
        - prompt: What to do
        - post: always | conditional | silent
    """
    content = _load_file(path)
    if not content:
        return []

    tasks = []
    current_name = None
    current = {}

    def _save_current():
        if current_name and "schedule" in current and "prompt" in current:
            tasks.append(CronTask(
                name=current_name,
                schedule=current["schedule"],
                prompt=current["prompt"],
                channel=current.get("channel", ""),
                dm=current.get("dm", ""),
                post=current.get("post", "always"),
            ))

    for line in content.split("\n"):
        line = line.strip()
        if line.startswith("## "):
            _save_current()
            current_name = line[3:].strip()
            current = {}
        elif line.startswith("- ") and ":" in line:
            key, _, val = line[2:].partition(":")
            current[key.strip().lower()] = val.strip()

    _save_current()
    return tasks


def _extract_and_strip_memory(text: str) -> tuple[str, list[str]]:
    """Extract <memory> tags from response. Returns (cleaned_text, memory_entries)."""
    entries = _MEMORY_TAG_RE.findall(text)
    cleaned = _MEMORY_TAG_RE.sub("", text).strip()
    return cleaned, entries


def _sanitize_for_slack(text: str) -> str:
    """Remove hallucinated XML tags and convert to Slack mrkdwn."""
    text = _XML_JUNK_RE.sub("", text)
    # **bold** → *bold* (Slack mrkdwn)
    text = re.sub(r"\*\*(.+?)\*\*", r"*\1*", text)
    # ### Header → *Header* (Slack has no headers)
    text = re.sub(r"^#{1,6}\s+(.+)$", r"*\1*", text, flags=re.MULTILINE)
    # [text](url) → <url|text> (Slack link format)
    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"<\2|\1>", text)
    # Collapse multiple blank lines
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


# ============================================================
# Agent Definition
# ============================================================
@dataclass
class AgentConfig:
    """One Slack Bot = One Agent"""
    name: str
    bot_token: str
    app_token: str
    agent_dir: Path
    bot_user_id: str = ""
    soul: str = ""
    tools: str = ""
    conversation_history: dict = field(default_factory=dict)


def load_agents() -> list[AgentConfig]:
    """Load agent configurations from environment variables."""
    agents = []

    agent_names = set()
    for key in os.environ:
        match = re.match(r"AGENT_(.+)_BOT_TOKEN", key)
        if match:
            agent_names.add(match.group(1))

    for name_key in sorted(agent_names):
        enabled = os.environ.get(f"AGENT_{name_key}_ENABLED", "true").lower()
        if enabled in ("false", "0", "no", "off"):
            logger.info(f"Agent {name_key}: disabled, skipping")
            continue

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
        agent_dir = soul_path.parent

        _ensure_workspace(agent_dir)

        soul_content = _load_file(soul_path)
        tools_content = _load_file(agent_dir / "TOOLS.md")

        if soul_content:
            logger.info(f"Agent {display_name}: SOUL.md loaded ({len(soul_content)} chars)")
        else:
            logger.warning(f"Agent {display_name}: SOUL.md not found ({soul_path})")

        if tools_content:
            logger.info(f"Agent {display_name}: TOOLS.md loaded ({len(tools_content)} chars)")

        agents.append(AgentConfig(
            name=display_name,
            bot_token=bot_token,
            app_token=app_token,
            agent_dir=agent_dir,
            soul=soul_content,
            tools=tools_content,
        ))

    return agents


# ============================================================
# Claude Backend
# ============================================================
USE_DIRECT_API = bool(os.environ.get("ANTHROPIC_API_KEY"))

if USE_DIRECT_API:
    _anthropic_client = anthropic.AsyncAnthropic()
else:
    _anthropic_client = None

CLAUDE_CLI = os.environ.get("CLAUDE_CLI", "claude")
_ALLOWED_TOOLS_RAW = os.environ.get("ALLOWED_TOOLS", "WebSearch,WebFetch,Read")
_PERMISSION_MODE = os.environ.get("PERMISSION_MODE", "default")

_FAST_CLI_FLAGS = [
    "--settings", '{"hooks":{}}',
    "--disable-slash-commands",
    "--permission-mode", _PERMISSION_MODE,
]

if _ALLOWED_TOOLS_RAW.strip().lower() != "default":
    _ALLOWED_TOOLS = [t.strip() for t in _ALLOWED_TOOLS_RAW.split(",") if t.strip()]
    _FAST_CLI_FLAGS.extend(["--tools", ",".join(_ALLOWED_TOOLS)])
    _FAST_CLI_FLAGS.extend(["--allowedTools", *_ALLOWED_TOOLS])


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


# ============================================================
# Persistent Claude CLI Subprocess (stream-json protocol)
# ============================================================
class PersistentClaude:
    """Persistent Claude CLI subprocess per agent.

    Uses `-p --input-format stream-json --output-format stream-json` to keep
    one process alive. Messages are sent as NDJSON via stdin, responses read
    as stream-json events from stdout. Restarts when system prompt changes
    (e.g. MEMORY.md updated) or process dies.
    """

    def __init__(self, name: str):
        self.name = name
        self.proc: Optional[asyncio.subprocess.Process] = None
        self._lock = asyncio.Lock()
        self._stderr_task: Optional[asyncio.Task] = None

    async def _start(self, system_prompt: str) -> None:
        await self._stop()

        cmd = [
            CLAUDE_CLI, "-p",
            "--input-format", "stream-json",
            "--output-format", "stream-json",
            "--verbose",
            "--model", MODEL,
            "--system-prompt", system_prompt,
            "--max-turns", os.environ.get("MAX_TURNS", "10"),
            *_FAST_CLI_FLAGS,
        ]

        self.proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        self._stderr_task = asyncio.create_task(self._drain_stderr())
        logger.info(f"[{self.name}] Claude subprocess started (PID: {self.proc.pid})")
        logger.info(f"[{self.name}] CLI flags: {' '.join(cmd[1:])}")

    async def _stop(self) -> None:
        if self._stderr_task:
            self._stderr_task.cancel()
            self._stderr_task = None
        if self.proc and self.proc.returncode is None:
            self.proc.terminate()
            try:
                await asyncio.wait_for(self.proc.wait(), timeout=5)
            except asyncio.TimeoutError:
                self.proc.kill()
        self.proc = None

    async def _drain_stderr(self) -> None:
        try:
            while self.proc and self.proc.returncode is None:
                line = await self.proc.stderr.readline()
                if not line:
                    break
                msg = line.decode().strip()
                if msg:
                    logger.debug(f"[{self.name}] cli stderr: {msg}")
        except asyncio.CancelledError:
            pass

    def _is_alive(self) -> bool:
        return self.proc is not None and self.proc.returncode is None

    async def send(self, message: str, system_prompt: str) -> str:
        async with self._lock:
            if not self._is_alive():
                logger.info(f"[{self.name}] Starting subprocess")
                await self._start(system_prompt)

            # Send NDJSON user message
            msg = _json.dumps({
                "type": "user",
                "message": {"role": "user", "content": message},
            })
            self.proc.stdin.write((msg + "\n").encode())
            await self.proc.stdin.drain()

            # Read stream-json events until result
            try:
                while True:
                    raw = await asyncio.wait_for(
                        self.proc.stdout.readline(),
                        timeout=CLI_TIMEOUT,
                    )
                    if not raw:
                        logger.warning(f"[{self.name}] Subprocess stdout closed")
                        self.proc = None
                        return "Claude process exited unexpectedly"

                    line = raw.decode().strip()
                    if not line:
                        continue

                    try:
                        event = _json.loads(line)
                    except _json.JSONDecodeError:
                        continue

                    etype = event.get("type", "")

                    # Debug: log all stream events
                    if etype == "assistant":
                        # Too verbose — just log a summary
                        msg = event.get("message", {})
                        content = msg.get("content", [])
                        for block in content:
                            btype = block.get("type", "")
                            if btype == "tool_use":
                                logger.info(
                                    f"[{self.name}] tool_use: {block.get('name', '?')} "
                                    f"({str(block.get('input', ''))[:100]})"
                                )
                            elif btype == "text":
                                logger.debug(f"[{self.name}] text: {block.get('text', '')[:100]}")
                    elif etype not in ("system", "result"):
                        logger.debug(f"[{self.name}] event: {etype}")

                    if etype == "result":
                        result = event.get("result", "")
                        if event.get("is_error"):
                            logger.warning(
                                f"[{self.name}] Claude error: "
                                f"{event.get('subtype', 'unknown')}"
                            )
                        return result or "No response from Claude."

            except asyncio.TimeoutError:
                logger.error(
                    f"[{self.name}] Timed out after {CLI_TIMEOUT}s, restarting"
                )
                await self._stop()
                return f"Timed out after {CLI_TIMEOUT}s"


# ============================================================
# Slack Bot Runner (per agent)
# ============================================================
class AgentBot:
    """A single Slack Bot instance"""

    def __init__(self, config: AgentConfig, all_agents: list[AgentConfig]):
        self.config = config
        self.all_agents = all_agents

        self.app = AsyncApp(token=config.bot_token)
        self.handler: Optional[AsyncSocketModeHandler] = None

        # Persistent Claude subprocess (CLI mode only)
        self._claude: Optional[PersistentClaude] = None
        if not USE_DIRECT_API:
            self._claude = PersistentClaude(config.name)

        # Per-thread bot response counter (loop prevention)
        self._thread_counter: dict[str, int] = {}

        # Cache: team info string (doesn't change at runtime)
        self._team_info = "\n".join(
            f"- @{a.name}: {a.soul[:200].split(chr(10))[0] if a.soul else '(role undefined)'}"
            for a in self.all_agents
            if a.name != self.config.name
        )

        # Cache: resolved user display names
        self._user_name_cache: dict[str, str] = {}

        self._register_handlers()

    def _register_handlers(self):
        """Register Slack event handlers"""

        @self.app.event("app_mention")
        async def handle_mention(event, say, client):
            await self._handle_message(event, say, client)

        @self.app.event("message")
        async def handle_message(event, say, client):
            if event.get("channel_type") == "im":
                await self._handle_message(event, say, client)

        @self.app.event("reaction_added")
        async def handle_reaction_added(event, logger):
            pass

        @self.app.event("reaction_removed")
        async def handle_reaction_removed(event, logger):
            pass

    async def _call_claude(self, system_prompt: str, messages: list[dict]) -> str:
        """Call Claude using the best available backend."""
        if USE_DIRECT_API:
            return await _call_claude_api(system_prompt, messages)

        prompt = _format_prompt(messages)
        return await self._claude.send(prompt, system_prompt)

    async def _handle_message(self, event: dict, say, client: AsyncWebClient):
        """Main message handling logic"""

        text = event.get("text", "")
        user = event.get("user", "")
        channel = event.get("channel", "")
        thread_ts = event.get("thread_ts", event.get("ts", ""))

        if user == self.config.bot_user_id:
            return

        if event.get("bot_id") and event.get("bot_id") == await self._get_own_bot_id(client):
            return

        counter_key = f"{channel}:{thread_ts}"
        self._thread_counter[counter_key] = self._thread_counter.get(counter_key, 0) + 1
        if self._thread_counter[counter_key] > MAX_CHAIN_DEPTH:
            logger.warning(f"[{self.config.name}] Thread {counter_key} max depth reached, ignoring")
            return

        t0 = time.monotonic()

        asyncio.create_task(self._safe_reaction(client, "add", channel, event["ts"]))

        readable_text = await self._resolve_mentions(text, client)
        t1 = time.monotonic()

        _append_daily_log(self.config.agent_dir, "User", readable_text)

        history_key = f"{channel}:{thread_ts}"
        if history_key not in self.config.conversation_history:
            self.config.conversation_history[history_key] = []

        history = self.config.conversation_history[history_key]
        history.append({"role": "user", "content": readable_text})

        if len(history) > 20:
            history = history[-20:]
            self.config.conversation_history[history_key] = history

        system_prompt = self._build_system_prompt()

        logger.info(f"[{self.config.name}] Calling Claude: {readable_text[:80]}...")
        t2 = time.monotonic()
        response_text = await self._call_claude(system_prompt, history)
        t3 = time.monotonic()

        slack_text, memory_entries = _extract_and_strip_memory(response_text)
        slack_text = _sanitize_for_slack(slack_text)
        for entry in memory_entries:
            _append_memory(self.config.agent_dir, entry)
            logger.info(f"[{self.config.name}] Memory saved: {entry[:80]}")

        _append_daily_log(self.config.agent_dir, self.config.name, slack_text)

        history.append({"role": "assistant", "content": slack_text})

        await say(text=slack_text, channel=channel)
        t4 = time.monotonic()

        asyncio.create_task(self._safe_reaction(client, "remove", channel, event["ts"]))

        # Prune stale threads (keep last 100 active threads)
        if len(self._thread_counter) > 200:
            oldest = sorted(self._thread_counter)[:100]
            for k in oldest:
                self._thread_counter.pop(k, None)
                self.config.conversation_history.pop(k, None)

        logger.info(
            f"[{self.config.name}] Timing: mention_resolve={t1-t0:.1f}s, "
            f"claude={t3-t2:.1f}s, slack_post={t4-t3:.1f}s, total={t4-t0:.1f}s"
        )
        logger.info(f"[{self.config.name}] Response sent ({len(slack_text)} chars)")

    def _build_system_prompt(self) -> str:
        """Build system prompt from SOUL.md + TOOLS.md + MEMORY.md + team context"""

        memory_content = _load_file(self.config.agent_dir / "MEMORY.md")

        parts = [self.config.soul]

        if self.config.tools:
            parts.append(f"\n---\n## TOOLS.md\n{self.config.tools}")

        if memory_content:
            parts.append(f"\n---\n## Long-Term Memory\n{memory_content}")

        parts.append(f"""
---
## Team Members (communicate via @mention on Slack)
{self._team_info}

## System Rules
- When requesting work from another agent, always use @AgentName format.
- Do not @mention yourself.
- Escalate to Boss after {MAX_CHAIN_DEPTH}+ round-trips in a thread.

## Slack Formatting (MUST follow)
- You are responding in Slack. Use Slack mrkdwn, NOT standard Markdown.
- Bold: *bold* (single asterisk). NEVER use **double asterisk** — Slack does not render it as bold.
- Italic: _italic_ (underscore).
- Strikethrough: ~text~.
- Code inline: `code` (same as markdown).
- Code block: ```code``` (same as markdown).
- NO markdown headers (# ## ###). Use *bold text* on its own line instead.
- NO markdown links [text](url). Use bare URLs or Slack format <url|text>.
- Lists: use bullet • or dash -.
- Keep responses concise and conversational. This is chat, not a document.
- NEVER output raw XML tags in your response text. Tool calls are handled internally.
- You have full tool access: Bash, Read, Write, Edit, WebSearch, WebFetch, Glob, Grep, Browser, etc.
- You CAN and SHOULD execute shell commands via Bash tool. NEVER say "I can't run commands" or "직접 실행은 안 됩니다".
- When asked to run a script, run it immediately with Bash. Do not just show the command — execute it.
- When presenting tool results, summarize naturally — do not dump raw JSON or XML.

## Memory
- To remember something across conversations, wrap it in <memory>...</memory> tags in your response.
- The tagged content will be saved to your long-term memory and stripped before sending to Slack.
- Example: <memory>Boss prefers daily standups at 10am</memory>
- Only memorize important facts, decisions, preferences — not routine conversation.
""")

        return "\n".join(parts)

    async def _safe_reaction(self, client: AsyncWebClient, action: str, channel: str, ts: str):
        """Add/remove reaction, silently ignore errors."""
        try:
            if action == "add":
                await client.reactions_add(channel=channel, name="eyes", timestamp=ts)
            else:
                await client.reactions_remove(channel=channel, name="eyes", timestamp=ts)
        except Exception:
            pass

    async def _resolve_mentions(self, text: str, client: AsyncWebClient) -> str:
        """Resolve <@U12345> mentions to display names"""
        mentions = re.findall(r"<@(U[A-Z0-9]+)>", text)
        for user_id in mentions:
            if user_id in self._user_name_cache:
                name = self._user_name_cache[user_id]
            else:
                # Check agent list first
                name = None
                for a in self.all_agents:
                    if a.bot_user_id == user_id:
                        name = a.name
                        break
                if not name:
                    try:
                        result = await client.users_info(user=user_id)
                        name = result["user"]["real_name"] or result["user"]["name"]
                    except Exception:
                        name = user_id
                self._user_name_cache[user_id] = name

            text = text.replace(f"<@{user_id}>", f"@{name}")

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

        # Start cron tasks
        self._cron_tasks: list[asyncio.Task] = []
        cron_file = self.config.agent_dir / "CRON.md"
        cron_entries = _parse_cron_md(cron_file)
        if cron_entries:
            logger.info(f"[{self.config.name}] CRON: {len(cron_entries)} tasks loaded")
            for entry in cron_entries:
                task = asyncio.create_task(self._run_cron(entry, client))
                self._cron_tasks.append(task)
                target = entry.channel or entry.dm or "log-only"
                logger.info(
                    f"[{self.config.name}] CRON: '{entry.name}' ({entry.schedule})"
                    f" → {target} [post={entry.post}]"
                )

        self.handler = AsyncSocketModeHandler(self.app, self.config.app_token)
        await self.handler.start_async()

    async def _run_cron(self, task: CronTask, client: AsyncWebClient):
        """Run a single cron task on its schedule."""
        name = self.config.name
        schedule = task.schedule.strip().lower()

        # Initial delay to let the bot fully start
        await asyncio.sleep(10)
        logger.info(f"[{name}] CRON '{task.name}': started ({task.schedule})")

        while True:
            # Calculate sleep duration based on schedule type
            try:
                if schedule.startswith("every "):
                    sleep_secs = _parse_interval(schedule[6:])
                elif schedule.startswith("daily "):
                    h, m = map(int, schedule[6:].strip().split(":"))
                    sleep_secs = _seconds_until(h, m)
                elif schedule.startswith("weekdays "):
                    h, m = map(int, schedule[9:].strip().split(":"))
                    sleep_secs = _seconds_until(h, m, weekdays_only=True)
                else:
                    logger.error(f"[{name}] CRON '{task.name}': unknown schedule '{task.schedule}'")
                    return
            except (ValueError, IndexError) as e:
                logger.error(f"[{name}] CRON '{task.name}': bad schedule: {e}")
                return

            logger.info(f"[{name}] CRON '{task.name}': next run in {sleep_secs}s")
            await asyncio.sleep(sleep_secs)

            # Execute
            try:
                logger.info(f"[{name}] CRON '{task.name}': executing")
                system_prompt = self._build_system_prompt()
                response = await self._call_claude(
                    system_prompt,
                    [{"role": "user", "content": f"[CRON: {task.name}] {task.prompt}"}],
                )

                slack_text, memory_entries = _extract_and_strip_memory(response)
                slack_text = _sanitize_for_slack(slack_text)
                # Strip <nopost/> tag
                has_nopost = bool(_NOPOST_TAG_RE.search(slack_text))
                slack_text = _NOPOST_TAG_RE.sub("", slack_text).strip()

                for entry in memory_entries:
                    _append_memory(self.config.agent_dir, entry)

                _append_daily_log(self.config.agent_dir, f"CRON:{task.name}", slack_text)

                # Decide whether to post
                should_post = (
                    task.post == "always"
                    or (task.post == "conditional" and not has_nopost)
                )
                if not should_post or not slack_text or slack_text == "No response from Claude.":
                    logger.info(f"[{name}] CRON '{task.name}': skipped posting (post={task.post}, nopost={has_nopost})")
                    continue

                msg_text = f"*[{task.name}]*\n{slack_text}"

                # Post to channel or DM
                if task.dm:
                    # Open DM conversation and post
                    dm_resp = await client.conversations_open(users=[task.dm])
                    dm_channel = dm_resp["channel"]["id"]
                    await client.chat_postMessage(channel=dm_channel, text=msg_text)
                    logger.info(f"[{name}] CRON '{task.name}': DM sent to {task.dm}")
                elif task.channel:
                    await client.chat_postMessage(channel=task.channel, text=msg_text)
                    logger.info(f"[{name}] CRON '{task.name}': posted to #{task.channel}")

            except asyncio.CancelledError:
                logger.info(f"[{name}] CRON '{task.name}': cancelled")
                return
            except Exception as e:
                logger.error(f"[{name}] CRON '{task.name}': error: {e}")

    async def shutdown(self):
        """Graceful shutdown: save state and stop subprocess."""
        name = self.config.name
        agent_dir = self.config.agent_dir

        # Cancel cron tasks
        for task in getattr(self, "_cron_tasks", []):
            task.cancel()

        # Save conversation summary to daily log
        active_threads = len(self.config.conversation_history)
        total_msgs = sum(len(v) for v in self.config.conversation_history.values())
        if total_msgs > 0:
            _append_daily_log(
                agent_dir, "SYSTEM",
                f"Shutdown: {active_threads} threads, {total_msgs} messages in memory"
            )

        # Stop persistent Claude subprocess
        if self._claude:
            await self._claude._stop()
            logger.info(f"[{name}] Claude subprocess stopped")

        logger.info(f"[{name}] Shutdown complete")


# ============================================================
# Main
# ============================================================
async def main():
    logger.info("=" * 60)
    logger.info("Multi-Agent Slack Bot System")
    logger.info("=" * 60)

    if not os.environ.get("ANTHROPIC_API_KEY") and not os.environ.get("CLAUDE_CODE_OAUTH_TOKEN"):
        logger.error("Set ANTHROPIC_API_KEY or CLAUDE_CODE_OAUTH_TOKEN environment variable")
        return

    if USE_DIRECT_API:
        logger.info("Claude backend: Anthropic API (direct)")
    else:
        logger.info("Claude backend: Persistent CLI subprocess (OAuth)")

    agents = load_agents()
    if not agents:
        logger.error("No agents found. Check your environment variables.")
        logger.info("Required env var pattern:")
        logger.info("  AGENT_{NAME}_BOT_TOKEN=xoxb-...")
        logger.info("  AGENT_{NAME}_APP_TOKEN=xapp-...")
        logger.info("  AGENT_{NAME}_SOUL=./agents/{name}/SOUL.md")
        return

    logger.info(f"Agents loaded: {len(agents)}")
    for a in agents:
        logger.info(f"  - {a.name} (SOUL: {'OK' if a.soul else 'MISSING'}, TOOLS: {'OK' if a.tools else '-'})")

    bots = [AgentBot(config=agent, all_agents=agents) for agent in agents]

    # Graceful shutdown handler
    async def _shutdown(sig_name: str):
        logger.info(f"Received {sig_name}, shutting down...")
        await asyncio.gather(*[bot.shutdown() for bot in bots])
        logger.info("All agents stopped. Goodbye.")

    loop = asyncio.get_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(
            sig,
            lambda s=sig: asyncio.create_task(_shutdown(s.name)),
        )

    logger.info("Starting all bots...")
    await asyncio.gather(*[bot.start() for bot in bots])


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
