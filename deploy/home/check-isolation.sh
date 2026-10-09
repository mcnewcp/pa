#!/usr/bin/env bash
# Checks the PA VM's fences from inside the VM. Each target the VM must not reach is first
# probed from the devbox as a control: a target the devbox can't reach either is skipped, since
# failing to reach it from the VM proves nothing. A TCP probe counts as blocked only if it times
# out; a refusal means a packet got there. Exits non-zero if any check fails.
set -euo pipefail
source "$(dirname "$0")/lib.sh"

failed=0
pass() { printf '  PASS  %s\n' "$*"; }
fail() { printf '  FAIL  %s\n' "$*"; failed=1; }
skip() { printf '  skip  %s\n' "$*"; }

tcp_from_devbox() { timeout 3 bash -c "exec 3<>/dev/tcp/$1/$2" 2>/dev/null; }
ping_from_devbox() { ping -c1 -W2 "$1" >/dev/null 2>&1; }

# expect_tcp_blocked LABEL IP PORT
expect_tcp_blocked() {
  local label=$1 ip=$2 port=$3 status=0
  tcp_from_devbox "$ip" "$port" || { skip "$label ($ip:$port): not reachable from the devbox either"; return; }
  incus exec "$PA_VM" -- timeout 3 bash -c "exec 3<>/dev/tcp/$ip/$port" 2>/dev/null || status=$?
  case $status in
    124) pass "$label ($ip:$port) unreachable" ;;
    0) fail "$label ($ip:$port) reachable from the VM" ;;
    *) fail "$label ($ip:$port) answered the VM (refused or unreachable, not dropped)" ;;
  esac
}

# expect_ping_blocked LABEL IP
expect_ping_blocked() {
  local label=$1 ip=$2
  ping_from_devbox "$ip" || { skip "$label ($ip): doesn't answer ping from the devbox either"; return; }
  if incus exec "$PA_VM" -- ping -c1 -W2 "$ip" >/dev/null 2>&1; then
    fail "$label ($ip) answers ping from the VM"
  else
    pass "$label ($ip) unreachable"
  fi
}

# expect_https LABEL URL
expect_https() {
  if incus exec "$PA_VM" -- curl -sS -o /dev/null -m 10 "$2"; then
    pass "$1 ($2) reachable"
  else
    fail "$1 ($2) unreachable from the VM"
  fi
}

echo "From $PA_VM, the devbox is unreachable:"
bridge_ip=$(incus network get "$PA_BRIDGE" ipv4.address); bridge_ip=${bridge_ip%/*}
lan_ip=$(ip -4 route get 1.1.1.1 | grep -o 'src [0-9.]*' | cut -d' ' -f2)
expect_tcp_blocked "devbox SSH via the bridge" "$bridge_ip" 22
expect_tcp_blocked "devbox SSH via its LAN address" "$lan_ip" 22
if command -v tailscale >/dev/null; then
  expect_tcp_blocked "devbox SSH via its tailnet address" "$(tailscale ip -4)" 22
fi

echo "From $PA_VM, the devbox's Docker services are unreachable:"
if command -v docker >/dev/null && docker info >/dev/null 2>&1; then
  found=0
  for id in $(docker ps -q); do
    name=$(docker inspect -f '{{.Name}}' "$id"); name=${name#/}
    ports=$(docker inspect -f '{{range $p, $_ := .Config.ExposedPorts}}{{$p}} {{end}}' "$id")
    for ip in $(docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}} {{end}}' "$id"); do
      for port in $ports; do
        [[ $port == */tcp ]] || continue
        expect_tcp_blocked "container $name" "$ip" "${port%/tcp}"
        found=1
      done
    done
  done
  ((found)) || skip "no running container exposes a TCP port"
else
  skip "Docker isn't available to this user"
fi

echo "From $PA_VM, the LAN is unreachable:"
expect_ping_blocked "LAN gateway" "$(ip -4 route show default | awk '{print $3; exit}')"

echo "From $PA_VM, tailnet peers are unreachable:"
if command -v tailscale >/dev/null; then
  while read -r ip host; do
    expect_ping_blocked "tailnet peer $host" "$ip"
  done < <(tailscale status | awk -v vm="$PA_VM" '$1 ~ /^100\./ && $2 != vm && !/offline/ {print $1, $2}')
else
  skip "tailscale isn't installed on the devbox"
fi

echo "From $PA_VM, IPv6 goes nowhere:"
if incus exec "$PA_VM" -- curl -6 -sS -o /dev/null -m 5 https://example.com 2>/dev/null; then
  fail "the VM reaches the internet over IPv6"
else
  pass "no IPv6 route out"
fi

echo "From $PA_VM, the internet is reachable:"
expect_https "public internet" https://example.com
expect_https "Anthropic API" https://api.anthropic.com

exit "$failed"
