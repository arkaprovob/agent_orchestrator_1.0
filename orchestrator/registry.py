"""
A2A Agent Discovery Registry.

Dynamically discovers remote agents by polling well-known URLs for A2A agent cards.
Any agent that exposes an agent card will be automatically registered and available
for orchestration.

KEY DESIGN PRINCIPLE:
  The registry knows NOTHING about what agents exist ahead of time.
  It probes discovery endpoints, reads whatever agent cards it finds,
  and builds a live catalog. The orchestrator's planner uses this catalog
  to match plan steps to available agents — no hardcoding anywhere.
"""

import json
import logging
import asyncio
from dataclasses import dataclass, field
from typing import Optional

import aiohttp

from google.adk.agents.remote_a2a_agent import RemoteA2aAgent

logger = logging.getLogger("google_adk." + __name__)


@dataclass
class DiscoveredAgent:
    """Metadata for a discovered remote agent."""
    name: str
    description: str
    url: str
    skills: list[dict] = field(default_factory=list)
    version: str = "unknown"
    agent_card_url: str = ""
    remote_agent: Optional[RemoteA2aAgent] = None

    @property
    def skill_tags(self) -> list[str]:
        """Flat list of all skill tags for matching."""
        tags = []
        for skill in self.skills:
            tags.extend(skill.get("tags", []))
            if "name" in skill:
                tags.append(skill["name"].lower())
            if "id" in skill:
                tags.append(skill["id"].lower())
        return tags

    @property
    def skill_descriptions(self) -> str:
        """Formatted skill descriptions for the planner."""
        if not self.skills:
            return "General capabilities."
        return "; ".join(
            f'{s.get("name", s.get("id", "unknown"))}: {s.get("description", "")}'
            for s in self.skills
        )


class AgentRegistry:
    """
    Registry that discovers and manages remote A2A agents.

    Probes a list of base URLs, fetches agent cards from well-known paths,
    and builds a live catalog of available agents with their capabilities.

    The registry is the SINGLE SOURCE OF TRUTH for what agents are available.
    The orchestrator NEVER references agents that aren't in the registry.

    Usage:
        registry = AgentRegistry(discovery_urls=["http://localhost:8001", ...])
        await registry.discover()
        catalog = registry.get_catalog_for_planner()  # str for LLM
        agent = registry.get_agent_by_name("some_agent")  # RemoteA2aAgent
    """

    def __init__(self, discovery_urls: list[str], poll_interval: int = 30):
        self.discovery_urls = discovery_urls
        self.poll_interval = poll_interval
        self._agents: dict[str, DiscoveredAgent] = {}
        self._lock = asyncio.Lock()

    # -----------------------------------------------------------------
    # Discovery
    # -----------------------------------------------------------------

    async def discover(self) -> list[DiscoveredAgent]:
        """
        Poll all discovery URLs and register any newly found agents.
        Returns list of all currently discovered agents.
        """
        discovered = []
        tasks = [self._probe_base_url(url) for url in self.discovery_urls]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for result in results:
            if isinstance(result, Exception):
                logger.warning(f"Discovery probe failed: {result}")
                continue
            if result is not None:
                agents_found = result if isinstance(result, list) else [result]
                for agent in agents_found:
                    async with self._lock:
                        self._agents[agent.name] = agent
                        discovered.append(agent)
                        logger.info(
                            f"Discovered agent: {agent.name} "
                            f"({agent.description[:80]}...) at {agent.url}"
                        )

        logger.info(
            f"Discovery complete. {len(self._agents)} agent(s) in registry."
        )
        return discovered

    async def _probe_base_url(
        self, base_url: str
    ) -> Optional[list[DiscoveredAgent]]:
        """
        Probe a base URL for agent cards using multiple strategies.
        NO hardcoded agent names — purely protocol-driven discovery.
        """
        base_url = base_url.rstrip("/")
        found = []

        # Strategy 1: Standard A2A well-known path (single agent per URL)
        for card_filename in ("agent-card.json", "agent.json"):
            card_path = f"{base_url}/.well-known/{card_filename}"
            card = await self._fetch_json(card_path)
            if card and isinstance(card, dict) and "name" in card:
                found.append(self._card_to_agent(card, card_path))
                return found

        # Strategy 2: ADK api_server pattern — enumerate agents via /list-apps
        # then fetch each agent's card from its A2A well-known path
        for list_endpoint in ("/list-apps", "/a2a"):
            a2a_response = await self._fetch_json(f"{base_url}{list_endpoint}")
            if not a2a_response:
                continue

            agent_names = []
            if isinstance(a2a_response, list):
                agent_names = a2a_response
            elif isinstance(a2a_response, dict):
                agent_names = list(a2a_response.keys())

            for agent_name in agent_names:
                if not isinstance(agent_name, str):
                    continue
                for card_filename in ("agent-card.json", "agent.json"):
                    card_url = (
                        f"{base_url}/a2a/{agent_name}"
                        f"/.well-known/{card_filename}"
                    )
                    card = await self._fetch_json(card_url)
                    if card and isinstance(card, dict) and "name" in card:
                        found.append(self._card_to_agent(card, card_url))
                        break

            if found:
                break

        if not found:
            logger.debug(f"No agent cards found at {base_url}")

        return found if found else None

    async def _fetch_json(self, url: str) -> Optional[dict | list]:
        """Fetch JSON from a URL with timeout and error handling."""
        try:
            timeout = aiohttp.ClientTimeout(total=5)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(url) as response:
                    if response.status == 200:
                        return await response.json()
        except (
            aiohttp.ClientError,
            asyncio.TimeoutError,
            json.JSONDecodeError,
        ) as e:
            logger.debug(f"Failed to fetch {url}: {e}")
        return None

    def _card_to_agent(self, card: dict, card_url: str) -> DiscoveredAgent:
        """Convert an A2A agent card dict to a DiscoveredAgent."""
        name = card.get("name", "unknown_agent")
        description = card.get("description", "No description provided.")
        url = card.get(
            "url", card_url.replace("/.well-known/agent.json", "")
        )
        skills = card.get("skills", [])
        version = card.get("version", "unknown")

        remote_agent = RemoteA2aAgent(
            name=name,
            description=description,
            agent_card=card_url,
        )

        return DiscoveredAgent(
            name=name,
            description=description,
            url=url,
            skills=skills,
            version=version,
            agent_card_url=card_url,
            remote_agent=remote_agent,
        )

    # -----------------------------------------------------------------
    # Catalog — Injected into the Planner prompt dynamically
    # -----------------------------------------------------------------

    def get_catalog_for_planner(self) -> str:
        """
        Build a structured catalog string describing ALL currently discovered
        agents and their capabilities.

        This is injected into the Planner's prompt at RUNTIME so it can
        map abstract plan steps to real, available agents. The planner never
        has hardcoded knowledge of what agents exist.

        Returns a formatted string suitable for LLM consumption.
        """
        if not self._agents:
            return (
                "NO AGENTS CURRENTLY AVAILABLE.\n"
                "You must answer the user's question directly using your own "
                "knowledge, as no specialist agents are reachable."
            )

        lines = []
        for agent in self._agents.values():
            lines.append(
                f"AGENT: {agent.name}\n"
                f"  Description: {agent.description}\n"
                f"  Skills: {agent.skill_descriptions}\n"
                f"  Tags: {', '.join(agent.skill_tags)}"
            )
        return "\n\n".join(lines)

    def get_agent_names(self) -> list[str]:
        """List of all discovered agent names."""
        return list(self._agents.keys())

    # -----------------------------------------------------------------
    # Lookup — Used by the Executor to resolve plan steps to agents
    # -----------------------------------------------------------------

    def get_agent_by_name(self, name: str) -> Optional[RemoteA2aAgent]:
        """Look up a discovered agent by exact name."""
        agent = self._agents.get(name)
        return agent.remote_agent if agent else None

    def search_agents_by_capability(
        self, capability: str
    ) -> list[DiscoveredAgent]:
        """
        Fuzzy search for agents whose skills, tags, or description match
        a capability string. Used when the planner's output doesn't exactly
        match an agent name — we try to find the best match.
        """
        query_lower = capability.lower()
        scored: list[tuple[int, DiscoveredAgent]] = []

        for agent in self._agents.values():
            score = 0
            # Exact name match
            if query_lower == agent.name.lower():
                score += 100
            # Partial name match
            elif query_lower in agent.name.lower():
                score += 50
            # Tag match
            for tag in agent.skill_tags:
                if query_lower in tag:
                    score += 30
            # Description match
            if query_lower in agent.description.lower():
                score += 20
            # Skill description match
            if query_lower in agent.skill_descriptions.lower():
                score += 10

            if score > 0:
                scored.append((score, agent))

        # Return sorted by best match
        scored.sort(key=lambda x: x[0], reverse=True)
        return [agent for _, agent in scored]

    def get_all_agents(self) -> dict[str, DiscoveredAgent]:
        """Get all discovered agents."""
        return dict(self._agents)

    @property
    def agent_count(self) -> int:
        return len(self._agents)

    @property
    def is_empty(self) -> bool:
        return len(self._agents) == 0

    # -----------------------------------------------------------------
    # Background Discovery
    # -----------------------------------------------------------------

    async def start_background_discovery(self):
        """Start a background task that periodically re-discovers agents."""
        while True:
            try:
                await self.discover()
            except Exception as e:
                logger.error(f"Background discovery error: {e}")
            await asyncio.sleep(self.poll_interval)
