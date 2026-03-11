"""
Configuration for the Agent Orchestrator.
"""

# --- Model Configuration ---
LLM_MODEL = "gemini-2.5-flash"

# --- Application Identity ---
APP_NAME = "agent_orchestrator"

# --- Redis Configuration ---
USE_REDIS = False  # Set to True when Redis is available
REDIS_HOST = "localhost"
REDIS_PORT = 6379
REDIS_PASSWORD = None
REDIS_SSL = False

# --- A2A Agent Discovery ---
# Well-known URLs where remote agents expose their agent cards.
# The orchestrator polls these endpoints to discover available agents.
# Any A2A-compliant agent can register here regardless of its tech stack.
AGENT_DISCOVERY_URLS = [
    "http://localhost:8001",  # ADK api_server hosting all remote agents
]

# How often (seconds) to re-poll discovery URLs for new/updated agents.
DISCOVERY_POLL_INTERVAL = 30

# --- Orchestrator Behavior ---
MAX_PLANNING_ITERATIONS = 5  # Max plan-match-refine iterations before proceeding
