# Slack App Setup Guide

Step-by-step guide to create a Slack bot for the Multi-Agent system.

## 1. Create a New Slack App

1. Go to [api.slack.com/apps](https://api.slack.com/apps)
2. Click **Create New App**
3. Choose **From a manifest**
4. Select your workspace
5. Paste the manifest below (switch to JSON tab):

```json
{
    "display_information": {
        "name": "YOUR_AGENT_NAME",
        "description": "Agent description",
        "background_color": "#590088"
    },
    "features": {
        "bot_user": {
            "display_name": "YOUR_AGENT_NAME",
            "always_online": false
        },
        "slash_commands": [
            {
                "command": "/cron",
                "description": "Manage scheduled tasks (list, add, enable, disable, delete)",
                "usage_hint": "list | add name | schedule | target | prompt | show <name> | enable <name> | disable <name> | delete <name>",
                "should_escape": false
            }
        ]
    },
    "oauth_config": {
        "scopes": {
            "bot": [
                "chat:write",
                "app_mentions:read",
                "channels:history",
                "channels:read",
                "groups:history",
                "groups:read",
                "im:history",
                "mpim:history",
                "mpim:read",
                "pins:read",
                "reactions:read",
                "commands",
                "emoji:read",
                "files:read",
                "files:write",
                "groups:write",
                "im:write",
                "pins:write",
                "reactions:write",
                "users:read"
            ]
        }
    },
    "settings": {
        "event_subscriptions": {
            "bot_events": [
                "app_mention",
                "channel_rename",
                "member_joined_channel",
                "member_left_channel",
                "message.channels",
                "message.groups",
                "message.im",
                "message.mpim",
                "pin_added",
                "pin_removed",
                "reaction_added",
                "reaction_removed"
            ]
        },
        "interactivity": {
            "is_enabled": true
        },
        "org_deploy_enabled": false,
        "socket_mode_enabled": true,
        "token_rotation_enabled": false
    }
}
```

6. Replace `YOUR_AGENT_NAME` with your agent's name (e.g., `FRIDAY`,`Hulk`)
7. Click **Create**

## 2. Get the Bot Token

1. Go to **OAuth & Permissions** in the sidebar
2. Click **Install to Workspace** and authorize
3. Copy the **Bot User OAuth Token** (`xoxb-...`)

## 3. Get the App-Level Token

1. Go to **Basic Information** in the sidebar
2. Scroll down to **App-Level Tokens**
3. Click **Generate Token and Scopes**
4. Name it anything (e.g., `socket-mode`)
5. Add scope: `connections:write`
6. Click **Generate**
7. Copy the token (`xapp-...`)

## 4. Configure in .env

Add the tokens to your `.env` file:

```bash
# Replace {NAME} with your agent identifier (e.g., FRIDAY, HULK)
AGENT_{NAME}_ENABLED=true
AGENT_{NAME}_NAME=Your Agent Display Name
AGENT_{NAME}_BOT_TOKEN=xoxb-your-bot-token
AGENT_{NAME}_APP_TOKEN=xapp-your-app-token
AGENT_{NAME}_SOUL=./agents/your-agent/SOUL.md
```

## 5. Create Agent Workspace

```bash
mkdir -p agents/your-agent
```

Create `agents/your-agent/SOUL.md` with the agent's persona. See [README.md](../README.md#adding-an-agent) for details.

## 6. Invite the Bot

In Slack, invite the bot to channels where it should be active:

```
/invite @YOUR_AGENT_NAME
```

## 7. Start

```bash
uv run multi-agent
# or
supervisorctl start agent
```

The bot responds when @mentioned in a channel or via direct message.

## Bot Scopes Reference

| Scope | Purpose |
|-------|---------|
| `app_mentions:read` | Respond when @mentioned |
| `chat:write` | Post messages |
| `channels:history` / `groups:history` | Read channel messages for context |
| `channels:read` / `groups:read` | List channels |
| `im:history` / `im:write` | Direct messages |
| `mpim:history` / `mpim:read` | Group DMs |
| `reactions:read` / `reactions:write` | Typing indicator (eyes emoji) |
| `users:read` | Resolve @mentions to display names |
| `files:read` / `files:write` | File handling |
| `pins:read` / `pins:write` | Pin messages |
| `emoji:read` | Custom emoji support |
| `commands` | Slash commands (future) |

## Event Subscriptions

| Event | Purpose |
|-------|---------|
| `app_mention` | Core: respond when @mentioned |
| `message.im` | Core: respond to direct messages |
| `message.channels` / `message.groups` / `message.mpim` | Read channel messages |
| `reaction_added` / `reaction_removed` | Track reactions |
| `member_joined_channel` / `member_left_channel` | Track membership changes |
| `channel_rename` | Track channel renames |
| `pin_added` / `pin_removed` | Track pins |

## Repeat for Each Agent

Each agent needs its own Slack App. Repeat steps 1-6 for every agent you want to add to the system.

## Troubleshooting

**Bot doesn't respond:**
- Check the bot is invited to the channel (`/invite @BOT_NAME`)
- Verify `AGENT_{NAME}_ENABLED=true` in `.env`
- Check `logs/agent.log` for errors

**"not_authed" error:**
- Verify `BOT_TOKEN` and `APP_TOKEN` are correct
- Re-install the app to your workspace

**Socket Mode errors:**
- Ensure Socket Mode is enabled in the app settings
- Verify the App-Level Token has `connections:write` scope
