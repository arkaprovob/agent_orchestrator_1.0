#!/usr/bin/env python3
"""
Standalone runner for the Lightspeed Orchestrator.

Demonstrates:
  - Redis-backed session service for persistent, cross-surface sessions
  - Memory service for long-term user context
  - Programmatic agent invocation (alternative to `adk web`)

Usage:
    # With in-memory sessions (default, no Redis needed):
    python run_orchestrator.py

    # With Redis sessions:
    USE_REDIS=true python run_orchestrator.py

    # With a specific user/session (simulates cross-surface continuity):
    python run_orchestrator.py --user-id user_123 --session-id session_abc
"""

import asyncio
import argparse
import json
import logging
import os

from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.adk.memory import InMemoryMemoryService
from google.genai import types

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Session Service Factory
# ---------------------------------------------------------------------------

def create_session_service():
    """
    Create the appropriate session service based on configuration.

    Returns InMemorySessionService for development, or RedisSessionService
    when USE_REDIS=true is set.
    """
    use_redis = os.environ.get("USE_REDIS", "false").lower() == "true"

    if use_redis:
        try:
            # Try the community Redis session service
            from google_adk_extras.sessions import RedisSessionService

            redis_host = os.environ.get("REDIS_HOST", "localhost")
            redis_port = int(os.environ.get("REDIS_PORT", "6379"))

            logger.info(f"Using Redis session service at {redis_host}:{redis_port}")
            return RedisSessionService(host=redis_host, port=redis_port)
        except ImportError:
            try:
                # Fallback to adk-ext Redis service
                from adk.ext.sessions import RedisSessionService

                redis_host = os.environ.get("REDIS_HOST", "localhost")
                redis_port = int(os.environ.get("REDIS_PORT", "6379"))

                logger.info(f"Using adk-ext Redis session service at {redis_host}:{redis_port}")
                return RedisSessionService(host=redis_host, port=redis_port)
            except ImportError:
                logger.warning(
                    "Redis session service not available. "
                    "Install google-adk-extras or adk-ext for Redis support. "
                    "Falling back to in-memory sessions."
                )
                return InMemorySessionService()
    else:
        logger.info("Using in-memory session service (non-persistent)")
        return InMemorySessionService()


# ---------------------------------------------------------------------------
# Memory Service Factory
# ---------------------------------------------------------------------------

def create_memory_service():
    """
    Create the memory service for long-term user context.

    For the prototype, uses InMemoryMemoryService.
    Production would use VertexAiMemoryBankService or RedisMemoryService.
    """
    try:
        # Try Redis-backed memory if available
        use_redis = os.environ.get("USE_REDIS", "false").lower() == "true"
        if use_redis:
            from google_adk_extras.memory import RedisMemoryService
            redis_host = os.environ.get("REDIS_HOST", "localhost")
            redis_port = int(os.environ.get("REDIS_PORT", "6379"))
            logger.info(f"Using Redis memory service at {redis_host}:{redis_port}")
            return RedisMemoryService(host=redis_host, port=redis_port)
    except ImportError:
        pass

    logger.info("Using in-memory memory service")
    return InMemoryMemoryService()


# ---------------------------------------------------------------------------
# Interactive Chat Loop
# ---------------------------------------------------------------------------

async def run_interactive(user_id: str, session_id: str):
    """Run an interactive chat session with the orchestrator."""

    # Import the orchestrator agent
    from orchestrator.agent import root_agent
    from orchestrator.config import APP_NAME

    # Create services
    session_service = create_session_service()
    memory_service = create_memory_service()

    # Create or resume session
    session = await session_service.create_session(
        app_name=APP_NAME,
        user_id=user_id,
        session_id=session_id,
        state={
            "user_context": "New user session. No prior context.",
        },
    )
    logger.info(f"Session created: {session.id} for user {user_id}")

    # Create runner
    runner = Runner(
        agent=root_agent,
        app_name=APP_NAME,
        session_service=session_service,
    )

    print("\n" + "=" * 60)
    print("  Lightspeed Unified AI Assistant")
    print("  (type 'quit' to exit, 'context' to see session state)")
    print("=" * 60 + "\n")

    while True:
        try:
            user_input = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nGoodbye!")
            break

        if not user_input:
            continue
        if user_input.lower() == "quit":
            print("Goodbye!")
            break
        if user_input.lower() == "context":
            current_session = await session_service.get_session(
                app_name=APP_NAME,
                user_id=user_id,
                session_id=session_id,
            )
            if current_session:
                print("\n--- Session State ---")
                for key, value in current_session.state.items():
                    print(f"  {key}: {value[:200] if isinstance(value, str) else value}")
                print("---\n")
            continue

        # Send message to orchestrator
        content = types.Content(
            role="user",
            parts=[types.Part(text=user_input)],
        )

        print("\nLightspeed: ", end="", flush=True)

        final_response = ""
        events = runner.run_async(
            user_id=user_id,
            session_id=session_id,
            new_message=content,
        )

        async for event in events:
            if event.is_final_response() and event.content and event.content.parts:
                for part in event.content.parts:
                    if hasattr(part, "text") and part.text:
                        final_response = part.text

        if final_response:
            print(final_response)
        else:
            print("[No response generated]")

        # Save session to memory for long-term recall
        try:
            current_session = await session_service.get_session(
                app_name=APP_NAME,
                user_id=user_id,
                session_id=session_id,
            )
            if current_session:
                await memory_service.add_session_to_memory(current_session)
        except Exception as e:
            logger.debug(f"Memory save skipped: {e}")

        print()


# ---------------------------------------------------------------------------
# Entry Point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Lightspeed Orchestrator — Interactive Runner"
    )
    parser.add_argument(
        "--user-id",
        default="demo_user",
        help="User ID for session tracking (default: demo_user)",
    )
    parser.add_argument(
        "--session-id",
        default="demo_session",
        help="Session ID for continuity (default: demo_session)",
    )
    args = parser.parse_args()

    asyncio.run(run_interactive(args.user_id, args.session_id))


if __name__ == "__main__":
    main()
