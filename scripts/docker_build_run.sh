#!/bin/bash
# =============================================================================
# Build and run the Agent Orchestrator Docker image.
#
# Automatically detects OS and CPU architecture to build the correct image
# platform. Supports Docker and Podman.
#
# Usage:
#   bash scripts/docker_build_run.sh                 # build + run
#   bash scripts/docker_build_run.sh build           # build only
#   bash scripts/docker_build_run.sh run              # run only (image must exist)
#   bash scripts/docker_build_run.sh stop             # stop running container
#
# Environment overrides:
#   IMAGE_NAME          (default: agent-orchestrator)
#   IMAGE_TAG           (default: latest)
#   CONTAINER_NAME      (default: agent-orchestrator)
#   ORCHESTRATOR_PORT   (default: 8000)
#   REMOTE_AGENTS_PORT  (default: 8001)
#   GOOGLE_API_KEY      (required for run)
#   CONTAINER_ENGINE    (default: auto-detect docker or podman)
# =============================================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

IMAGE_NAME="${IMAGE_NAME:-agent-orchestrator}"
IMAGE_TAG="${IMAGE_TAG:-latest}"
CONTAINER_NAME="${CONTAINER_NAME:-agent-orchestrator}"
ORCH_PORT="${ORCHESTRATOR_PORT:-8000}"
REMOTE_PORT="${REMOTE_AGENTS_PORT:-8001}"

# ── Load .env if present ─────────────────────────────────────────────────────

if [ -f "$PROJECT_DIR/.env" ]; then
    set -a
    source "$PROJECT_DIR/.env"
    set +a
fi

# ── Detect Container Engine ──────────────────────────────────────────────────

detect_engine() {
    if [ -n "$CONTAINER_ENGINE" ]; then
        echo "$CONTAINER_ENGINE"
        return
    fi

    if command -v podman &> /dev/null; then
        echo "podman"
    elif command -v docker &> /dev/null; then
        echo "docker"
    else
        echo ""
    fi
}

ENGINE=$(detect_engine)

if [ -z "$ENGINE" ]; then
    echo "ERROR: Neither docker nor podman found. Please install one of them."
    exit 1
fi

echo "Container engine: $ENGINE"

# ── Detect OS and Architecture ───────────────────────────────────────────────

detect_platform() {
    local os arch platform

    os="$(uname -s | tr '[:upper:]' '[:lower:]')"
    arch="$(uname -m)"

    case "$os" in
        linux)   os="linux" ;;
        darwin)  os="linux" ;;   # Docker/Podman always builds linux images
        *)
            echo "WARNING: Unsupported OS '$os', defaulting to linux"
            os="linux"
            ;;
    esac

    case "$arch" in
        x86_64|amd64)       arch="amd64"  ;;
        aarch64|arm64)      arch="arm64"  ;;
        armv7l|armhf)       arch="arm/v7" ;;
        *)
            echo "WARNING: Unsupported architecture '$arch', defaulting to amd64"
            arch="amd64"
            ;;
    esac

    platform="${os}/${arch}"
    echo "$platform"
}

PLATFORM=$(detect_platform)
echo "Detected platform: $PLATFORM"
echo "Host OS: $(uname -s) | Host Arch: $(uname -m)"
echo ""

# ── Build ────────────────────────────────────────────────────────────────────

do_build() {
    echo "=============================================="
    echo " Building image: ${IMAGE_NAME}:${IMAGE_TAG}"
    echo " Platform:       $PLATFORM"
    echo " Engine:         $ENGINE"
    echo "=============================================="
    echo ""

    $ENGINE build \
        --platform "$PLATFORM" \
        -t "${IMAGE_NAME}:${IMAGE_TAG}" \
        -f "$PROJECT_DIR/Dockerfile" \
        "$PROJECT_DIR"

    echo ""
    echo "Build complete: ${IMAGE_NAME}:${IMAGE_TAG}"
    echo ""
}

# ── Run ──────────────────────────────────────────────────────────────────────

do_run() {
    if [ -z "$GOOGLE_API_KEY" ]; then
        echo "ERROR: GOOGLE_API_KEY is not set."
        echo "Set it in your .env file or export it:"
        echo "  export GOOGLE_API_KEY=your_key_here"
        exit 1
    fi

    # Stop existing container with the same name
    if $ENGINE ps -a --format '{{.Names}}' 2>/dev/null | grep -q "^${CONTAINER_NAME}$"; then
        echo "Stopping existing container '$CONTAINER_NAME' ..."
        $ENGINE rm -f "$CONTAINER_NAME" > /dev/null 2>&1 || true
    fi

    echo "=============================================="
    echo " Running: ${IMAGE_NAME}:${IMAGE_TAG}"
    echo " Container:      $CONTAINER_NAME"
    echo " Orchestrator:   http://localhost:$ORCH_PORT"
    echo " Remote Agents:  http://localhost:$REMOTE_PORT/list-apps"
    echo "=============================================="
    echo ""

    EXTRA_ENV=""
    [ -n "$USE_REDIS" ]    && EXTRA_ENV="$EXTRA_ENV -e USE_REDIS=$USE_REDIS"
    [ -n "$REDIS_HOST" ]   && EXTRA_ENV="$EXTRA_ENV -e REDIS_HOST=$REDIS_HOST"
    [ -n "$REDIS_PORT" ]   && EXTRA_ENV="$EXTRA_ENV -e REDIS_PORT=$REDIS_PORT"

    $ENGINE run -d \
        --name "$CONTAINER_NAME" \
        -p "${ORCH_PORT}:8000" \
        -p "${REMOTE_PORT}:8001" \
        -e "GOOGLE_API_KEY=${GOOGLE_API_KEY}" \
        -e "ORCHESTRATOR_PORT=8000" \
        -e "REMOTE_AGENTS_PORT=8001" \
        $EXTRA_ENV \
        "${IMAGE_NAME}:${IMAGE_TAG}"

    echo ""
    echo "Container '$CONTAINER_NAME' started in detached mode."
    echo ""
    echo "Useful commands:"
    echo "  $ENGINE logs -f $CONTAINER_NAME          # follow logs"
    echo "  $ENGINE exec -it $CONTAINER_NAME bash    # shell into container"
    echo "  bash scripts/docker_build_run.sh stop    # stop the container"
    echo ""
}

# ── Stop ─────────────────────────────────────────────────────────────────────

do_stop() {
    echo "Stopping container '$CONTAINER_NAME' ..."
    $ENGINE rm -f "$CONTAINER_NAME" 2>/dev/null || true
    echo "Done."
}

# ── Main ─────────────────────────────────────────────────────────────────────

ACTION="${1:-all}"

case "$ACTION" in
    build)
        do_build
        ;;
    run)
        do_run
        ;;
    stop)
        do_stop
        ;;
    all)
        do_build
        do_run
        ;;
    *)
        echo "Usage: $0 {build|run|stop|all}"
        echo ""
        echo "  build   Build the Docker image"
        echo "  run     Run the container (image must exist)"
        echo "  stop    Stop and remove the running container"
        echo "  all     Build + run (default)"
        exit 1
        ;;
esac
