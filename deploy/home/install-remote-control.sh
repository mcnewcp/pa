#!/usr/bin/env bash
# Usage: install-remote-control.sh [--enable]
#
# Installs the Remote Control unit in the PA VM: `claude remote-control` in the rendered agent
# project, as the assistant user, with exchange capture on. Restarts it if it's enabled, and
# --enable enables it first. Enable it only once the wizard's one-time terminal steps are done:
# until then the service waits on a question nobody can answer. Safe to re-run.
set -euo pipefail
source "$(dirname "$0")/lib.sh"

enable=false
case ${1:-} in
  "") ;;
  --enable) enable=true ;;
  *) die "usage: install-remote-control.sh [--enable]" ;;
esac

# The proven shape (#23). Options go after the remote-control verb: flags before it make it
# refuse to start. No --debug-file: it writes full session transcripts beside its log. The
# permission mode isn't pinned: the phone picks each session's, and the fences are at the VM's
# boundary instead.
# shellcheck disable=SC2016 # $1 is expanded by the shell in the VM
incus exec "$PA_VM" -- sh -c 'cat >"$1"' sh "/etc/systemd/system/$PA_VM_RC_UNIT" <<EOF
[Unit]
Description=Claude Remote Control in the PA agent project
Wants=network-online.target
After=network-online.target incus-agent.service
# A permanent error, such as a rejected login, fails every start: after 5 starts in 5 minutes
# the service stays stopped instead of restarting in a loop.
StartLimitIntervalSec=300
StartLimitBurst=5

[Service]
User=$PA_USER
WorkingDirectory=$PA_VM_PROJECT
# Capture is on for Remote Control sessions and nothing else, into the VM's inbox directory.
Environment=PA_CAPTURE_EXCHANGES=1
Environment=PA_CAPTURE_INBOX=$PA_VM_CHAT_INBOX
# Captures written before the inbox is mounted would go to the VM's own disk and never land.
ExecStartPre=/usr/bin/mountpoint -q $PA_VM_CHAT_INBOX
ExecStart=$PA_VM_CLAUDE remote-control
StandardInput=null
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF
incus exec "$PA_VM" -- systemctl daemon-reload
log "installed $PA_VM_RC_UNIT"

$enable && incus exec "$PA_VM" -- systemctl enable --quiet "$PA_VM_RC_UNIT"
if ! incus exec "$PA_VM" -- systemctl is-enabled --quiet "$PA_VM_RC_UNIT"; then
  log "$PA_VM_RC_UNIT isn't enabled: the setup wizard enables it once Remote Control is set up"
  exit 0
fi

# A tripped start limit refuses every start until it's cleared, say after the login was fixed.
# On a unit that has never run, clearing it fails, harmlessly.
incus exec "$PA_VM" -- systemctl reset-failed "$PA_VM_RC_UNIT" &>/dev/null || true
# A failed start shows below, with the journal. A rejected login exits within seconds.
incus exec "$PA_VM" -- systemctl restart "$PA_VM_RC_UNIT" || true
sleep 5
if ! incus exec "$PA_VM" -- systemctl is-active --quiet "$PA_VM_RC_UNIT"; then
  incus exec "$PA_VM" -- journalctl -u "$PA_VM_RC_UNIT" -n 20 --no-pager >&2
  die "$PA_VM_RC_UNIT isn't running"
fi
log "$PA_VM_RC_UNIT is running"
