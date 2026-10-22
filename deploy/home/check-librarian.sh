#!/usr/bin/env bash
# Checks the librarian VM's fences from inside the VM, set up as a run sets it up: started by
# librarian-sandbox.sh over the instance's L0, the librarian project rendered for the owner, and
# the wiki, and stopped again at the end. It checks that:
#
# - the store is shared as a run's: L0 and the project read-only, even to root, the wiki writable,
#   and nothing else, and no git directory: neither a .git in the wiki nor a marker planted in
#   the wiki's git directory and the data root is anywhere in the VM;
# - the API is reachable and nothing else is: not the web by name (there's no DNS) or by address,
#   nor other ports in the API's range, the devbox, the assistant's VM, or the LAN;
# - Claude Code is the pinned release, with the repo's managed settings, both owned by root;
# - denied tools stay denied: a session with no flags, and one with every flag that tries to
#   reopen them (bypass mode, the shell and web tools named, an MCP server), each has exactly
#   the file tools, no MCP server, and no bypass mode. Both run on Haiku, briefly.
#
# Like check-isolation.sh, each target the VM must not reach is first probed from the devbox as a
# control, and a TCP probe counts as blocked only if it times out. Exits non-zero if any check
# fails.
set -euo pipefail
source "$(dirname "$0")/lib.sh"
select_vm librarian
require_owner_env

failed=0
pass() { printf '  PASS  %s\n' "$*"; }
fail() { printf '  FAIL  %s\n' "$*"; failed=1; }
skip() { printf '  skip  %s\n' "$*"; }

sandbox=$DEPLOY_DIR/librarian-sandbox.sh
in_vm() { incus exec "$PA_VM" -- "$@"; }
# in_vm_as USER COMMAND... runs COMMAND in the VM as USER, quietly; its status is the result.
in_vm_as() {
  local user=$1
  shift
  incus exec "$PA_VM" -- runuser -u "$user" -- "$@" >/dev/null 2>&1
}

tcp_from_devbox() { timeout 3 bash -c "exec 3<>/dev/tcp/$1/$2" 2>/dev/null; }

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
  ping -c1 -W2 "$ip" >/dev/null 2>&1 || { skip "$label ($ip): doesn't answer ping from the devbox either"; return; }
  if in_vm ping -c1 -W2 "$ip" >/dev/null 2>&1; then
    fail "$label ($ip) answers ping from the VM"
  else
    pass "$label ($ip) unreachable"
  fi
}

work=$(mktemp -d)
marker=pa-librarian-check-$RANDOM$RANDOM
markers=("$PA_DATA_ROOT/$marker")
[[ -d $PA_WIKI_GIT ]] && markers+=("$PA_WIKI_GIT/$marker")
# shellcheck disable=SC2329 # run by the trap
cleanup() {
  "$sandbox" stop || true
  rm -f "${markers[@]}"
  rm -rf "$work"
}
trap cleanup EXIT

echo "The librarian project renders for the owner:"
project=$work/project
if (
  set -a
  # shellcheck source=/dev/null # owner.env, written by seed-data-root.sh
  source "$PA_OWNER_ENV"
  set +a
  uv run --quiet --project "$REPO_DIR" pa librarian-project render "$project" \
    --l0 "$PA_LIB_VM_L0" --wiki "$PA_LIB_VM_WIKI"
); then
  pass "rendered into $project"
else
  fail "pa librarian-project render failed"
  exit 1
fi

echo "$PA_VM starts as a run starts it:"
for file in "${markers[@]}"; do : >"$file"; done
if "$sandbox" start "$PA_L0" "$project" "$PA_WIKI" >"$work/seen.json"; then
  pass "librarian-sandbox.sh start shares the store and starts $PA_VM"
else
  fail "librarian-sandbox.sh start failed (above)"
  exit 1
fi

echo "$PA_VM is shared L0 and the project read-only, the wiki writable, and nothing else:"
problems=$(incus query "/1.0/instances/$PA_VM" | share_problems \
  "lib-l0|$PA_L0|$PA_LIB_VM_L0|true" \
  "lib-project|$(realpath "$project")|$PA_LIB_VM_PROJECT|true" \
  "lib-wiki|$(realpath "$PA_WIKI")|$PA_LIB_VM_WIKI|false")
if [[ -z $problems ]]; then
  pass "the devbox shares L0, the project and the wiki as a run's, and nothing else"
else
  while read -r problem; do fail "$problem"; done <<<"$problems"
fi
expected=$(printf '%s\n' "$PA_LIB_VM_L0" "$PA_LIB_VM_PROJECT" "$PA_LIB_VM_WIKI" | sort)
mounts=$(in_vm findmnt -rn -t virtiofs -o TARGET | sort)
if [[ $mounts == "$expected" ]]; then
  pass "the VM mounts only $(paste -sd ' ' <<<"$expected")"
else
  fail "the VM mounts other shares:"$'\n'"$mounts"
fi
for path in "$PA_LIB_VM_L0" "$PA_LIB_VM_PROJECT"; do
  for user in "$PA_USER" root; do
    if in_vm_as "$user" touch "$path/.write-check"; then
      fail "$user created a file in $path"
      in_vm rm -f "$path/.write-check"
    else
      pass "$user can't create a file in $path"
    fi
  done
done
episode=$(in_vm find "$PA_LIB_VM_L0/episodes" -name '*.json' -print -quit 2>/dev/null || true)
if [[ -n $episode ]] && in_vm_as "$PA_USER" cat "$episode"; then
  pass "$PA_USER reads episodes under $PA_LIB_VM_L0"
else
  fail "$PA_USER can't read an episode under $PA_LIB_VM_L0"
fi
if in_vm_as "$PA_USER" cat "$PA_LIB_VM_PROJECT/CLAUDE.md"; then
  pass "$PA_USER reads the project's CLAUDE.md"
else
  fail "$PA_USER can't read $PA_LIB_VM_PROJECT/CLAUDE.md"
fi
probe=.librarian-check-$$
if in_vm_as "$PA_USER" touch "$PA_LIB_VM_WIKI/$probe" && [[ -e $PA_WIKI/$probe ]]; then
  pass "$PA_USER's file in $PA_LIB_VM_WIKI lands in $PA_WIKI"
else
  fail "$PA_USER can't write the wiki, or it doesn't reach the devbox"
fi
rm -f "$PA_WIKI/$probe"

echo "No git directory is visible in $PA_VM:"
if [[ -e $PA_WIKI/.git ]] || in_vm test -e "$PA_LIB_VM_WIKI/.git"; then
  fail "the wiki holds a .git"
else
  pass "the wiki holds no .git"
fi
found=$(in_vm find / \( -path /proc -o -path /sys -o -path /dev -o -path /run \) -prune \
  -o -name "$marker" -print 2>/dev/null || true)
if [[ -z $found ]]; then
  pass "nothing planted in $(printf '%s ' "${markers[@]%/*}")is visible anywhere in the VM"
else
  fail "the VM sees the devbox's marker at: $found"
fi
if in_vm sh -c 'command -v tailscale' >/dev/null 2>&1; then
  fail "tailscale is installed in $PA_VM"
else
  pass "no tailscale in $PA_VM"
fi

echo "From $PA_VM, the Anthropic API is reachable:"
api=$(in_vm getent hosts "$PA_API_HOST" | awk '{print $1}')
if [[ -n $api && -n $(in_api_range <<<"$api") ]]; then
  pass "$PA_API_HOST is $api, in $PA_API_RANGE"
else
  fail "$PA_API_HOST doesn't resolve in the VM to an address in $PA_API_RANGE (got '${api:-nothing}')"
fi
if [[ -n $api ]] && in_vm timeout 5 bash -c "exec 3<>/dev/tcp/$api/443" 2>/dev/null; then
  pass "$api:443 reachable"
else
  fail "$PA_API_HOST:443 unreachable from the VM (has the API's range moved?)"
fi

echo "From $PA_VM, nothing else is:"
if in_vm getent hosts example.com >/dev/null 2>&1; then
  fail "example.com resolves in the VM: it has DNS"
else
  pass "example.com doesn't resolve: no DNS"
fi
web=$(getent ahostsv4 example.com | awk 'NR == 1 {print $1}')
if [[ -n $web ]]; then
  expect_tcp_blocked "example.com by address" "$web" 443
else
  skip "example.com doesn't resolve on the devbox either"
fi
[[ -n $api ]] && expect_tcp_blocked "the API's address on another port" "$api" 80
bridge_ip=$(incus network get "$PA_BRIDGE" ipv4.address); bridge_ip=${bridge_ip%/*}
# Nothing to probe: the bridge's dnsmasq doesn't listen on port 53 at all.
if ss -Hlnut "sport = :53" | awk '{print $5}' | grep -q "^$bridge_ip:"; then
  fail "something listens for DNS on the bridge ($bridge_ip:53)"
else
  pass "nothing listens for DNS on the bridge ($bridge_ip:53)"
fi
expect_tcp_blocked "devbox SSH via the bridge" "$bridge_ip" 22
lan_ip=$(ip -4 route get 1.1.1.1 | grep -o 'src [0-9.]*' | cut -d' ' -f2)
expect_tcp_blocked "devbox SSH via its LAN address" "$lan_ip" 22
expect_ping_blocked "LAN gateway" "$(ip -4 route show default | awk '{print $3; exit}')"
assistant_ip=$(incus list "^$PA_ASSISTANT_VM\$" -c 4 -f csv | grep -o '^[0-9.]*' || true)
if [[ -n $assistant_ip ]]; then
  expect_ping_blocked "the assistant's VM" "$assistant_ip"
else
  skip "the assistant's VM has no address on its bridge"
fi
if [[ -z $(in_vm ip -6 route show default) ]]; then
  pass "no IPv6 route out"
else
  fail "the VM has an IPv6 default route"
fi

echo "In $PA_VM, Claude Code is the pinned release, with the repo's managed settings:"
installed=$(in_vm runuser -u "$PA_USER" -- "$PA_LIB_VM_CLAUDE" --version 2>/dev/null || true)
if [[ $installed == "$PA_LIBRARIAN_CLAUDE_VERSION "* ]]; then
  pass "Claude Code $PA_LIBRARIAN_CLAUDE_VERSION"
else
  fail "Claude Code is '${installed:-missing}', not $PA_LIBRARIAN_CLAUDE_VERSION (run update.sh --vm librarian)"
fi
for entry in "$PA_LIB_VM_CLAUDE|755" "$PA_LIB_VM_MANAGED_SETTINGS|644"; do
  IFS='|' read -r file mode <<<"$entry"
  owner=$(in_vm stat -c '%U %a' "$file" 2>/dev/null || true)
  if [[ $owner == "root $mode" ]]; then
    pass "$file is root's, mode $mode"
  else
    fail "$file is '${owner:-missing}', not root's with mode $mode"
  fi
done
if [[ $(in_vm sha256sum "$PA_LIB_VM_MANAGED_SETTINGS" 2>/dev/null | cut -d' ' -f1) == \
  "$(sha256sum "$PA_LIBRARIAN_MANAGED_SETTINGS" | cut -d' ' -f1)" ]]; then
  pass "$PA_LIB_VM_MANAGED_SETTINGS matches the repo's"
else
  fail "$PA_LIB_VM_MANAGED_SETTINGS differs from the repo's (run update.sh --vm librarian)"
fi

echo "In $PA_VM, denied tools stay denied:"
# check_session LABEL FLAG... runs a short session with FLAGs and checks its fences.
check_session() {
  local label=$1 output problems
  shift
  output=$(echo "Reply with the word OK." | "$sandbox" claude -p --output-format stream-json \
    --verbose --model claude-haiku-5-5 --max-turns 2 "$@" 2>"$work/session.err") || true
  if ! problems=$(session_fence_problems <<<"$output" 2>&1); then
    fail "$label: $problems $(head -c 300 "$work/session.err")"
  elif [[ -n $problems ]]; then
    while read -r problem; do fail "$label: $problem"; done <<<"$problems"
  else
    pass "$label: exactly ${PA_LIBRARIAN_TOOLS[*]}, no MCP server, no bypass mode"
  fi
}
check_session "a session with no flags"
denied=Bash,PowerShell,Monitor,WebFetch,WebSearch,Agent,Skill,RemoteTrigger
check_session "a session asking for bypass mode, the shell, the web and MCP" \
  --dangerously-skip-permissions --permission-mode bypassPermissions \
  --tools "$(IFS=,; echo "${PA_LIBRARIAN_TOOLS[*]}"),$denied" --allowedTools "$denied,mcp__stub" \
  --mcp-config '{"mcpServers": {"stub": {"type": "stdio", "command": "/bin/true"}}}'
if in_vm test -e "/home/$PA_USER/.claude/.credentials.json"; then
  fail "the setup token was saved in the VM"
else
  pass "the setup token isn't saved in the VM"
fi

exit "$failed"
