#!/usr/bin/env python3
"""
Multi-Agent Slack Bot runner script.
Loads .env file then runs multi_agent.py.

Usage:
  python run.py
  # or via uv:
  uv run multi-agent
"""

import sys
import asyncio
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:
    print("python-dotenv is required: pip install python-dotenv")
    sys.exit(1)


def main():
    # Load .env
    env_path = Path(__file__).parent / ".env"
    if env_path.exists():
        load_dotenv(env_path)
        print(f".env loaded: {env_path}")
    else:
        print("Warning: .env file not found. Make sure environment variables are set manually.")

    import multi_agent
    asyncio.run(multi_agent.main())


if __name__ == "__main__":
    main()
