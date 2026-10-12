#!/usr/bin/env bash
# Checks Ladybug Explorer on the devbox: it serves the current build, refuses /api/db and
# /api/gpt, can't change a build, listens only on localhost and, through `tailscale serve`, on
# the tailnet, and the PA VM can't reach it at any of the devbox's addresses. Reaching it from a
# tailnet device is the owner's to check (the wizard asks). Exits non-zero if any check fails.
set -euo pipefail
source "$(dirname "$0")/lib.sh"

failed=0
pass() { printf '  PASS  %s\n' "$*"; }
fail() { printf '  FAIL  %s\n' "$*"; failed=1; }
skip() { printf '  skip  %s\n' "$*"; }

url=$PA_EXPLORER_LOCAL_URL
port=$PA_EXPLORER_PORT

# status METHOD PATH prints the HTTP status the explorer answers with.
status() { curl -s -o /dev/null -m 10 -w '%{http_code}' -X "$1" -H 'Content-Type: application/json' -d '{}' "$url$2"; }

# cypher QUERY prints the explorer's answer to QUERY.
cypher() {
  curl -s -m 20 -H 'Content-Type: application/json' \
    -d "$(python3 -c 'import json, sys; print(json.dumps({"query": sys.argv[1]}))' "$1")" "$url/api/cypher"
}

# graph_state prints a line per file under the graph directory, with its checksum and mode.
graph_state() {
  find "$PA_GRAPH" -printf '%P %y %m\n' | sort
  find "$PA_GRAPH" -type f -exec sha256sum {} + | sort
}

# served_build prints the build the explorer's server was last started on.
served_build() {
  docker logs "$PA_EXPLORER" 2>&1 | sed -n 's/^follow-current: serving //p' | tail -n 1
}

echo "The explorer is running and serves the current build:"
[[ -L $PA_GRAPH/current ]] || { fail "$PA_GRAPH has no published build: run pa librarian rebuild"; exit 1; }
state=$(curl -fsS -m 5 "$url/api/" 2>/dev/null) || { fail "nothing answers at $url (install-explorer.sh)"; exit 1; }
pinned=$(sed -n 's/.*"ladybug==\([^"]*\)".*/\1/p' "$REPO_DIR/adapters/home/pyproject.toml")
if [[ $state == *'"mode":"READ_ONLY"'* && $state == *"\"version\":\"$pinned\""* ]]; then
  pass "$url answers, read-only, on LadybugDB $pinned"
else
  fail "$url answers $state; expected READ_ONLY on LadybugDB $pinned"
fi
current=$(readlink "$PA_GRAPH/current")
# follow-current reads `current` every 2 s, and the server takes a moment to open the build.
for _ in $(seq 15); do
  [[ $(served_build) == "$current" ]] && curl -fsS -m 2 "$url/api/" >/dev/null 2>&1 && break
  sleep 1
done
if [[ $(served_build) == "$current" ]]; then
  pass "it serves $current, the current build"
else
  fail "it serves $(served_build), but current is $current"
fi

echo "The explorer refuses /api/db and /api/gpt:"
for path in /api/db /api/gpt; do
  for method in GET POST; do
    code=$(status "$method" "$path")
    if [[ $code == 404 ]]; then pass "$method $path: $code"; else fail "$method $path: $code"; fi
  done
done

echo "The explorer can't change a build:"
before=$(graph_state)
answer=$(cypher "CREATE (:Episode {id: 'explorer-write-check'})")
if [[ $answer == *'"error"'* ]]; then pass "CREATE is refused"; else fail "CREATE answered $answer"; fi
answer=$(cypher 'CHECKPOINT')
if [[ $answer == *'"error"'* ]]; then pass "CHECKPOINT is refused"; else fail "CHECKPOINT answered $answer"; fi
answer=$(cypher "COPY (MATCH (e:Episode) RETURN e.id) TO '/graph/explorer-write-check.csv'")
if [[ $answer == *'"error"'* ]]; then pass "COPY TO the graph is refused"; else fail "COPY TO answered $answer"; fi
answer=$(cypher "COPY (MATCH (e:Episode) RETURN e.id) TO '/tmp/explorer-write-check.csv'")
if [[ $answer == *'"error"'* ]]; then pass "COPY TO its own disk is refused"; else fail "COPY TO /tmp answered $answer"; fi
answer=$(cypher 'INSTALL json')
if [[ $answer == *'"error"'* ]]; then pass "INSTALL is refused"; else fail "INSTALL answered $answer"; fi
if [[ $(graph_state) == "$before" ]]; then
  pass "the graph directory is unchanged, byte for byte"
else
  fail "the graph directory changed: diff <(echo \"\$before\") <(graph_state)"
fi

echo "The explorer listens only on localhost and the tailnet:"
published=$(docker port "$PA_EXPLORER" 8000/tcp)
if [[ $published == "127.0.0.1:$port" ]]; then
  pass "Docker publishes it on 127.0.0.1:$port only"
else
  fail "Docker publishes it on: ${published//$'\n'/, }"
fi
lan_ip=$(ip -4 route get 1.1.1.1 | grep -o 'src [0-9.]*' | cut -d' ' -f2)
if curl -s -o /dev/null -m 5 "http://$lan_ip:$port/"; then
  fail "it answers on the devbox's LAN address ($lan_ip:$port)"
else
  pass "nothing answers on the devbox's LAN address ($lan_ip:$port)"
fi
if ! command -v tailscale >/dev/null; then
  fail "tailscale isn't installed on the devbox"
elif explorer_served; then
  pass "tailscale serve forwards $(explorer_tailnet_url) to it"
else
  fail "tailscale serve doesn't forward port $port to $url (install-explorer.sh --serve)"
fi

echo "From $PA_VM, the explorer is unreachable:"
if incus info "$PA_VM" >/dev/null 2>&1; then
  bridge_ip=$(incus network get "$PA_BRIDGE" ipv4.address); bridge_ip=${bridge_ip%/*}
  targets=("devbox via the bridge:$bridge_ip" "devbox's LAN address:$lan_ip")
  command -v tailscale >/dev/null && targets+=("devbox's tailnet address:$(tailscale ip -4)")
  for target in "${targets[@]}"; do
    label=${target%:*} ip=${target##*:} code=0
    incus exec "$PA_VM" -- timeout 3 bash -c "exec 3<>/dev/tcp/$ip/$port" 2>/dev/null || code=$?
    case $code in
      124) pass "$label ($ip:$port) unreachable" ;;
      0) fail "$label ($ip:$port) reachable from the VM" ;;
      *) fail "$label ($ip:$port) answered the VM (refused or unreachable, not dropped)" ;;
    esac
  done
else
  skip "$PA_VM doesn't exist"
fi

exit "$failed"
