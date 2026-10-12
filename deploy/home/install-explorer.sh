#!/usr/bin/env bash
# Usage: install-explorer.sh [--serve]
#
# Builds our Ladybug Explorer image (explorer/) and runs it on the devbox as the pa-explorer
# container, read-only against the instance's graph, bound to localhost, and restarted by Docker
# at boot. The image's follow-current moves it to each newly published build by itself. With
# --serve, also exposes it on the tailnet with `tailscale serve` (needs sudo unless you are
# tailscaled's operator). Safe to re-run: the container is replaced, and the serve config with it.
set -euo pipefail
source "$(dirname "$0")/lib.sh"

serve=0
case ${1:-} in
  --serve) serve=1 ;;
  "") ;;
  *) die "usage: install-explorer.sh [--serve]" ;;
esac

docker info >/dev/null 2>&1 || die "Docker isn't available to $USER (are you in the docker group?)"

log "building $PA_EXPLORER_IMAGE"
docker build --quiet --tag "$PA_EXPLORER_IMAGE" "$DEPLOY_DIR/explorer" >/dev/null

# The explorer waits for the first build if there's none yet; the directory must exist to mount.
mkdir -p "$PA_GRAPH"

docker rm --force "$PA_EXPLORER" >/dev/null 2>&1 || true
# Nothing in the container is writable: not the graph, not the image, no tmpfs, no /dev/shm. So
# a query can't change a build, write a file, or install an extension. The buffer pool and query
# timeout make a runaway query fail fast instead of eating the devbox's memory.
docker run --detach --name "$PA_EXPLORER" --restart unless-stopped --init \
  --read-only --ipc none --cap-drop ALL --security-opt no-new-privileges \
  --user "$(id -u):$(id -g)" --memory 2g --pids-limit 256 \
  --publish "127.0.0.1:$PA_EXPLORER_PORT:8000" \
  --volume "$PA_GRAPH:/graph:ro" \
  --env LBUG_BUFFER_POOL_SIZE=$((512 << 20)) --env LBUG_QUERY_TIMEOUT=10000 \
  "$PA_EXPLORER_IMAGE" >/dev/null

url=$PA_EXPLORER_LOCAL_URL
if [[ -L $PA_GRAPH/current ]]; then
  for _ in $(seq 30); do
    curl -fsS -m 2 "$url/api/" >/dev/null 2>&1 && break
    sleep 1
  done
  curl -fsS -m 2 "$url/api/" >/dev/null || die "the explorer isn't answering at $url: docker logs $PA_EXPLORER"
  log "the explorer is serving $(readlink "$PA_GRAPH/current") at $url"
else
  log "the explorer is running, and waits for the first build (pa librarian rebuild)"
fi

if ((serve)); then
  # tailscale serve listens only on the devbox's tailnet addresses. Plain HTTP: the tailnet
  # already encrypts it, and HTTPS would need certificates turned on for the whole tailnet.
  serve_args=(serve --bg --yes --http="$PA_EXPLORER_PORT" "$url")
  tailscale "${serve_args[@]}" >/dev/null 2>&1 || sudo tailscale "${serve_args[@]}" >/dev/null
  explorer_served || die "tailscale serve doesn't forward port $PA_EXPLORER_PORT to $url"
  log "on the tailnet at $(explorer_tailnet_url)"
fi
