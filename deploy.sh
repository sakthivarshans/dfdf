#!/usr/bin/env bash
# One-command deployment for table-data-extraction on GPU server
set -euo pipefail

# ── Helpers ───────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
info()  { echo -e "${GREEN}[INFO]${NC}  $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*"; exit 1; }

# The container always listens on 8001; only the host mapping is variable.
HOST_PORT="${HOST_PORT:-8001}"
export HOST_PORT

# ── Pre-flight checks ─────────────────────────────────────────────────────────
command -v docker >/dev/null 2>&1 || error "Docker is not installed. Install from https://docs.docker.com/get-docker/"
command -v docker compose >/dev/null 2>&1 || command -v docker-compose >/dev/null 2>&1 || \
    error "Docker Compose is not available. Install Docker Desktop or the compose plugin."

if ! docker info >/dev/null 2>&1; then
    warn "Cannot reach Docker daemon as current user."
    warn "Run this once to fix it, then log out and back in (or run: newgrp docker):"
    warn "  sudo usermod -aG docker \$USER"
    warn "Re-running with sudo for this session..."
    DOCKER="sudo docker"
else
    DOCKER="docker"
fi

info "Checking NVIDIA GPU and Container Toolkit..."
if ! nvidia-smi >/dev/null 2>&1; then
    warn "nvidia-smi not found — GPU will NOT be available to the container."
    warn "Install NVIDIA drivers and the NVIDIA Container Toolkit: https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/install-guide.html"
else
    GPU=$(nvidia-smi --query-gpu=name --format=csv,noheader | head -1)
    info "GPU detected: ${GPU}"
fi

# ── Setup data dirs required for bind mounts ──────────────────────────────────
info "Creating storage directories..."
mkdir -p storage/input storage/pages storage/json storage/output storage/tmp storage/db

# ── Build ─────────────────────────────────────────────────────────────────────
info "Building Docker image (first build downloads PaddlePaddle GPU wheels, ~5-10 min)..."
$DOCKER compose build

# ── Start ─────────────────────────────────────────────────────────────────────
info "Starting service..."
$DOCKER compose up -d

# Wait for health check
info "Waiting for service to become healthy (model download on first run can take a few minutes)..."
for i in $(seq 1 60); do
    STATUS=$($DOCKER inspect --format='{{.State.Health.Status}}' table-data-extraction 2>/dev/null || echo "starting")
    if [[ "$STATUS" == "healthy" ]]; then
        break
    elif [[ "$STATUS" == "unhealthy" ]]; then
        error "Container is unhealthy. Check logs: docker compose logs"
    fi
    echo -n "."
    sleep 5
done
echo ""

# ── Done ──────────────────────────────────────────────────────────────────────
HOST_IP=$(hostname -I | awk '{print $1}')
echo ""
echo -e "${GREEN}╔══════════════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║      Table Data Extraction API is running!           ║${NC}"
echo -e "${GREEN}╠══════════════════════════════════════════════════════╣${NC}"
echo -e "${GREEN}║${NC}  UI:      http://${HOST_IP}:${HOST_PORT}/ui                "
echo -e "${GREEN}║${NC}  API:     http://${HOST_IP}:${HOST_PORT}                   "
echo -e "${GREEN}║${NC}  Docs:    http://${HOST_IP}:${HOST_PORT}/docs              "
echo -e "${GREEN}║${NC}  Health:  http://${HOST_IP}:${HOST_PORT}/health            "
echo -e "${GREEN}╠══════════════════════════════════════════════════════╣${NC}"
echo -e "${GREEN}║${NC}  Logs:    docker compose logs -f                  "
echo -e "${GREEN}║${NC}  Stop:    docker compose down                     "
echo -e "${GREEN}║${NC}  Rebuild: docker compose build --no-cache         "
echo -e "${GREEN}╚══════════════════════════════════════════════════════╝${NC}"
