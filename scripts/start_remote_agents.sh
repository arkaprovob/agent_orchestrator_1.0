#!/bin/bash
# =============================================================================
# Start all remote demo agents as a single A2A server.
#
# ADK discovers agents by scanning subdirectories of remote_agents/ for
# directories containing an agent.json (A2A agent card). All agents are
# served from one process on port 8001.
#
# The orchestrator's registry probes http://localhost:8001/list-apps to
# discover all available agents dynamically.
# =============================================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

# Load API key from .env if present
if [ -f "$PROJECT_DIR/.env" ]; then
    set -a
    source "$PROJECT_DIR/.env"
    set +a
fi

# Activate virtualenv if present
if [ -f "$PROJECT_DIR/venv/bin/activate" ]; then
    source "$PROJECT_DIR/venv/bin/activate"
fi

echo "=============================================="
echo " Agent Orchestrator — Starting Remote Agents"
echo "=============================================="
echo ""

if [ -z "$GOOGLE_API_KEY" ]; then
    echo " WARNING: GOOGLE_API_KEY is not set."
    echo " Create a .env file in the project root with:"
    echo "   GOOGLE_API_KEY=your_key_here"
    echo ""
fi

PORT=${REMOTE_AGENTS_PORT:-8001}

echo " Serving all agents from: $PROJECT_DIR/remote_agents/"
echo " Port: $PORT"
echo ""

adk api_server --a2a --port "$PORT" "$PROJECT_DIR/remote_agents/" &
ADK_PID=$!
echo " ADK api_server PID: $ADK_PID"

# Wait for server to start
sleep 3

echo ""
echo "=============================================="
echo " Remote agents started!"
echo ""
echo " Discovery endpoint:"
echo "   http://localhost:$PORT/list-apps"
echo ""
echo " Agent cards:"
echo "   OCP:       http://localhost:$PORT/a2a/ocp_agent/.well-known/agent-card.json"
echo "   OpenStack: http://localhost:$PORT/a2a/openstack_agent/.well-known/agent-card.json"
echo "   Knowledge: http://localhost:$PORT/a2a/knowledge_agent/.well-known/agent-card.json"
echo ""
echo " Press Ctrl+C to stop."
echo "=============================================="

wait $ADK_PID
