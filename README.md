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

Then send requests via curl — see [Try It with curl](#try-it-with-curl) below for
ready-to-copy examples covering single-domain, sequential, parallel, and hybrid
multi-agent queries.

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

### Step 4: Run with Docker / Podman

The project includes a Dockerfile and a build script that automatically detects your
OS and CPU architecture (x86_64 / Apple Silicon / ARM) and builds the correct image.

#### Quick start

```bash
# Build and run in one command
bash scripts/docker_build_run.sh
```

The script auto-detects Docker or Podman — whichever is installed.

#### Build only

```bash
bash scripts/docker_build_run.sh build
```

#### Run only (image must already exist)

```bash
bash scripts/docker_build_run.sh run
```

#### Stop the container

```bash
bash scripts/docker_build_run.sh stop
```

#### What the container runs

The image bundles both services in a single container:

| Service | Internal Port | Default Host Port |
|---|---|---|
| Remote A2A Agents | 8001 | 8001 |
| Orchestrator Web UI | 8000 | 8000 |

The entrypoint starts remote agents first, waits for them to be healthy, then starts
the orchestrator.

#### Environment variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `GOOGLE_API_KEY` | **Yes** | — | Google AI API key (paid-tier recommended) |
| `ORCHESTRATOR_PORT` | No | `8000` | Host port for the orchestrator |
| `REMOTE_AGENTS_PORT` | No | `8001` | Host port for remote agents |
| `USE_REDIS` | No | `false` | Enable Redis-backed sessions |
| `REDIS_HOST` | No | `localhost` | Redis host (use host.containers.internal for host Redis) |
| `REDIS_PORT` | No | `6379` | Redis port |
| `IMAGE_NAME` | No | `agent-orchestrator` | Docker image name |
| `IMAGE_TAG` | No | `latest` | Docker image tag |
| `CONTAINER_NAME` | No | `agent-orchestrator` | Container name |
| `CONTAINER_ENGINE` | No | auto-detect | Force `docker` or `podman` |

#### Passing environment variables

The build script automatically loads your `.env` file from the project root, so if
you already have `GOOGLE_API_KEY` set there, no extra steps are needed:

```bash
bash scripts/docker_build_run.sh run
```

You can also pass variables inline or via `export`:

```bash
# Inline
GOOGLE_API_KEY=your_key_here bash scripts/docker_build_run.sh run

# Or export first
export GOOGLE_API_KEY=your_key_here
bash scripts/docker_build_run.sh run
```

Multiple variables work the same way:

```bash
GOOGLE_API_KEY=your_key_here ORCHESTRATOR_PORT=9000 USE_REDIS=true \
  bash scripts/docker_build_run.sh run
```

#### Custom ports example

```bash
ORCHESTRATOR_PORT=9000 REMOTE_AGENTS_PORT=9001 bash scripts/docker_build_run.sh
```

#### Verify the container

```bash
# Check remote agents
curl http://localhost:8001/list-apps
# ["knowledge_agent","ocp_agent","openstack_agent"]

# Check orchestrator (redirects to Web UI)
curl -I http://localhost:8000/
```

#### Architecture support

The build script detects the host machine and sets `--platform` accordingly:

| Host | Platform flag |
|---|---|
| Intel / AMD (x86_64) | `linux/amd64` |
| Apple Silicon (M1/M2/M3/M4) | `linux/arm64` |
| ARM 32-bit (armv7l) | `linux/arm/v7` |

---

### Step 5: Run Automated Tests

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

**Multi-agent sequential execution:**
> "First explain how to set up networking in OpenStack using Neutron, then describe how to configure an OpenShift cluster to use that OpenStack network for pod connectivity."

Routes to OpenStack Agent first, then OCP Agent — output of the first step feeds the second. Strategy: `sequential`.

**Multi-agent parallel execution:**
> "Give me a summary of RHEL 9 security features, the key improvements in OpenShift 4.15, and best practices for OpenStack Neutron network segmentation."

Routes to Knowledge Agent, OCP Agent, and OpenStack Agent simultaneously — all three steps are independent. Strategy: `parallel`.

**Multi-agent hybrid execution:**
> "Compare the storage options available in both OpenShift and OpenStack, then recommend a unified storage strategy for running containerized workloads on OpenStack infrastructure."

Routes to OCP Agent and OpenStack Agent in parallel to gather storage options, then synthesizes a unified recommendation that depends on both outputs. Strategy: `hybrid`.

**Memory continuity:**
> Session 1: "I'm working on project Atlas using OCP 4.14"
> Session 2: "What are the upgrade paths for my cluster?"

Remembers project context from previous session.

**Dynamic discovery:**
> Deploy a new agent with an `agent.json` card → automatically available for routing.

---

## Try It with curl

Make sure both servers are running first:

```bash
# Terminal 1 — remote agents
bash scripts/start_remote_agents.sh

# Terminal 2 — orchestrator API
bash scripts/start_orchestrator.sh
# (or: source .env && mkdir -p agents && cp -r orchestrator agents/orchestrator && adk api_server --port 8000 agents/)
```

### Create a session

```bash
curl -s -X POST http://localhost:8000/apps/orchestrator/users/test_user/sessions \
  -H "Content-Type: application/json" \
  -d '{}' | python3 -m json.tool
```

Copy the `id` value from the response — you'll use it as `SESSION_ID` below.

### Single-domain query (OCP)

```bash
curl -s -X POST http://localhost:8000/run \
  -H "Content-Type: application/json" \
  -d '{
    "app_name": "orchestrator",
    "user_id": "test_user",
    "session_id": "SESSION_ID",
    "new_message": {
      "role": "user",
      "parts": [{"text": "How do I scale a deployment to 5 replicas in OpenShift?"}]
    }
  }'
```

Routes to `ocp_agent`. Strategy: `single`.

### Multi-agent sequential (OpenStack → OCP)

```bash
curl -s -X POST http://localhost:8000/run \
  -H "Content-Type: application/json" \
  -d '{
    "app_name": "orchestrator",
    "user_id": "test_user",
    "session_id": "SESSION_ID",
    "new_message": {
      "role": "user",
      "parts": [{"text": "First explain how to set up networking in OpenStack using Neutron, then describe how to configure an OpenShift cluster to use that OpenStack network for pod connectivity."}]
    }
  }'
```

Routes to `openstack_agent` first, then `ocp_agent` (depends on step 1 output). Strategy: `sequential`.

### Multi-agent parallel (RHEL + OCP + OpenStack)

```bash
curl -s -X POST http://localhost:8000/run \
  -H "Content-Type: application/json" \
  -d '{
    "app_name": "orchestrator",
    "user_id": "test_user",
    "session_id": "SESSION_ID",
    "new_message": {
      "role": "user",
      "parts": [{"text": "Give me a summary of RHEL 9 security features, the key improvements in OpenShift 4.15, and best practices for OpenStack Neutron network segmentation."}]
    }
  }'
```

Routes to `knowledge_agent`, `ocp_agent`, and `openstack_agent` simultaneously. Strategy: `parallel`.

### Multi-agent hybrid (parallel gather + sequential synthesis)

```bash
curl -s -X POST http://localhost:8000/run \
  -H "Content-Type: application/json" \
  -d '{
    "app_name": "orchestrator",
    "user_id": "test_user",
    "session_id": "SESSION_ID",
    "new_message": {
      "role": "user",
      "parts": [{"text": "Compare the storage options available in both OpenShift and OpenStack, then recommend a unified storage strategy for running containerized workloads on OpenStack infrastructure."}]
    }
  }'
```

Routes to `ocp_agent` and `openstack_agent` in parallel, then synthesizes a unified recommendation. Strategy: `hybrid`.

> **Tip:** Create a new session (repeat the session creation step) for each query to avoid context carryover between tests.

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

---

## Docker Image Distribution

### Push to a Container Registry

Tag and push the image to any OCI-compliant registry (Docker Hub, Quay.io, GHCR, etc.):

```bash
# Tag for your registry
podman tag agent-orchestrator:latest quay.io/YOUR_ORG/agent-orchestrator:latest

# Push
podman push quay.io/YOUR_ORG/agent-orchestrator:latest
```

Replace `podman` with `docker` if that's your engine. Replace `quay.io/YOUR_ORG` with
your actual registry path.

### Pull and Run on Another Machine

```bash
# Pull
podman pull quay.io/YOUR_ORG/agent-orchestrator:latest

# Run
podman run -d \
  --name agent-orchestrator \
  -p 8000:8000 \
  -p 8001:8001 \
  -e GOOGLE_API_KEY=your_key_here \
  quay.io/YOUR_ORG/agent-orchestrator:latest
```

### Multi-Architecture Images

To build and push a multi-arch manifest (e.g., for both `amd64` and `arm64`):

```bash
# With Docker Buildx
docker buildx create --use
docker buildx build \
  --platform linux/amd64,linux/arm64 \
  -t quay.io/YOUR_ORG/agent-orchestrator:latest \
  --push .

# With Podman manifest
podman build --platform linux/amd64 -t agent-orchestrator:amd64 .
podman build --platform linux/arm64 -t agent-orchestrator:arm64 .
podman manifest create agent-orchestrator:latest \
  agent-orchestrator:amd64 \
  agent-orchestrator:arm64
podman manifest push agent-orchestrator:latest \
  quay.io/YOUR_ORG/agent-orchestrator:latest
```

### Save / Load for Offline Transfer

```bash
# Export to a tar file
podman save agent-orchestrator:latest -o agent-orchestrator.tar

# Import on another machine
podman load -i agent-orchestrator.tar
```
