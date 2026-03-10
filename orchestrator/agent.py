"""
Lightspeed Unified Orchestrator Agent.

ARCHITECTURE: Plan-First, Discover-Then-Execute

  1. RECEIVE   -> Extract query, load session context & memory
  2. DISCOVER  -> Poll registry for currently available agents (A2A)
  3. PLAN-LOOP -> Iteratively plan, match, and refine until all steps resolve
                 or MAX_PLANNING_ITERATIONS is reached:
                   a. Planner creates execution plan from query + agent catalog
                   b. Match plan steps against registry (exact -> fuzzy)
                   c. If unresolved steps remain, PlanRefiner adjusts the plan
                   d. Re-match and re-validate; loop until satisfied
  4. EXECUTE   -> Run the validated plan: single / sequential / parallel / hybrid
  5. ASSEMBLE  -> Combine multi-agent outputs into unified response
  6. PERSIST   -> Update session context for cross-surface continuity

KEY PRINCIPLE:
  The orchestrator has ZERO knowledge of which agents exist at code time.
  Everything is discovered dynamically from the registry. The Planner
  receives the agent catalog at runtime and creates plans accordingly.

ADK Primitives Used:
  - BaseAgent (Custom) -> orchestrator control flow with conditional branching
  - LlmAgent -> planning, plan refinement, response assembly, self-answering
  - RemoteA2aAgent -> A2A protocol communication (created dynamically by registry)
  - Session state -> cross-step data passing and context persistence
"""

import json
import logging
import asyncio
from typing import AsyncGenerator

from typing_extensions import override

from google.genai import types
from google.adk.agents import LlmAgent, BaseAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event

from .config import (
    LLM_MODEL,
    AGENT_DISCOVERY_URLS,
    DISCOVERY_POLL_INTERVAL,
    MAX_PLANNING_ITERATIONS,
)
from .registry import AgentRegistry


logger = logging.getLogger("google_adk." + __name__)


# =============================================================================
# Registry — the SINGLE SOURCE OF TRUTH for available agents
# =============================================================================

registry = AgentRegistry(
    discovery_urls=AGENT_DISCOVERY_URLS,
    poll_interval=DISCOVERY_POLL_INTERVAL,
)


# =============================================================================
# Internal LLM Sub-Agents
#
# NOTE: These agents' instructions use {placeholders} that are resolved
# from session state at runtime. The agent catalog, user query, etc. are
# NEVER hardcoded — they are injected into state before each agent runs.
# =============================================================================

planner = LlmAgent(
    name="Planner",
    model=LLM_MODEL,
    include_contents="none",
    instruction="""You are an execution planner for a unified AI assistant.

Your job is to analyze a user's query and create an execution plan that describes
WHAT needs to be done, then map each step to the best available agent.

**User Query:**
{current_query}

**User Context (prior conversation history):**
{user_context}

**Currently Available Agents (discovered dynamically):**
{agent_catalog}

**Previous Planning Feedback (if any — use this to improve your plan):**
{planning_feedback}

**Instructions:**
1. Break the query into logical steps that need to be accomplished.
2. For each step, identify which available agent is best suited based on its
   description, skills, and tags.
3. If NO suitable agent exists for a step, mark it as "orchestrator" — the
   orchestrator will answer that part from its own general knowledge.
4. Determine the execution strategy:
   - "single": Only one step/agent needed
   - "parallel": Multiple steps that are INDEPENDENT and can run simultaneously
   - "sequential": Steps that DEPEND on each other (output of one feeds the next)
   - "hybrid": Mix of parallel and sequential (specify dependency order)
5. Consider user context — a follow-up question like "how do I upgrade?" should
   be interpreted in the context of what they were previously discussing.

**Output ONLY valid JSON, no other text:**
{{
  "reasoning": "Brief explanation of why this plan was chosen",
  "steps": [
    {{
      "step_id": 1,
      "description": "What this step accomplishes",
      "agent": "agent_name_from_catalog OR orchestrator",
      "depends_on": [],
      "input_context": "What information this step needs"
    }}
  ],
  "execution_strategy": "single|parallel|sequential|hybrid"
}}

IMPORTANT:
- Agent names in the plan MUST exactly match names from the catalog above.
- If the catalog says NO AGENTS AVAILABLE, set all steps to agent "orchestrator".
- Do NOT invent agent names that are not in the catalog.
""",
    output_key="execution_plan",
)


plan_refiner = LlmAgent(
    name="PlanRefiner",
    model=LLM_MODEL,
    include_contents="none",
    instruction="""You are a plan refinement engine.

You received an execution plan and the results of matching plan steps
to available agents. Some steps failed to match. Your job is to
adjust the plan so every step is executable.

**Current Plan:**
{execution_plan}

**Agent Matching Results:**
{matching_results}

**Available Agents:**
{agent_catalog}

**Instructions:**
1. For any step where the agent was NOT FOUND, either:
   a. Re-assign to a different available agent that could partially handle it
   b. Change the agent to "orchestrator" (answer from general knowledge)
   c. Merge the step into another step that has a valid agent
2. If all agents matched successfully, return the plan unchanged.
3. Maintain the execution_strategy — only change it if step merging altered
   the dependency structure.
4. ONLY use agent names that appear in the Available Agents list above or
   "orchestrator". Do NOT invent agent names.

**Output ONLY valid JSON with the same schema as the original plan.**
""",
    output_key="execution_plan",
)


response_assembler = LlmAgent(
    name="ResponseAssembler",
    model=LLM_MODEL,
    include_contents="none",
    instruction="""You are the unified voice of a company-wide AI assistant.

You have received responses from one or more specialist agents (or from the
orchestrator's own knowledge). Your job is to:

1. Combine all responses into a single, coherent, helpful answer
2. Maintain a consistent, professional, and friendly tone
3. Resolve any contradictions between responses
4. Add cross-references where domains overlap
5. NEVER reveal the internal multi-agent architecture to the user
6. NEVER say "Based on the agents..." or "According to the OCP agent..."
   — speak as one unified assistant

**Original User Query:**
{current_query}

**Responses Collected:**
{agent_responses}

**User Context:**
{user_context}

Produce a unified, natural response as if you are one knowledgeable assistant.
""",
    output_key="final_response",
)


self_answerer = LlmAgent(
    name="OrchestratorSelfAnswer",
    model=LLM_MODEL,
    include_contents="none",
    instruction=(
        "Answer the following based on your general knowledge. "
        "Be concise and helpful.\n\n"
        "User's original query: {current_query}\n"
        "Specific aspect to address: {self_answer_task}\n"
        "User context: {user_context}"
    ),
    output_key="self_answer",
)


# =============================================================================
# Orchestrator — Custom BaseAgent
# =============================================================================

class LightspeedOrchestrator(BaseAgent):
    """
    Plan-first orchestrator with dynamic agent discovery.

    Flow:
      Receive -> Discover -> [Plan -> Match -> Refine] loop -> Execute -> Assemble -> Persist

    The planning loop iterates until every step in the plan maps to a
    discovered agent (or "orchestrator"), or MAX_PLANNING_ITERATIONS is reached.

    The orchestrator holds ZERO references to specific remote agents.
    All agent knowledge comes from the registry at runtime.
    """

    planner: LlmAgent
    plan_refiner: LlmAgent
    response_assembler: LlmAgent
    self_answerer: LlmAgent

    model_config = {"arbitrary_types_allowed": True}

    def __init__(
        self,
        name: str,
        planner: LlmAgent,
        plan_refiner: LlmAgent,
        response_assembler: LlmAgent,
        self_answerer: LlmAgent,
    ):
        super().__init__(
            name=name,
            planner=planner,
            plan_refiner=plan_refiner,
            response_assembler=response_assembler,
            self_answerer=self_answerer,
            sub_agents=[planner, plan_refiner, response_assembler, self_answerer],
        )

    @override
    async def _run_async_impl(
        self, ctx: InvocationContext
    ) -> AsyncGenerator[Event, None]:
        """
        Main orchestration loop — plan-first, discover-then-execute.
        """
        logger.info(f"[{self.name}] ========== Orchestration Started ==========")

        # ==============================================================
        # Step 1: RECEIVE — Extract query, load context
        # ==============================================================
        user_query = self._extract_user_query(ctx)
        user_context = ctx.session.state.get("user_context", "No prior context.")
        ctx.session.state["current_query"] = user_query
        ctx.session.state["user_context"] = user_context
        ctx.session.state["planning_feedback"] = "None — this is the first planning attempt."
        ctx.session.state["self_answer_task"] = ""

        logger.info(f"[{self.name}] Query: {user_query}")

        # ==============================================================
        # Step 2: DISCOVER — Refresh the agent registry
        # ==============================================================
        logger.info(f"[{self.name}] Step 2: Discovering agents...")
        try:
            await registry.discover()
        except Exception as e:
            logger.error(f"[{self.name}] Discovery failed: {e}")

        agent_catalog = registry.get_catalog_for_planner()
        ctx.session.state["agent_catalog"] = agent_catalog
        logger.info(f"[{self.name}] Registry has {registry.agent_count} agent(s)")

        # ==============================================================
        # Step 3: PLAN-LOOP — Iteratively plan, match, refine
        #
        # Loop until:
        #   a) All plan steps are matched to agents (or "orchestrator"), OR
        #   b) MAX_PLANNING_ITERATIONS is exhausted
        #
        # Inspired by ADK LoopAgent pattern: sub-agents run in a loop
        # with a termination condition. Here the Planner and PlanRefiner
        # alternate until the plan validates.
        # ==============================================================
        plan = None
        all_matched = False

        for iteration in range(1, MAX_PLANNING_ITERATIONS + 1):
            logger.info(
                f"[{self.name}] Planning iteration {iteration}/{MAX_PLANNING_ITERATIONS}"
            )

            # --- 3a. PLAN — Create / re-create execution plan ---
            async for event in self.planner.run_async(ctx):
                yield event

            raw_plan = ctx.session.state.get("execution_plan", "")
            plan = self._parse_json(raw_plan)

            if not plan or "steps" not in plan:
                logger.warning(
                    f"[{self.name}] Iteration {iteration}: "
                    f"Planner returned invalid JSON. Retrying..."
                )
                ctx.session.state["planning_feedback"] = (
                    "Your previous response was not valid JSON. "
                    "You MUST output ONLY valid JSON matching the schema. "
                    "No markdown, no explanation — just the JSON object."
                )
                continue

            logger.info(
                f"[{self.name}] Iteration {iteration}: "
                f"{len(plan['steps'])} step(s), "
                f"strategy={plan.get('execution_strategy', 'unknown')}"
            )

            # --- 3b. MATCH — Resolve plan steps against registry ---
            matching_results, all_matched = self._match_plan_to_agents(plan)
            ctx.session.state["matching_results"] = "\n".join(matching_results)

            if all_matched:
                logger.info(
                    f"[{self.name}] Iteration {iteration}: "
                    f"All steps matched — plan validated"
                )
                break

            logger.info(
                f"[{self.name}] Iteration {iteration}: "
                f"Some steps unresolved — refining..."
            )

            if iteration < MAX_PLANNING_ITERATIONS:
                # --- 3c. REFINE — Let PlanRefiner adjust the plan ---
                async for event in self.plan_refiner.run_async(ctx):
                    yield event

                raw_refined = ctx.session.state.get("execution_plan", "")
                refined = self._parse_json(raw_refined)

                if refined and "steps" in refined:
                    # Re-match the refined plan
                    re_match_results, all_matched = self._match_plan_to_agents(refined)
                    ctx.session.state["matching_results"] = "\n".join(re_match_results)

                    if all_matched:
                        plan = refined
                        logger.info(
                            f"[{self.name}] Iteration {iteration}: "
                            f"Refined plan validated"
                        )
                        break

                    # Feed matching failures back into the next planning iteration
                    ctx.session.state["planning_feedback"] = (
                        f"Refinement attempt {iteration} still has unresolved steps:\n"
                        + "\n".join(re_match_results)
                        + "\n\nPlease create a new plan using ONLY agents from the catalog "
                        "or 'orchestrator'. Do NOT use agent names that are not listed."
                    )
                else:
                    ctx.session.state["planning_feedback"] = (
                        "The previous refinement produced invalid JSON. "
                        "Output ONLY valid JSON matching the plan schema."
                    )

        if plan is None or "steps" not in plan:
            logger.warning(
                f"[{self.name}] All planning iterations exhausted with no valid plan. "
                f"Answering directly."
            )
            ctx.session.state["self_answer_task"] = user_query
            async for event in self.self_answerer.run_async(ctx):
                yield event
            ctx.session.state["final_response"] = ctx.session.state.get(
                "self_answer", "I'll do my best to help with that directly."
            )
            return

        if not all_matched:
            logger.warning(
                f"[{self.name}] Max iterations reached with unresolved steps. "
                f"Proceeding with best-effort plan."
            )

        # ==============================================================
        # Step 4: EXECUTE — Run the validated plan
        # Strategy: single / parallel / sequential / hybrid
        # ==============================================================
        logger.info(f"[{self.name}] Step 4: Executing plan...")
        strategy = plan.get("execution_strategy", "single")
        steps = plan.get("steps", [])

        if strategy == "parallel":
            agent_responses = await self._execute_parallel(ctx, steps)
        elif strategy == "sequential":
            agent_responses = await self._execute_sequential(ctx, steps)
        elif strategy == "hybrid":
            agent_responses = await self._execute_hybrid(ctx, steps)
        else:  # "single" or fallback
            agent_responses = await self._execute_sequential(ctx, steps)

        logger.info(
            f"[{self.name}] Execution complete. "
            f"Collected {len(agent_responses)} response(s)"
        )

        # ==============================================================
        # Step 5: ASSEMBLE — Combine into unified response
        # ==============================================================
        logger.info(f"[{self.name}] Step 5: Assembling response...")

        formatted_responses = "\n\n".join(
            f"[Step {step_id}]: {response}"
            for step_id, response in agent_responses.items()
        )
        ctx.session.state["agent_responses"] = formatted_responses

        if len(agent_responses) > 1:
            async for event in self.response_assembler.run_async(ctx):
                yield event
        else:
            if len(agent_responses) == 1:
                final_text = next(iter(agent_responses.values()))
            else:
                final_text = (
                    "I apologize, but I wasn't able to process your request. "
                    "No agents were available to handle it. Please try again later."
                )
            ctx.session.state["final_response"] = final_text

            yield Event(
                invocation_id=ctx.invocation_id,
                author=self.name,
                branch=ctx.branch,
                content=types.Content(
                    role="model",
                    parts=[types.Part(text=final_text)],
                ),
            )

        # ==============================================================
        # Step 6: PERSIST — Update session context
        # ==============================================================
        agents_used = [s.get("agent", "orchestrator") for s in steps]
        prior_context = ctx.session.state.get("user_context", "")
        ctx.session.state["user_context"] = (
            f"{prior_context}\n\n"
            f"User asked: {user_query}\n"
            f"Agents involved: {', '.join(agents_used)}\n"
            f"Strategy used: {strategy}"
        ).strip()

        logger.info(f"[{self.name}] ========== Orchestration Complete ==========")

    # ------------------------------------------------------------------
    # Plan Matching
    # ------------------------------------------------------------------

    def _match_plan_to_agents(
        self, plan: dict
    ) -> tuple[list[str], bool]:
        """
        Validate a plan by matching each step's agent against the registry.

        Returns:
            (matching_results, all_matched) where matching_results is a list
            of human-readable match descriptions and all_matched is True when
            every step resolved to a known agent or "orchestrator".
        """
        matching_results = []
        all_matched = True

        for step in plan.get("steps", []):
            agent_name = step.get("agent", "orchestrator")

            if agent_name == "orchestrator":
                matching_results.append(
                    f"Step {step['step_id']}: '{step['description']}' "
                    f"-> orchestrator (self-handled)"
                )
                continue

            resolved = registry.get_agent_by_name(agent_name)
            if resolved:
                matching_results.append(
                    f"Step {step['step_id']}: '{step['description']}' "
                    f"-> MATCHED to agent '{agent_name}'"
                )
            else:
                candidates = registry.search_agents_by_capability(agent_name)
                if candidates:
                    best = candidates[0]
                    step["agent"] = best.name
                    matching_results.append(
                        f"Step {step['step_id']}: '{step['description']}' "
                        f"-> FUZZY MATCHED to '{best.name}' "
                        f"(original: '{agent_name}')"
                    )
                else:
                    all_matched = False
                    matching_results.append(
                        f"Step {step['step_id']}: '{step['description']}' "
                        f"-> NOT FOUND (agent '{agent_name}' not in registry)"
                    )

        return matching_results, all_matched

    # ------------------------------------------------------------------
    # Execution Strategies
    # ------------------------------------------------------------------

    async def _execute_sequential(
        self,
        ctx: InvocationContext,
        steps: list[dict],
    ) -> dict:
        """Execute steps one at a time. Each step can see prior output via state."""
        responses = {}
        for step in steps:
            step_id = step.get("step_id", "?")
            agent_name = step.get("agent", "orchestrator")
            description = step.get("description", "")

            logger.info(
                f"[{self.name}] Executing step {step_id}: "
                f"{description} -> {agent_name}"
            )

            response = await self._invoke_agent(ctx, agent_name, description)
            responses[step_id] = response
            ctx.session.state[f"step_{step_id}_result"] = response

        return responses

    async def _execute_parallel(
        self,
        ctx: InvocationContext,
        steps: list[dict],
    ) -> dict:
        """Execute all steps concurrently. Steps are independent."""
        responses = {}

        async def run_step(step: dict):
            step_id = step.get("step_id", "?")
            agent_name = step.get("agent", "orchestrator")
            description = step.get("description", "")

            logger.info(
                f"[{self.name}] [Parallel] Step {step_id}: "
                f"{description} -> {agent_name}"
            )
            result = await self._invoke_agent(ctx, agent_name, description)
            responses[step_id] = result

        tasks = [run_step(step) for step in steps]
        await asyncio.gather(*tasks, return_exceptions=True)
        return responses

    async def _execute_hybrid(
        self,
        ctx: InvocationContext,
        steps: list[dict],
    ) -> dict:
        """
        Hybrid execution: group steps by dependency, run independent groups
        in parallel, dependent groups sequentially.
        """
        responses = {}
        completed = set()
        remaining = list(steps)

        while remaining:
            ready = [
                s for s in remaining
                if all(dep in completed for dep in s.get("depends_on", []))
            ]

            if not ready:
                logger.warning(
                    f"[{self.name}] Hybrid: no ready steps, forcing sequential"
                )
                ready = [remaining[0]]

            if len(ready) == 1:
                step = ready[0]
                step_id = step.get("step_id", "?")
                agent_name = step.get("agent", "orchestrator")
                description = step.get("description", "")

                response = await self._invoke_agent(ctx, agent_name, description)
                responses[step_id] = response
                ctx.session.state[f"step_{step_id}_result"] = response
                completed.add(step_id)
            else:
                parallel_results = await self._execute_parallel(ctx, ready)
                for step_id, result in parallel_results.items():
                    responses[step_id] = result
                    ctx.session.state[f"step_{step_id}_result"] = result
                    completed.add(step_id)

            remaining = [
                s for s in remaining
                if s.get("step_id") not in completed
            ]

        return responses

    # ------------------------------------------------------------------
    # Agent Invocation
    # ------------------------------------------------------------------

    async def _invoke_agent(
        self,
        ctx: InvocationContext,
        agent_name: str,
        step_description: str,
    ) -> str:
        """
        Invoke an agent by name from the registry.
        Falls back to fuzzy search, then self-answer if nothing matches.
        """
        if agent_name == "orchestrator":
            return await self._self_answer(ctx, step_description)

        remote_agent = registry.get_agent_by_name(agent_name)

        if not remote_agent:
            candidates = registry.search_agents_by_capability(agent_name)
            if candidates:
                remote_agent = candidates[0].remote_agent
                logger.info(
                    f"[{self.name}] Fuzzy matched '{agent_name}' "
                    f"-> '{candidates[0].name}'"
                )
            else:
                logger.warning(
                    f"[{self.name}] Agent '{agent_name}' not found. Self-answering."
                )
                return await self._self_answer(ctx, step_description)

        try:
            last_text = ""
            async for event in remote_agent.run_async(ctx):
                if event.content and event.content.parts:
                    for part in event.content.parts:
                        if hasattr(part, "text") and part.text:
                            last_text = part.text
            return last_text or f"[Agent {agent_name} returned empty response]"
        except Exception as e:
            logger.error(f"[{self.name}] A2A call to '{agent_name}' failed: {e}")
            return (
                f"[Agent {agent_name} is currently unavailable. "
                f"Error: {str(e)[:200]}]"
            )

    async def _self_answer(
        self,
        ctx: InvocationContext,
        description: str,
    ) -> str:
        """
        The orchestrator answers a step using its registered self_answerer
        sub-agent when no suitable remote agent is available.
        """
        ctx.session.state["self_answer_task"] = description

        async for event in self.self_answerer.run_async(ctx):
            pass

        return ctx.session.state.get("self_answer", description)

    # ------------------------------------------------------------------
    # Utilities
    # ------------------------------------------------------------------

    def _extract_user_query(self, ctx: InvocationContext) -> str:
        """Extract the user's most recent query from session events."""
        if ctx.session.events:
            for event in reversed(ctx.session.events):
                if (
                    event.content
                    and event.content.role == "user"
                    and event.content.parts
                ):
                    for part in event.content.parts:
                        if hasattr(part, "text") and part.text:
                            return part.text
        return "No query provided."

    def _parse_json(self, raw: str) -> dict:
        """Parse JSON from LLM output, handling markdown fences."""
        try:
            cleaned = raw.strip()
            if cleaned.startswith("```"):
                cleaned = cleaned.split("\n", 1)[-1]
                cleaned = cleaned.rsplit("```", 1)[0]
            return json.loads(cleaned)
        except (json.JSONDecodeError, ValueError) as e:
            logger.warning(f"[{self.name}] JSON parse failed: {e}")
            return {}


# =============================================================================
# Root Agent — entry point for ADK (`adk web`, `adk api_server`)
#
# NOTE: No RemoteA2aAgent references here. No hardcoded agent names.
# The orchestrator discovers everything from the registry at runtime.
# =============================================================================

root_agent = LightspeedOrchestrator(
    name="lightspeed_orchestrator",
    planner=planner,
    plan_refiner=plan_refiner,
    response_assembler=response_assembler,
    self_answerer=self_answerer,
)
