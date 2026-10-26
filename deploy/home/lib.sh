# Shared names and helpers for the deploy scripts. Source it; don't run it.
# The names are used by the scripts that source this; sudo incus means the binary.
# shellcheck shell=bash disable=SC2034,SC2032,SC2033

# The devbox runs two VMs: the assistant's (pa-home) and the librarian's (pa-librarian). PA_VM,
# PA_PROFILE, PA_BRIDGE and PA_USER name the one a script works on: the assistant's, unless the
# script takes --vm and is given another (take_vm_option).
PA_ASSISTANT_VM=pa-home
PA_ASSISTANT_PROFILE=pa
PA_ASSISTANT_BRIDGE=pabr0
PA_ASSISTANT_USER=assistant
PA_VM=$PA_ASSISTANT_VM
PA_PROFILE=$PA_ASSISTANT_PROFILE
PA_POOL=pa
PA_POOL_SIZE=120GiB
PA_BRIDGE=$PA_ASSISTANT_BRIDGE
PA_IMAGE=images:ubuntu/26.04/cloud
PA_REPO_URL=https://github.com/mcnewcp/pa.git
PA_USER=$PA_ASSISTANT_USER
PA_VM_ROLE=assistant
PA_REPO_DIR=/home/assistant/pa

# The librarian's VM: no Tailscale, a bridge of its own with no DNS (its dnsmasq serves DHCP
# only), and an Incus ACL on its NIC that lets out TCP 443 to the Anthropic API's published
# inbound range and drops everything else, both ways (docs/research/librarian-sandbox-spike.md).
PA_LIBRARIAN_VM=pa-librarian
PA_LIBRARIAN_PROFILE=pa-librarian
PA_LIBRARIAN_BRIDGE=palib0
PA_LIBRARIAN_ACL=pa-librarian-egress
PA_LIBRARIAN_USER=librarian
# https://platform.claude.com/docs/en/api/ip-addresses. The check fails if it ever moves.
PA_API_HOST=api.anthropic.com
PA_API_RANGE=160.79.104.0/23
# Claude Code in the librarian's VM: a pinned release, downloaded and verified on the devbox (its
# manifest's signature, by Anthropic's key, and the binary's checksum) and pushed in by
# update.sh --vm librarian. Bump it deliberately: a new release can add tools.
PA_LIBRARIAN_CLAUDE_VERSION=2.1.304
PA_CLAUDE_RELEASES=https://downloads.claude.ai/claude-code-releases
PA_CLAUDE_KEY_URL=https://downloads.claude.ai/keys/claude-code.asc
PA_CLAUDE_KEY_FINGERPRINT=31DDDE24DDFAB679F42D7BD2BAA929FF1A7ECACE
PA_CLAUDE_CACHE=$HOME/.cache/pa/claude-code
PA_LIB_VM_CLAUDE=/usr/local/bin/claude
# Its managed settings: every tool but the file tools denied, bypass mode locked out, and memory,
# connectors, nonessential traffic and updates off.
PA_LIB_VM_MANAGED_SETTINGS=/etc/claude-code/managed-settings.json
# The setup token the librarian's sessions run on (claude setup-token). It stays on the devbox,
# mode 0600, and reaches each session through stdin, never the VM's disk or a command line.
PA_LIBRARIAN_TOKEN=$HOME/.config/pa/librarian.token

# The instance's data root on the devbox: v0.11's, seeded from the synthetic corpus and
# disposable (v0.13 starts a fresh one for real sources). owner.env holds the instance owner.
PA_DATA_ROOT=$HOME/.local/share/pa-home-synthetic
PA_OWNER_ENV=$PA_DATA_ROOT/owner.env
# Real-data evals' private files: run directories from runs over a cut, under evals/runs/.
PA_EVALS=$PA_DATA_ROOT/evals
# The three parts of it the VM sees.
PA_L0=$PA_DATA_ROOT/l0
PA_CHAT_INBOX=$PA_DATA_ROOT/inbox/assistant_chat
# The wiki's working tree; its git directory (wiki.git beside it) is never shared.
PA_WIKI=$PA_DATA_ROOT/wiki
PA_WIKI_GIT=$PA_DATA_ROOT/wiki.git
# The librarian's VM writes the wiki as its own user and as it likes, so the wiki is a
# filesystem of its own too, like the inbox directory (install-wiki.sh). So is the directory
# replays build their wikis in (pa librarian replay), for eval wikis and rebuilds.
PA_WIKI_IMAGE=$PA_DATA_ROOT/wiki.img
PA_WIKI_SIZE=1G
PA_REPLAYS=$PA_DATA_ROOT/replays
PA_REPLAYS_IMAGE=$PA_DATA_ROOT/replays.img
PA_REPLAYS_SIZE=4G
# The inbox directory is a filesystem of its own, loop-mounted from this image
# (install-chat-inbox.sh).
PA_CHAT_INBOX_IMAGE=$PA_DATA_ROOT/chat-inbox.img
PA_CHAT_INBOX_SIZE=1G
# What it's mounted with (and the wiki's and the replays' filesystems too), so nothing a VM
# writes there runs, runs setuid, or acts as a device.
PA_CHAT_INBOX_FLAGS=(nosuid nodev noexec)
PA_OWN_FILESYSTEM_FLAGS=("${PA_CHAT_INBOX_FLAGS[@]}")

# The vault copy (#87): Obsidian's headless Sync client keeps the owner's vault here, pull-only,
# for the vault collector to read. It's outside the data root, no VM is shared it, and nothing in
# the PA writes it. vault.env tells the capture cycle where it is and which folder holds the daily
# notes (PA_VAULT_DIR, PA_VAULT_DAILY_NOTES); the wizard writes it.
PA_VAULT=$HOME/.local/share/pa-vault
PA_VAULT_ENV=$HOME/.config/pa/vault.env
# The client is a pinned release: vault-sync/package.json pins it, and its lockfile pins every
# dependency with its checksum. It runs on a pinned Node 22, which the devbox checks against the
# checksum below (from the release's SHASUMS256.txt, signed by a Node releaser). Both are
# installed under PA_VAULT_SYNC_DIR by install-vault-sync.sh. Bump them deliberately: the client
# is in open beta.
PA_VAULT_SYNC_VERSION=0.0.14
PA_VAULT_SYNC_DIR=$HOME/.local/share/pa/vault-sync
PA_NODE_VERSION=22.23.4
PA_NODE_SHA256=1805bcd35e6815559bd2eb97db438d3fdb0eb3a5e6a977b7c1badca76337e9ce
PA_NODE_DIST=https://nodejs.org/dist
PA_NODE_CACHE=$HOME/.cache/pa/node
# The client's own state: its login, each vault's encryption key, and a sync log that names
# notes. Like the copy, it's shared into no VM, and development sessions don't read it.
PA_VAULT_SYNC_CONFIG=$HOME/.config/obsidian-headless
# The unit that runs it, continuously, as the devbox user, and the name its changes carry in the
# vault's version history (it makes none).
PA_VAULT_SYNC_UNIT=pa-vault-sync.service
PA_VAULT_DEVICE_NAME=pa-devbox

# Where the VM sees the store: L0 and the wiki read-only, and its own inbox directory, its only
# way back.
PA_VM_L0=/srv/pa/l0
PA_VM_CHAT_INBOX=/srv/pa/inbox/assistant_chat
PA_VM_WIKI=/srv/pa/wiki
# The VM's copy of owner.env, and the agent project rendered from it.
PA_VM_OWNER_ENV=/home/assistant/.config/pa/owner.env
PA_VM_PROJECT=/home/assistant/agent
PA_VM_SCRATCH=/home/assistant/scratch
# Where run-evals.sh pushes the eval wiki a files-plus-wiki run asks with, replaced each run.
PA_VM_EVAL_WIKI=/home/assistant/.cache/pa-eval/eval-wiki
# Where it pushes the cuts runs over a cut ask against, each once and kept for reuse, and the
# eval set asked over one (replaced each run); and where such a run writes its run directory.
PA_VM_EVAL_CUTS=/home/assistant/.cache/pa-eval/cuts
PA_VM_EVAL_SET=/home/assistant/.cache/pa-eval/eval-set.json
PA_VM_EVAL_RUNS=/home/assistant/.cache/pa-eval/runs
# Claude Code in the VM, and the unit that runs Remote Control in the agent project.
PA_VM_CLAUDE=/home/assistant/.local/bin/claude
PA_VM_RC_UNIT=pa-remote-control.service

# The store's shares into the VM, and nothing else of the data root: each one's Incus device,
# its directory on the devbox, where the VM mounts it, and whether it's read-only.
PA_SHARES=(
  "pa-l0|$PA_L0|$PA_VM_L0|true"
  "pa-chat-inbox|$PA_CHAT_INBOX|$PA_VM_CHAT_INBOX|false"
  "pa-wiki|$PA_WIKI|$PA_VM_WIKI|true"
)

# Where the librarian's VM sees what a run gives it (librarian-sandbox.sh start): L0 and the
# rendered librarian project read-only, the project its sessions' working directory, and the
# wiki writable. Never the wiki's git directory. The devices are added for a run and removed
# after it, so the VM at rest shares nothing.
PA_LIB_VM_L0=/srv/pa/l0
PA_LIB_VM_PROJECT=/srv/pa/librarian
PA_LIB_VM_WIKI=/srv/pa/wiki
PA_LIB_DEVICES=(lib-l0 lib-project lib-wiki)

DEPLOY_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REPO_DIR=$(cd "$DEPLOY_DIR/../.." && pwd)
# The librarian VM's managed settings, as the repo has them.
PA_LIBRARIAN_MANAGED_SETTINGS=$DEPLOY_DIR/librarian/managed-settings.json

log() { printf '==> %s\n' "$*" >&2; }
die() { printf 'error: %s\n' "$*" >&2; exit 1; }

# select_vm assistant|librarian points PA_VM, PA_PROFILE, PA_BRIDGE and PA_USER at that VM, and
# PA_VM_PROFILE_FILE at its Incus profile.
select_vm() {
  case $1 in
    assistant)
      PA_VM=$PA_ASSISTANT_VM PA_PROFILE=$PA_ASSISTANT_PROFILE PA_BRIDGE=$PA_ASSISTANT_BRIDGE
      PA_USER=$PA_ASSISTANT_USER PA_VM_PROFILE_FILE=$DEPLOY_DIR/profile.yaml
      ;;
    librarian)
      PA_VM=$PA_LIBRARIAN_VM PA_PROFILE=$PA_LIBRARIAN_PROFILE PA_BRIDGE=$PA_LIBRARIAN_BRIDGE
      PA_USER=$PA_LIBRARIAN_USER PA_VM_PROFILE_FILE=$DEPLOY_DIR/librarian/profile.yaml
      ;;
    *) die "no VM named $1: --vm takes assistant or librarian" ;;
  esac
  PA_VM_ROLE=$1
}

# take_vm_option "$@" selects the VM a leading `--vm NAME` (or `--vm=NAME`) names, else the
# assistant's, and leaves the arguments after it in ARGS.
take_vm_option() {
  local vm=assistant
  case ${1:-} in
    --vm) vm=${2:?--vm takes assistant or librarian}; shift 2 ;;
    --vm=*) vm=${1#--vm=}; shift ;;
  esac
  select_vm "$vm"
  ARGS=("$@")
}

# incus runs through sudo until the shell picks up the incus-admin group (a new login).
incus() {
  if id -nG | tr ' ' '\n' | grep -qx incus-admin; then
    command incus "$@"
  else
    sudo incus "$@"
  fi
}

# as_assistant runs the bash script on stdin as the VM's assistant user, with a login shell.
# Arguments become the script's positional parameters.
as_assistant() {
  local args=""
  (($#)) && args=$(printf ' %q' "$@")
  incus exec "$PA_VM" -- runuser -l "$PA_USER" -c "bash -s --$args"
}

# require_owner_env stops unless the data root is seeded, owner.env with it.
require_owner_env() {
  [[ -f $PA_OWNER_ENV ]] || die "$PA_OWNER_ENV is missing: run seed-data-root.sh first"
}

# filesystem_missing_flags PATH prints each of PA_OWN_FILESYSTEM_FLAGS that the filesystem PATH
# is on is mounted without, one per line, so it prints nothing for a path anywhere inside the
# wiki's or the replays' filesystem.
filesystem_missing_flags() {
  local options flag
  options=$(findmnt -rn -T "$1" -o OPTIONS) || return 1
  for flag in "${PA_OWN_FILESYSTEM_FLAGS[@]}"; do
    [[ ,$options, == *,$flag,* ]] || echo "$flag"
  done
}

# missing_mount_flags DIR: filesystem_missing_flags for DIR, which must be a mount of its own.
missing_mount_flags() { mountpoint -q "$1" && filesystem_missing_flags "$1"; }

# chat_inbox_missing_flags: missing_mount_flags for the inbox directory.
chat_inbox_missing_flags() { missing_mount_flags "$PA_CHAT_INBOX"; }

# store_share_problems reads the VM as `incus query /1.0/instances/$PA_VM` prints it, and prints
# each way its host directories differ from PA_SHARES, one per line (share_problems).
store_share_problems() { share_problems "${PA_SHARES[@]}"; }

# share_problems SHARE... reads a VM as `incus query /1.0/instances/<vm>` prints it, and prints
# each way its host directories differ from the SHAREs (DEVICE|SOURCE|PATH|READONLY, as in
# PA_SHARES), one per line: a share that's missing, at another path, or writable when it should
# be read-only, and any other host directory shared. It prints nothing when they match.
share_problems() {
  python3 -c '
import json, sys
expected = {}
for share in sys.argv[1:]:
    _, source, path, readonly = share.split("|")
    expected[source] = (path, readonly == "true")
devices = json.load(sys.stdin)["expanded_devices"].values()
shared = {
    d["source"]: d for d in devices if d.get("type") == "disk" and d.get("source", "").startswith("/")
}
for source, (path, readonly) in expected.items():
    device = shared.pop(source, None)
    if device is None:
        print(f"{source} isn'"'"'t shared")
        continue
    at = device.get("path")
    if at != path:
        print(f"{source} is shared at {at}, not {path}")
    is_readonly = device.get("readonly", "false").lower() in ("true", "1", "yes", "on")
    if readonly and not is_readonly:
        print(f"{source} is shared writable")
    if not readonly and is_readonly:
        print(f"{source} is shared read-only")
for source, device in shared.items():
    at = device.get("path")
    print(f"{source} is shared too, at {at}")
' "$@"
}

# render_vault_sync_unit prints the vault sync unit (vault-sync/pa-vault-sync.service), for the
# devbox user, with the client installed under PA_VAULT_SYNC_DIR.
render_vault_sync_unit() {
  sed -e "s|@USER@|$USER|g" -e "s|@RUNNER@|$PA_VAULT_SYNC_DIR/bin/pa-vault-sync|g" \
    -e "s|@VAULT@|$PA_VAULT|g" -e "s|@CONFIG@|$PA_VAULT_SYNC_CONFIG|g" \
    "$DEPLOY_DIR/vault-sync/pa-vault-sync.service"
}

# vault_share_problems reads every VM as `incus query /1.0/instances?recursion=1` prints them,
# and prints each VM shared the vault copy or the client's state, one per line: shared it, a
# directory in it, or a directory holding it. It prints nothing when no VM is.
vault_share_problems() { shared_into_vms "$PA_VAULT" "$PA_VAULT_SYNC_CONFIG"; }

# shared_into_vms PATH... reads every VM as `incus query /1.0/instances?recursion=1` prints them,
# and prints each VM shared a PATH, a directory in it, or a directory holding it, one per line.
shared_into_vms() {
  python3 -c '
import json, os, sys
def within(path, directory):
    return os.path.commonpath([path, directory]) == directory
paths = [os.path.normpath(p) for p in sys.argv[1:]]
for vm in sorted(json.load(sys.stdin), key=lambda vm: vm["name"]):
    devices = vm.get("expanded_devices") or {}
    for path in paths:
        for name in sorted(devices):
            device = devices[name]
            source = device.get("source", "")
            if device.get("type") != "disk" or not source.startswith("/"):
                continue
            source = os.path.normpath(source)
            if within(source, path) or within(path, source):
                vm_name, at = vm["name"], device.get("path")
                print(f"{vm_name} is shared {path} (as {source}, at {at})")
' "$@"
}

# api_address prints the first IPv4 address PA_API_HOST resolves to on the devbox that lies in
# PA_API_RANGE, the only addresses the librarian's VM can reach. It fails if none does.
api_address() {
  getent ahostsv4 "$PA_API_HOST" | awk '{print $1}' | in_api_range | awk 'NR == 1 {print; found = 1} END {exit !found}'
}

# in_api_range prints each address on stdin, one per line, that lies in PA_API_RANGE.
in_api_range() {
  python3 -c '
import ipaddress, sys
network = ipaddress.ip_network(sys.argv[1])
for line in sys.stdin:
    try:
        address = ipaddress.ip_address(line.strip())
    except ValueError:
        continue
    if address in network:
        print(address)
' "$PA_API_RANGE"
}

# acl_differs FILE reads a network ACL as `incus query /1.0/network-acls/<name>` prints it, and
# succeeds if its description or rules differ from FILE's (JSON, as `incus network acl edit`
# takes it). Empty fields count as absent.
acl_differs() {
  python3 -c '
import json, sys
def rules(acl, key):
    return sorted(json.dumps({k: v for k, v in r.items() if v != ""}, sort_keys=True)
                  for r in acl.get(key) or [])
def spec(acl):
    return acl.get("description", ""), rules(acl, "egress"), rules(acl, "ingress")
sys.exit(0 if spec(json.load(sys.stdin)) != spec(json.load(open(sys.argv[1]))) else 1)
' "$1"
}

# vm_running: the selected VM is running.
vm_running() { [[ $(incus list "^$PA_VM\$" -c s -f csv 2>/dev/null) == RUNNING ]]; }

# snapshot_vm PREFIX takes a snapshot of the selected VM named PREFIX-<UTC time>.
snapshot_vm() {
  local snapshot
  snapshot="$1-$(date -u +%Y%m%dT%H%M%SZ)"
  log "snapshot $PA_VM/$snapshot"
  # With stdin not a terminal, snapshot create reads the snapshot's config from it, and waits
  # forever on one that never closes.
  incus snapshot create "$PA_VM" "$snapshot" </dev/null
}

# rc_unit CMD...: runs systemctl CMD on the Remote Control unit in the VM.
rc_unit() { incus exec "$PA_VM" -- systemctl "$@" "$PA_VM_RC_UNIT"; }

# wait_for_agent waits until the VM's Incus agent answers, after a start or a restore.
wait_for_agent() {
  for _ in $(seq 60); do
    incus exec "$PA_VM" -- true >/dev/null 2>&1 && return 0
    sleep 2
  done
  die "the $PA_VM agent didn't come up within 2 minutes"
}

# PA_LIBRARIAN_TOOLS: the only tools a librarian session may have, whatever its flags.
PA_LIBRARIAN_TOOLS=(Edit Glob Grep Read Write)

# session_fence_problems reads what `claude -p --output-format stream-json --verbose` printed for
# a session in the librarian's VM, and prints each way the session got past its fences, one per
# line: a tool other than PA_LIBRARIAN_TOOLS (any tool a later release adds included), an MCP
# server that connected, or bypass mode. It prints nothing when the fences held, and fails if
# the session never started or didn't finish.
session_fence_problems() {
  python3 -c '
import json, sys
allowed = set(sys.argv[1:])
events = []
for line in sys.stdin:
    try:
        event = json.loads(line)
    except json.JSONDecodeError:
        continue
    if isinstance(event, dict):
        events.append(event)
inits = [e for e in events if e.get("type") == "system" and e.get("subtype") == "init"]
results = [e for e in events if e.get("type") == "result"]
if not inits:
    sys.exit("the session never started (no init message)")
if not results or results[-1].get("is_error"):
    detail = results[-1].get("result") if results else "no result"
    sys.exit(f"the session did not finish: {detail}")
init = inits[0]
tools = init.get("tools") or []
for tool in sorted(set(tools) - allowed):
    print(f"it has the tool {tool}")
for tool in sorted(allowed - set(tools)):
    print(f"it lacks the file tool {tool}")
for server in init.get("mcp_servers") or []:
    if server.get("status") == "connected":
        name = server.get("name")
        print(f"the MCP server {name} connected")
if init.get("permissionMode") == "bypassPermissions":
    print("it runs in bypass mode")
' "${PA_LIBRARIAN_TOOLS[@]}"
}
