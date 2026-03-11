FROM python:3.12-slim

LABEL maintainer="apb@live.in"
LABEL description="Unified Agent Orchestrator with remote A2A agents"

RUN apt-get update && \
    apt-get install -y --no-install-recommends curl && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY orchestrator/ orchestrator/
COPY remote_agents/ remote_agents/
COPY run_orchestrator.py .

RUN mkdir -p agents && cp -r orchestrator agents/orchestrator

COPY scripts/entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

ENV ORCHESTRATOR_PORT=8000
ENV REMOTE_AGENTS_PORT=8001

EXPOSE 8000 8001

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD curl -sf http://localhost:${REMOTE_AGENTS_PORT}/list-apps && \
        curl -sf http://localhost:${ORCHESTRATOR_PORT}/ || exit 1

ENTRYPOINT ["/app/entrypoint.sh"]
