#!/bin/bash
set -e

REMOTE_PORT=${REMOTE_AGENTS_PORT:-8001}
ORCH_PORT=${ORCHESTRATOR_PORT:-8000}

if [ -z "$GOOGLE_API_KEY" ]; then
    echo "ERROR: GOOGLE_API_KEY is not set."
    echo "Pass it via: -e GOOGLE_API_KEY=your_key"
    exit 1
fi

echo "=============================================="
echo " Agent Orchestrator — Docker"
echo "=============================================="
echo ""
echo " Remote agents port : $REMOTE_PORT"
echo " Orchestrator port  : $ORCH_PORT"
echo ""

echo "[1/2] Starting remote agents on port $REMOTE_PORT ..."
adk api_server --a2a --port "$REMOTE_PORT" --host 0.0.0.0 /app/remote_agents/ &
REMOTE_PID=$!

echo "     Waiting for remote agents to be ready ..."
for i in $(seq 1 30); do
    if curl -sf "http://localhost:$REMOTE_PORT/list-apps" > /dev/null 2>&1; then
        echo "     Remote agents ready."
        break
    fi
    if [ "$i" -eq 30 ]; then
        echo "     WARNING: Remote agents did not respond within 30s."
    fi
    sleep 1
done

echo "[2/2] Starting orchestrator on port $ORCH_PORT ..."
adk web --port "$ORCH_PORT" --host 0.0.0.0 /app/agents/ &
ORCH_PID=$!

trap "kill $REMOTE_PID $ORCH_PID 2>/dev/null; exit 0" SIGTERM SIGINT

echo ""
echo "=============================================="
echo " Both services started."
echo "   Web UI:    http://localhost:$ORCH_PORT"
echo "   Agents:    http://localhost:$REMOTE_PORT/list-apps"
echo "=============================================="
echo ""

wait -n $REMOTE_PID $ORCH_PID
exit $?
