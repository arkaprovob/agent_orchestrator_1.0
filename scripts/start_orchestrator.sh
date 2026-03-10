#!/bin/bash
# =============================================================================
# Start the Lightspeed Orchestrator with ADK Web UI.
#
# Prerequisites:
#   - Remote agents must be running (see start_remote_agents.sh)
#   - GOOGLE_API_KEY must be set (via .env or environment)
#   - (Optional) Redis must be running for persistent sessions
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
echo " Lightspeed — Starting Orchestrator"
echo "=============================================="
echo ""

if [ -z "$GOOGLE_API_KEY" ]; then
    echo " ERROR: GOOGLE_API_KEY is not set."
    echo " Create a .env file in the project root with:"
    echo "   GOOGLE_API_KEY=your_key_here"
    echo ""
    exit 1
fi

echo " Make sure remote agents are running first:"
echo "   bash scripts/start_remote_agents.sh"
echo ""

PORT=${ORCHESTRATOR_PORT:-8000}

echo " Starting orchestrator with ADK Web UI..."
echo "   URL: http://localhost:$PORT"
echo ""

# ADK expects agents_dir to contain agent subdirectories.
# Copy orchestrator/ into agents/orchestrator/ (symlinks are rejected by
# newer ADK versions because they resolve outside the base directory).
if [ ! -d "$PROJECT_DIR/agents/orchestrator" ] || [ -L "$PROJECT_DIR/agents/orchestrator" ]; then
    rm -rf "$PROJECT_DIR/agents/orchestrator"
    mkdir -p "$PROJECT_DIR/agents"
    cp -r "$PROJECT_DIR/orchestrator" "$PROJECT_DIR/agents/orchestrator"
fi

adk web --port "$PORT" "$PROJECT_DIR/agents/"
