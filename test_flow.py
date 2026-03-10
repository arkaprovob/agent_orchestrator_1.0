#!/usr/bin/env python3
"""
End-to-end test for the Lightspeed Orchestrator.

Runs 3 test scenarios:
  1. Single-domain: OCP-only question
  2. Cross-domain: OCP + OpenStack question
  3. General knowledge: handled by knowledge agent or orchestrator self-answer

Requires remote agents running on port 8001 (adk api_server --a2a remote_agents/).
"""

import asyncio
import logging
import os
import traceback

from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger("test_flow")


TEST_QUERIES = [
    {
        "name": "Single-domain (OCP)",
        "query": "How do I scale a deployment to 5 replicas in OpenShift?",
    },
    {
        "name": "Cross-domain (OCP + OpenStack)",
        "query": "Can I run Kubernetes workloads on my OpenStack infrastructure?",
    },
    {
        "name": "General knowledge (RHEL/Ansible)",
        "query": "What is the difference between RHEL 8 and RHEL 9?",
    },
]


async def run_test():
    from google.adk.runners import Runner
    from google.adk.sessions import InMemorySessionService
    from google.genai import types

    from orchestrator.agent import root_agent
    from orchestrator.config import APP_NAME

    session_service = InMemorySessionService()

    runner = Runner(
        agent=root_agent,
        app_name=APP_NAME,
        session_service=session_service,
    )

    for i, test in enumerate(TEST_QUERIES, 1):
        print(f"\n{'='*70}")
        print(f"  TEST {i}: {test['name']}")
        print(f"  Query: {test['query']}")
        print(f"{'='*70}\n")

        session = await session_service.create_session(
            app_name=APP_NAME,
            user_id="test_user",
        )

        content = types.Content(
            role="user",
            parts=[types.Part(text=test["query"])],
        )

        try:
            final_response = ""
            events = runner.run_async(
                user_id="test_user",
                session_id=session.id,
                new_message=content,
            )

            event_count = 0
            async for event in events:
                event_count += 1
                author = getattr(event, "author", "?")
                has_content = bool(
                    event.content and event.content.parts
                )
                is_final = event.is_final_response() if hasattr(event, "is_final_response") else False

                logger.info(
                    f"  Event #{event_count}: author={author}, "
                    f"has_content={has_content}, is_final={is_final}"
                )

                if has_content:
                    for part in event.content.parts:
                        if hasattr(part, "text") and part.text:
                            final_response = part.text

            updated_session = await session_service.get_session(
                app_name=APP_NAME,
                user_id="test_user",
                session_id=session.id,
            )

            plan = updated_session.state.get("execution_plan", "N/A")
            final = updated_session.state.get("final_response", final_response)

            print(f"\n--- Plan ---")
            print(plan[:500] if isinstance(plan, str) else str(plan)[:500])
            print(f"\n--- Final Response ---")
            response_text = final if final else final_response
            print(response_text[:800] if response_text else "[No response]")
            print(f"\n--- Stats ---")
            print(f"  Events: {event_count}")
            print(f"  Status: {'PASS' if response_text else 'FAIL'}")

        except Exception as e:
            print(f"\n--- ERROR ---")
            traceback.print_exc()
            print(f"  Status: FAIL ({type(e).__name__}: {e})")

    print(f"\n{'='*70}")
    print("  ALL TESTS COMPLETE")
    print(f"{'='*70}\n")


if __name__ == "__main__":
    asyncio.run(run_test())
