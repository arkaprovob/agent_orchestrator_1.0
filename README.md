# Unified Agent Orchestrator — Prototype

## Architecture Overview

```
                         ┌──────────────────────────────────┐
                         │        User Interfaces           │
                         │   (UI / CLI / API - any surface) │
                         └──────────────┬───────────────────┘
                                        │
                                        ▼
┌───────────────────────────────────────────────────────────────────────────┐
│                     ORCHESTRATOR (Custom ADK BaseAgent)                   │
│                                                                           │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐ │
│  │  Planner      │  │ PlanRefiner  │  │   Agent      │  │   Session    │ │
│  │  (LLM Agent)  │  │ (LLM Agent)  │  │   Registry   │  │   & Memory   │ │
│  │               │  │              │  │  (A2A Disc.) │  │   (Redis)    │ │
│  └──────────────┘  └──────────────┘  └──────────────┘  └──────────────┘ │
│                                                                           │
│  ┌───────────────────────────────┐  ┌─────────────────────────────────┐  │
│  │  Response Assembler           │  │  Self-Answerer                  │  │
│  │  (LLM Agent)                  │  │  (LLM Agent — fallback)        │  │
│  │  Unified voice for user       │  │  When no agent matches         │  │
│  └───────────────────────────────┘  └─────────────────────────────────┘  │
│                                                                           │
│  ┌─────────────────────────────────────────────────────────────────────┐  │
│  │  Iterative Plan-Match-Refine Loop (max 5 iterations)                │  │
│  │  Plan → Match against registry → Validate → Refine → repeat        │  │
│  └─────────────────────────────────────────────────────────────────────┘  │
└──────────────────────────┬───────────────────────────────────────────────┘
                           │
                      A2A Protocol
                           │
               ┌───────────┼───────────┐
               ▼           ▼           ▼
    ┌──────────────┐ ┌──────────────┐ ┌──────────────┐
    │  OCP/K8s     │ │  OpenStack   │ │  Knowledge   │
    │  Agent       │ │  Agent       │ │  /Docs Agent │
    │              │ │              │ │              │
    └──────────────┘ └──────────────┘ └──────────────┘
    All served from one ADK api_server on port 8001
    Discovered via /list-apps + /.well-known/agent-card.json
```

## Flow

1. **Receive** — Extract query, load session context and memory
2. **Discover** — Registry polls A2A endpoints, builds live agent catalog
3. **Plan-Loop** — Iteratively plan, match, and refine (up to 5 iterations):
   - Planner creates execution plan from query + agent catalog
   - Match each step against the registry (exact then fuzzy)
   - If unresolved, PlanRefiner adjusts; loop until validated
4. **Execute** — Run plan via single / sequential / parallel / hybrid strategy
5. **Assemble** — Combine multi-agent outputs into unified response
6. **Persist** — Save context for cross-surface continuity

## Components

| Component | ADK Primitive | Purpose |
|---|---|---|
| Orchestrator | `BaseAgent` (Custom) | Main entry point, orchestration logic |
| Planner | `LlmAgent` | Creates execution plans from query + catalog |
| PlanRefiner | `LlmAgent` | Adjusts plans when agents don't match |
| Response Assembler | `LlmAgent` | Combines multi-agent outputs into unified voice |
| Self-Answerer | `LlmAgent` | Fallback when no agent fits a step |
| Agent Registry | Custom Python | A2A discovery via well-known URLs |
| Remote Agents | `RemoteA2aAgent` | A2A client proxies to remote services |
| Session Service | `InMemorySessionService` / `RedisSessionService` | Session persistence |

## Quick Start

### Prerequisites

```bash
pip install -r requirements.txt
```

Create a `.env` file in the project root with your Google API key:

```bash
GOOGLE_API_KEY=your_key_here
```

> **Note:** You need a **paid-tier** Google API key. Free-tier keys have strict rate
> limits that will block the orchestrator. Get one from
> [Google AI Studio](https://aistudio.google.com/apikey) with billing enabled.

---

### Step 1: Start Remote Agents

Open a terminal and run:

```bash
bash scripts/start_remote_agents.sh
```

Or manually:

```bash
source .env
source venv/bin/activate
adk api_server --a2a --port 8001 remote_agents/
```

Verify agents are discoverable:

```bash
curl http://localhost:8001/list-apps
# ["knowledge_agent","ocp_agent","openstack_agent"]
```

Keep this terminal running.

---

### Step 2: Start the Orchestrator

Open a **second terminal** and choose one of the three interfaces below.

#### Option A: Web UI (recommended for exploration)

```bash
bash scripts/start_orchestrator.sh
```

Or manually:

```bash
source .env
source venv/bin/activate

# Create the agents wrapper directory (needed once)
mkdir -p agents && cp -r orchestrator agents/orchestrator

adk web --port 8000 agents/
```

Then open **http://localhost:8000** in your browser.

In the Web UI:
1. Select **orchestrator** from the agent dropdown (top-left)
2. Type your query in the chat box
3. Watch the orchestrator discover agents, plan, execute, and assemble

#### Option B: Interactive CLI

```bash
source .env
source venv/bin/activate
adk run orchestrator/
```

This starts an interactive terminal session. Type your query and press Enter.
Type `exit` to quit.

#### Option C: API Server (for programmatic access)

```bash
source .env
source venv/bin/activate

mkdir -p agents && cp -r orchestrator agents/orchestrator

adk api_server --port 8000 agents/
```

Then send requests via curl:

```bash
# Create a session
curl -s -X POST http://localhost:8000/apps/orchestrator/users/test_user/sessions \
  -H "Content-Type: application/json" \
  -d '{}' | python3 -m json.tool

# Run a query (replace SESSION_ID with the id from above)
curl -s -X POST http://localhost:8000/run \
  -H "Content-Type: application/json" \
  -d '{
    "app_name": "orchestrator",
    "user_id": "test_user",
    "session_id": "SESSION_ID",
    "new_message": {
      "role": "user",
      "parts": [{"text": "How do I scale a deployment in OpenShift?"}]
    }
  }'
```

Or use the SSE streaming endpoint for real-time events:

```bash
curl -s -N -X POST http://localhost:8000/run_sse \
  -H "Content-Type: application/json" \
  -d '{
    "app_name": "orchestrator",
    "user_id": "test_user",
    "session_id": "SESSION_ID",
    "new_message": {
      "role": "user",
      "parts": [{"text": "How do I scale a deployment in OpenShift?"}]
    }
  }'
```

#### Option D: Standalone Python Runner

```bash
source .env
source venv/bin/activate
python run_orchestrator.py
```

This uses the custom runner with configurable session/memory services.
Supports `--user-id` and `--session-id` flags for session continuity.

---

### Step 3: (Optional) Redis for Session Persistence

```bash
docker run -d -p 6379:6379 redis:latest
```

Set `USE_REDIS=True` in `orchestrator/config.py` to enable Redis-backed sessions.

### Step 4: Run Automated Tests

```bash
source .env
source venv/bin/activate
python test_flow.py
```

---

## Demo Scenarios

**Single-domain routing:**
> "How do I scale a deployment to 5 replicas in OpenShift?"

Routes to OCP Agent, returns specific `oc scale` commands.

**Cross-domain routing:**
> "Can I run Kubernetes workloads on my OpenStack infrastructure?"

Routes to Knowledge Agent (cross-product integration), explains OCP-on-OpenStack.

**General knowledge:**
> "What is the difference between RHEL 8 and RHEL 9?"

Routes to Knowledge Agent, returns detailed version comparison.

**Memory continuity:**
> Session 1: "I'm working on project Atlas using OCP 4.14"
> Session 2: "What are the upgrade paths for my cluster?"

Remembers project context from previous session.

**Dynamic discovery:**
> Deploy a new agent with an `agent.json` card → automatically available for routing.

---

## ADK CLI Reference

| Command | Purpose |
|---|---|
| `adk web agents/` | Web UI with chat interface on port 8000 |
| `adk run orchestrator/` | Interactive CLI — type queries in terminal |
| `adk api_server agents/` | REST API server (`/run`, `/run_sse`) |
| `adk api_server --a2a remote_agents/` | A2A server exposing remote agents |
| `adk api_server --a2a --port 8001 remote_agents/` | A2A server on custom port |

All commands support these useful flags:

| Flag | Purpose |
|---|---|
| `--port PORT` | Custom port (default 8000) |
| `-v` / `--verbose` | Debug-level logging |
| `--session_service_uri URI` | Session backend (`memory://`, `sqlite://path`) |
| `--memory_service_uri URI` | Memory backend (`memory://`, `rag://corpus_id`) |
| `--reload` | Auto-reload on code changes |
