#!/usr/bin/env bash
# Checks the assistant VM's fences from inside the VM:
#
# - the network (#91): its NIC on its own bridge behind the repo's egress ACL, and no DNS on the
#   bridge; the devbox, its containers, the LAN, tailnet peers, the web (by name or address) and
#   IPv6 unreachable; no Tailscale in the VM; and the API's pinned names, in its range, answering.
#   Each target the VM must not reach is first probed from the devbox as a control: a target the
#   devbox can't reach either is skipped, since failing to reach it from the VM proves nothing. A
#   TCP probe counts as blocked only if it times out; a refusal means a packet got there;
# - nothing that knocks on the fence: the OS's own updates and clock sync masked, and Claude Code
#   the pinned release, root's, with no copy of the assistant's own left;
# - the managed settings: the repo's, root's and mode 0644, with the assistant's user settings a
#   root-owned {} (#92);
# - the read-only project: the agent project, its hook, the code and the interpreter the hook
#   runs with are root's, and the assistant can't write them;
# - the tools: a session with no flags, and one with every flag that tries to reopen them, each
#   has exactly the assistant's tools, no MCP server, and no bypass mode;
# - the sandbox, by having a session run a command in it: no new privileges, scratch writable,
#   the home directory, the inbox, the project and the evals' directory not, and no sudo;
# - no auto memory directory left.
#
# The sessions run on Haiku, briefly, on the assistant's login. A Remote Control session's tools
# can't be read headlessly: the owner looks once from the phone. Exits non-zero if any check fails.
set -euo pipefail
source "$(dirname "$0")/lib.sh"

failed=0
pass() { printf '  PASS  %s\n' "$*"; }
fail() { printf '  FAIL  %s\n' "$*"; failed=1; }
skip() { printf '  skip  %s\n' "$*"; }

in_vm() { incus exec "$PA_VM" -- "$@" </dev/null; }
tcp_from_devbox() { timeout 3 bash -c "exec 3<>/dev/tcp/$1/$2" 2>/dev/null; }
ping_from_devbox() { ping -c1 -W2 "$1" >/dev/null 2>&1; }

# expect_tcp_blocked LABEL IP PORT
expect_tcp_blocked() {
  local label=$1 ip=$2 port=$3 status=0
  tcp_from_devbox "$ip" "$port" || { skip "$label ($ip:$port): not reachable from the devbox either"; return; }
  in_vm timeout 3 bash -c "exec 3<>/dev/tcp/$ip/$port" 2>/dev/null || status=$?
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
  if in_vm ping -c1 -W2 "$ip" >/dev/null 2>&1; then
    fail "$label ($ip) answers ping from the VM"
  else
    pass "$label ($ip) unreachable"
  fi
}

echo "$PA_VM is fenced: its NIC on $PA_BRIDGE behind $PA_ACL, and no DNS on the bridge:"
if ! problems=$(incus query "/1.0/instances/$PA_VM" | vm_fence_problems); then
  fail "can't read $PA_VM's devices"
elif [[ -n $problems ]]; then
  while read -r problem; do fail "$PA_VM: $problem (run fence-vm.sh)"; done <<<"$problems"
else
  pass "its only NIC is on $PA_BRIDGE, behind $PA_ACL, dropping all else both ways"
fi
if incus query "/1.0/network-acls/$PA_ACL" | acl_differs "$PA_ACL_FILE"; then
  fail "the ACL $PA_ACL differs from the repo's $PA_ACL_FILE (run incus-setup.sh)"
else
  pass "the ACL $PA_ACL is the repo's: only TCP 443 to $PA_API_RANGE"
fi
bridge_ip=$(incus network get "$PA_BRIDGE" ipv4.address); bridge_ip=${bridge_ip%/*}
# Nothing to probe: the bridge's dnsmasq doesn't listen on port 53 at all.
if ss -Hlnut "sport = :53" | awk '{print $5}' | grep -q "^$bridge_ip:"; then
  fail "something listens for DNS on the bridge ($bridge_ip:53) (run incus-setup.sh)"
else
  pass "nothing listens for DNS on the bridge ($bridge_ip:53)"
fi

echo "From $PA_VM, the devbox is unreachable:"
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

echo "$PA_VM has no Tailscale, and tailnet peers are unreachable:"
if in_vm sh -c 'command -v tailscale' >/dev/null 2>&1; then
  fail "tailscale is installed in $PA_VM (run fence-vm.sh)"
else
  pass "no tailscale in $PA_VM"
fi
if in_vm ip link show tailscale0 >/dev/null 2>&1; then
  fail "$PA_VM has a tailscale0 interface"
else
  pass "no tailscale0 interface in $PA_VM"
fi
if command -v tailscale >/dev/null; then
  while read -r ip host; do
    expect_ping_blocked "tailnet peer $host" "$ip"
  done < <(tailscale status | awk -v vm="$PA_VM" '$1 ~ /^100\./ && $2 != vm && !/offline/ {print $1, $2}')
else
  skip "tailscale isn't installed on the devbox"
fi

echo "From $PA_VM, the web is unreachable:"
if in_vm getent hosts example.com >/dev/null 2>&1; then
  fail "example.com resolves in the VM: it has DNS"
else
  pass "example.com doesn't resolve: no DNS"
fi
for site in example.com wikipedia.org; do
  web=$(getent ahostsv4 "$site" | awk 'NR == 1 {print $1}')
  if [[ -n $web ]]; then
    expect_tcp_blocked "$site by address" "$web" 443
    expect_tcp_blocked "$site by address" "$web" 80
  else
    skip "$site doesn't resolve on the devbox either"
  fi
done
if [[ -z $(in_vm ip -6 route show default) ]]; then
  pass "no IPv6 route out"
else
  fail "the VM has an IPv6 default route"
fi

echo "From $PA_VM, the Anthropic API is reachable, at its pinned names:"
api=""
for host in "${PA_VM_PINNED_HOSTS[@]}"; do
  address=$(in_vm getent hosts "$host" | awk 'NR == 1 {print $1}')
  if [[ -n $address && -n $(in_api_range <<<"$address") ]]; then
    pass "$host is pinned at $address, in $PA_API_RANGE"
    api=${api:-$address}
  else
    fail "$host isn't pinned in the VM at an address in $PA_API_RANGE (got '${address:-nothing}'; run pin-hosts.sh)"
  fi
  # Any HTTP answer will do: the request reached the host and TLS checked out for its name.
  if in_vm curl -sS -o /dev/null -m 10 "https://$host" 2>/dev/null; then
    pass "https://$host answers"
  else
    fail "https://$host doesn't answer the VM (has the API's range moved?)"
  fi
done
[[ -n $api ]] && expect_tcp_blocked "the API's address on another port" "$api" 80

echo "In $PA_VM, nothing knocks on the fence:"
loose=()
for unit in "${PA_VM_QUIET_UNITS[@]}"; do
  [[ $(in_vm systemctl is-enabled "$unit" 2>/dev/null) == masked ]] || loose+=("$unit")
done
if ((${#loose[@]} == 0)); then
  pass "the OS's own updates, clock sync and news timers are masked"
else
  fail "not masked: ${loose[*]} (run update.sh)"
fi
if in_vm test -f "$PA_VM_APT_NO_PERIODIC"; then
  pass "apt's periodic runs are off ($PA_VM_APT_NO_PERIODIC)"
else
  fail "$PA_VM_APT_NO_PERIODIC is missing (run update.sh)"
fi
installed=$(in_vm runuser -u "$PA_USER" -- "$PA_VM_CLAUDE" --version 2>/dev/null || true)
if [[ $installed == "$PA_ASSISTANT_CLAUDE_VERSION "* ]]; then
  pass "Claude Code $PA_ASSISTANT_CLAUDE_VERSION, the pinned release"
else
  fail "Claude Code is '${installed:-missing}', not $PA_ASSISTANT_CLAUDE_VERSION (run update.sh)"
fi
owner=$(in_vm stat -c '%U %a' "$PA_VM_CLAUDE" 2>/dev/null || true)
if [[ $owner == "root 755" ]]; then
  pass "$PA_VM_CLAUDE is root's, mode 755"
else
  fail "$PA_VM_CLAUDE is '${owner:-missing}', not root's with mode 755 (run update.sh)"
fi
if in_vm test -e "${PA_VM_OLD_CLAUDE[0]}"; then
  fail "the assistant's own Claude Code is still at ${PA_VM_OLD_CLAUDE[0]} (run update.sh)"
else
  pass "no Claude Code of the assistant's own"
fi

# as_assistant_quietly COMMAND... runs COMMAND in the VM as the assistant; its status is the result.
as_assistant_quietly() { incus exec "$PA_VM" -- runuser -u "$PA_USER" -- "$@" >/dev/null 2>&1; }
# assistant_claude ARG... runs Claude Code in the VM as the assistant, in the agent project, as a
# session there starts (without Remote Control's capture), with stdin as its prompt.
assistant_claude() {
  incus exec "$PA_VM" -- runuser -l "$PA_USER" -c \
    "cd $(printf %q "$PA_VM_PROJECT") && $(printf '%q ' "$PA_VM_CLAUDE" "$@")"
}
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

echo "In $PA_VM, the managed settings are the repo's, root's, and the user settings an empty root's:"
owner=$(in_vm stat -c '%U %a' "$PA_VM_MANAGED_SETTINGS" 2>/dev/null || true)
if [[ $owner == "root 644" ]]; then
  pass "$PA_VM_MANAGED_SETTINGS is root's, mode 644"
else
  fail "$PA_VM_MANAGED_SETTINGS is '${owner:-missing}', not root's with mode 644 (run update.sh)"
fi
if [[ $(in_vm sha256sum "$PA_VM_MANAGED_SETTINGS" 2>/dev/null | cut -d' ' -f1) == \
  "$(sha256sum "$PA_ASSISTANT_MANAGED_SETTINGS" | cut -d' ' -f1)" ]]; then
  pass "$PA_VM_MANAGED_SETTINGS matches the repo's"
else
  fail "$PA_VM_MANAGED_SETTINGS differs from the repo's (run update.sh)"
fi
owner=$(in_vm stat -c '%U %a %F' "$PA_VM_USER_SETTINGS" 2>/dev/null || true)
if [[ $owner == "root 644 regular file" && $(in_vm cat "$PA_VM_USER_SETTINGS") == "{}" ]]; then
  pass "$PA_VM_USER_SETTINGS is {}, root's"
else
  fail "$PA_VM_USER_SETTINGS isn't a root-owned {} ('${owner:-missing}'; run update.sh)"
fi

echo "In $PA_VM, the agent project, its hook and their interpreter are read-only to $PA_USER:"
hook_python=$(in_vm realpath -e "$PA_VM_HOOK_PYTHON" 2>/dev/null || true)
read_only=("$PA_VM_PROJECT" "$PA_REPO_DIR" "$PA_VM_PYTHON_DIR" "$PA_VM_OWNER_ENV" "${hook_python:-}")
for path in "${read_only[@]}"; do
  [[ -n $path ]] || { fail "$PA_VM_HOOK_PYTHON doesn't resolve to an interpreter"; continue; }
  in_vm test -e "$path" || { skip "$path doesn't exist"; continue; }
  # Anything not root's, or writable by group or others; links are never writable themselves.
  loose=$(in_vm find "$path" \( ! -user root -o \( ! -type l -perm /022 \) \) -print -quit)
  if [[ -z $loose ]]; then
    pass "everything in $path is root's and writable by root alone"
  else
    fail "$path holds $loose, which isn't root's or which others can write"
  fi
done
hook=$PA_VM_PROJECT/.claude/hooks/capture_exchange.py
for path in "$PA_VM_PROJECT/CLAUDE.md" "$PA_VM_PROJECT/.claude/settings.json" "$hook" \
  "${hook_python:-$PA_VM_HOOK_PYTHON}" "$PA_VM_MANAGED_SETTINGS" "$PA_VM_USER_SETTINGS"; do
  if ! in_vm test -f "$path"; then
    fail "$path is missing (run update.sh)"
  elif as_assistant_quietly test -w "$path"; then
    fail "$PA_USER can write $path"
  else
    pass "$PA_USER can't write $path"
  fi
done
for dir in "$PA_VM_PROJECT" "$PA_VM_PROJECT/.claude/hooks"; do
  if as_assistant_quietly touch "$dir/.pa-write-check"; then
    fail "$PA_USER created a file in $dir"
    in_vm rm -f "$dir/.pa-write-check"
  else
    pass "$PA_USER can't create a file in $dir"
  fi
done
owner=$(in_vm stat -c '%U %a' "$PA_VM_SCRATCH" 2>/dev/null || true)
if [[ $owner == "$PA_USER 700" ]]; then
  pass "$PA_VM_SCRATCH is $PA_USER's alone"
else
  fail "$PA_VM_SCRATCH is '${owner:-missing}', not $PA_USER's with mode 700 (run update.sh)"
fi

echo "In $PA_VM, a session has only the assistant's tools, whatever its flags:"
# check_session LABEL FLAG... runs a short session with FLAGs and checks its fences.
check_session() {
  local label=$1 output problems
  shift
  output=$(echo "Reply with the word OK." | assistant_claude -p --output-format stream-json \
    --verbose --no-session-persistence --model claude-haiku-5-5 --max-turns 2 "$@" \
    2>"$work/session.err") || true
  if ! problems=$(session_fence_problems "${PA_ASSISTANT_TOOLS[@]}" <<<"$output" 2>&1); then
    fail "$label: $problems $(head -c 300 "$work/session.err")"
  elif [[ -n $problems ]]; then
    while read -r problem; do fail "$label: $problem"; done <<<"$problems"
  else
    pass "$label: exactly ${PA_ASSISTANT_TOOLS[*]}, no MCP server, no bypass mode"
  fi
}
check_session "a session with no flags"
denied=WebFetch,WebSearch,Monitor,Agent,Skill,RemoteTrigger,CronCreate,ScheduleWakeup,SendUserFile
check_session "a session asking for bypass mode, the web, scheduling and MCP" \
  --dangerously-skip-permissions --permission-mode bypassPermissions \
  --tools "$(IFS=,; echo "${PA_ASSISTANT_TOOLS[*]}"),$denied" --allowedTools "$denied,mcp__stub" \
  --mcp-config '{"mcpServers": {"stub": {"type": "stdio", "command": "/bin/true"}}}'

echo "In $PA_VM, Bash runs in the sandbox, writing only to scratch:"
# Run by a session, since the sandbox is Claude Code's: a broken one still lets a session start,
# and fails every command (#81).
probe_dirs=("$PA_VM_SCRATCH" "$PA_VM_HOME" "$PA_VM_HOME/.claude" "$PA_VM_CHAT_INBOX" \
  "$PA_VM_PROJECT" "$PA_VM_EVAL")
probe=$(sandbox_probe "${probe_dirs[@]}")
output=$(printf 'Run this exact command with the Bash tool, once, unchanged, then reply DONE:\n\n%s\n' \
  "$probe" | assistant_claude -p --output-format stream-json --verbose --no-session-persistence \
  --model claude-haiku-5-5 --max-turns 3 2>"$work/probe.err") || true
if ! problems=$(sandbox_probe_problems "${probe_dirs[@]}" <<<"$output" 2>&1); then
  fail "the sandbox probe: $problems $(head -c 300 "$work/probe.err")"
elif [[ -n $problems ]]; then
  while read -r problem; do fail "$problem"; done <<<"$problems"
else
  pass "Bash ran sandboxed, with no new privileges: it wrote scratch, and not $(
    IFS=,; echo "${probe_dirs[*]:1}"), and sudo failed"
fi

echo "In $PA_VM, no auto memory is left, and these sessions wrote none:"
memory=$(in_vm find "$PA_VM_HOME/.claude/projects" -mindepth 2 -maxdepth 2 -name memory \
  2>/dev/null || true)
if [[ -z $memory ]]; then
  pass "no memory directory under $PA_VM_HOME/.claude/projects"
else
  fail "auto memory directories remain: $(paste -sd ' ' <<<"$memory")"
fi

exit "$failed"
