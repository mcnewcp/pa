# deploy/home

The PA VM on the devbox: an Incus VM, `pa-home`, that reaches the public internet and nothing of the owner's, and reads the store without being able to change it. See the Home hosting bullet in the [roadmap](../../docs/roadmaps/v0.1-prototype.md).

## Setting up

Run the wizard on the devbox. It walks through the steps only the owner can do and calls the scripts below for the rest. Each stage checks its result, and re-running it skips what's done.

```sh
deploy/home/setup-wizard.sh
```

| Script | Does |
|---|---|
| `incus-setup.sh` | Creates the `pa` btrfs storage pool (copy-on-write, so snapshots are cheap) and the VM's own bridge, `pabr0`, with IPv6 off |
| `install-firewall.sh` | Installs the bridge firewall (`firewall/`) as `pa-vm-firewall.service` |
| `create-vm.sh` | Creates `pa-home` from `profile.yaml` (4 vCPU, 6 GiB RAM, 40 GiB disk, cloud-init, user `assistant`, Tailscale) |
| `seed-data-root.sh` | Creates the instance's data root on the devbox from the frozen synthetic corpus, with its owner as the instance owner |
| `install-chat-inbox.sh` | Makes `inbox/assistant_chat/` a 1 GiB filesystem of its own, mounted `nosuid,nodev,noexec` at every boot before Incus (needs sudo) |
| `mount-store.sh` | Mounts L0 into the VM read-only and `inbox/assistant_chat/` writable |
| `update.sh <ref>` | Snapshots the VM, puts the repo at `<ref>` and runs `uv sync`, then renders the agent project and installs the Remote Control unit, restarting it once it's enabled; the first run installs uv and Claude Code |
| `install-remote-control.sh [--enable]` | Installs `pa-remote-control.service` in the VM and restarts it if it's enabled; `--enable` enables it first |
| `rollback.sh [snapshot]` | Restores the VM to a snapshot, by default the latest one `update.sh` took |
| `ingest.sh` | Ingests the instance's inbox on the devbox, landing the exchanges the VM captured |
| `run-evals.sh [options]` | Runs `pa-eval run` in the VM and copies the run back to `evals/runs/` |
| `check-isolation.sh` | Probes the fences from inside the VM |
| `check-store.sh` | Checks the store's mounts from inside the VM |

`tailscale-policy.hujson` holds the parts of the tailnet policy the VM needs, for merging into the policy in the admin console.

## The fences

- **Host firewall** (`firewall/pa-vm.nft`): its own nftables table on the bridge. Anything the VM sends to the devbox, at any of its addresses, is dropped except DNS and DHCP. Anything it sends on to private ranges (RFC1918, which covers the LAN and Docker's networks), the tailnet range (100.64.0.0/10) or link-local is dropped, and so is all IPv6. A drop in this table is final whatever Incus's, Docker's or Tailscale's own rules accept.
- **Tailnet policy**: the VM joins as `tag:pa`. The owner's devices may reach it, and it is the source of no rule. Its traffic to tailnet peers travels inside Tailscale's tunnel, so only the tailnet policy can stop it.

The devbox also runs ufw, which drops input and forwarded traffic by default. `install-firewall.sh` adds ufw rules letting the bridge's DHCP, DNS and outbound traffic through, and the `pa_vm` table still fences everything else.

The firewall loads at boot from `pa-vm-firewall.service`, after Docker and before Incus. Incus requires it, so if the rules fail to load, Incus doesn't start and the VM stays down. Don't enable `nftables.service` to persist rules: Ubuntu's `/etc/nftables.conf` starts with `flush ruleset`, which would wipe Docker's and Incus's rules at boot.

## The store

The data root stays on the devbox, at `~/.local/share/pa-home-synthetic/`. In v0.11 it is seeded from the frozen synthetic corpus, with the corpus's fictional owner as the instance owner (in `owner.env` beside it), and it is disposable: v0.13 starts a fresh one for real sources. Ingest runs on the devbox, by hand (`ingest.sh`).

Two directories of it are mounted into the VM, and nothing else, the catalog included:

| On the devbox | In the VM | |
|---|---|---|
| `l0/` | `/srv/pa/l0` | Read-only. Enforced on the devbox, so even root in the VM can't write it |
| `inbox/assistant_chat/` | `/srv/pa/inbox/assistant_chat` | Writable: the VM's only way back. Ingest makes anything written there an `assistant_chat` episode, or quarantines it ([ADR-0004](../../docs/adr/0004-inbox-directory-sets-source.md)) |

`update.sh` copies `owner.env` into the VM and renders the agent project from it into `/home/assistant/agent`, pointing it at `/srv/pa/l0`, with `/home/assistant/scratch` as its scratch directory. Each update deletes the rendered project and renders it again at the same path.

The shares are served by `virtiofsd` running as root on the devbox, with no id mapping. The VM's `assistant` user can write its inbox directory only because its uid is the devbox user's (both 1000); `mount-store.sh` refuses to go on if they differ. What the VM writes into `inbox/assistant_chat/` keeps the VM's owners and modes, so root in the VM could leave root-owned files there, setuid programs and device nodes included, or fill the devbox's disk. So that directory is a filesystem of its own (`install-chat-inbox.sh`): a 1 GiB ext4 image, `chat-inbox.img` in the data root, loop-mounted `nosuid,nodev,noexec` by a systemd mount unit. Nothing in it can run, setuid or not, or act as a device, and the VM can't write more than 1 GiB. The unit mounts it at boot before Incus, and Incus requires it: if it can't be mounted, Incus doesn't start and the VM stays down instead of sharing the bare directory.

Ingest reads only regular files and never follows a symbolic link (a link is quarantined unread), so it can't be made to copy a devbox file into L0, where the VM could read it. A file it isn't allowed to read is quarantined without its content, and one it isn't allowed to remove (root in the VM can take the devbox user's write permission away) is left in the inbox, not landed, so it doesn't land again on every run; `ingest.sh` names it and exits non-zero, and the rest of the inbox still lands. Either way only root on the devbox can clear it.

To start the data root over (stopping the inbox mount stops Incus too, since Incus requires it; the next `incus` command starts it again):

```sh
incus config device remove pa-home pa-l0 pa-chat-inbox
unit=$(systemd-escape -p --suffix=mount ~/.local/share/pa-home-synthetic/inbox/assistant_chat)
sudo systemctl disable --now "$unit"
rm -rf ~/.local/share/pa-home-synthetic
deploy/home/seed-data-root.sh && deploy/home/install-chat-inbox.sh && deploy/home/mount-store.sh
```

## Remote Control

`pa-remote-control.service` in the VM runs `claude remote-control` in the rendered agent project, as `assistant`, so the owner chats with the assistant from the Claude app on their phone and from claude.ai/code. Both list it as `pa-home → agent`: the VM's hostname and the project's folder. It's the only thing that turns exchange capture on (`PA_CAPTURE_EXCHANGES=1`, with `PA_CAPTURE_INBOX` set to the inbox directory), so each exchange of a chat lands in the inbox for `ingest.sh`.

- It runs on the `claude auth login` subscription login. Remote Control refuses `setup-token` tokens.
- It waits until the inbox directory is mounted, so no capture goes to the VM's own disk.
- It restarts on failure, but five failed starts in five minutes leave it stopped, so a permanent error such as a rejected login doesn't loop. `journalctl -u pa-remote-control` in the VM says why; fix it, then run `install-remote-control.sh` to start it again.
- `update.sh` stops it before the render and starts it again after. A chat open during an update is cut off.
- The permission mode isn't pinned: the phone picks each session's mode, and the fences hold in every mode.
- Before it's enabled, two questions are answered once from a terminal as `assistant`: running `claude` in the project (theme, the "Claude can make mistakes" notice, folder trust), and running `claude remote-control` (`Enable Remote Control? (y/n)`). With no terminal, the service would wait on that question forever. The wizard asks both, checks the answers in the VM's `~/.claude.json` (`hasTrustDialogAccepted`, `remoteDialogSeen`), and only then enables the service.

Don't chat during an eval run: the run fails if the inbox changes while it runs, and a chat changes it.

## Evals in the VM

```sh
deploy/home/run-evals.sh                               # --agent claude --judge model
deploy/home/run-evals.sh --agent claude --judge model --agent-model sonnet --judge-model sonnet
```

The run uses the code and agent project at the VM's deployed commit, builds its throwaway data root on the VM's own disk (never the mounted store) with exchange capture off, and fails if `inbox/assistant_chat/` changed while it ran. The run directory is copied back to `evals/runs/` in this checkout. Claude Code in the VM must be logged in (the wizard's login stage).

## Updating and rolling back

```sh
deploy/home/update.sh <commit|branch|tag>   # snapshot, then deploy
deploy/home/rollback.sh                     # back to the latest pre-update snapshot
incus snapshot list pa-home                 # every snapshot
```

The VM's Tailscale identity lives on its disk, so it survives reboots and rollbacks to any snapshot taken after it joined.

## Acceptance run (#27)

1. Run the wizard from nothing; it ends with `check-isolation.sh` passing.
2. From the Mac, `ssh assistant@pa-home` gets a shell.
3. Reboot the devbox. `pa-home` comes back on its own, `systemctl status pa-vm-firewall` is active, and `check-isolation.sh` passes again.
4. Roll back:
   ```sh
   deploy/home/update.sh <ref>
   incus exec pa-home -- touch /root/rollback-marker
   deploy/home/rollback.sh
   incus exec pa-home -- test ! -e /root/rollback-marker && echo "rolled back"
   ```

## Acceptance run (#30)

1. `check-store.sh` passes, and passes again after `incus restart pa-home`.
2. A payload the VM writes lands on the devbox:
   ```sh
   incus exec pa-home -- runuser -u assistant -- sh -c 'cd /srv/pa/inbox/assistant_chat &&
     cp ~/pa/core/tests/fixtures/assistant_chat/single_exchange.json .check.json && mv .check.json check.json'
   deploy/home/ingest.sh   # ingested 1; run again: ingested 0
   ```
   Then delete that episode from L0 and rebuild the catalog, so the assistant doesn't see it.
3. `run-evals.sh` completes and leaves the inbox unchanged; its report is committed under `evals/baselines/`.

## Acceptance run (#33)

1. `install-chat-inbox.sh` runs, and `check-store.sh` passes.
2. What root in the VM writes there can't run or act as a device on the devbox:
   ```sh
   incus exec pa-home -- sh -c 'cd /srv/pa/inbox/assistant_chat &&
     printf "#!/bin/sh\nid\n" >.setuid && chmod 4755 .setuid && mknod -m 666 .null c 1 3'
   ~/.local/share/pa-home-synthetic/inbox/assistant_chat/.setuid   # Permission denied
   : <~/.local/share/pa-home-synthetic/inbox/assistant_chat/.null  # Permission denied
   incus exec pa-home -- rm /srv/pa/inbox/assistant_chat/.setuid /srv/pa/inbox/assistant_chat/.null
   ```
3. The VM can't write more than 1 GiB:
   ```sh
   incus exec pa-home -- runuser -u assistant -- dd if=/dev/zero of=/srv/pa/inbox/assistant_chat/.fill bs=1M count=1100
   # No space left on device, short of 1100 MiB
   incus exec pa-home -- rm /srv/pa/inbox/assistant_chat/.fill
   ```
4. Reboot the devbox: the inbox is mounted before `pa-home` starts, and `check-store.sh` passes.

## Acceptance run (#31)

1. The wizard ends with `pa-remote-control.service` running, and the Claude app on the phone and claude.ai/code list `pa-home → agent`.
2. From the phone, in one session: a question about the corpus gets an answer with `[ep:…]` citations, and a `WebFetch` goes through without a prompt in auto mode and again in manual mode.
3. `deploy/home/ingest.sh` lands one `assistant_chat` episode per exchange, all with the session's id as `thread_ref`; run again, it ingests 0.
4. `incus exec pa-home -- systemctl restart pa-remote-control`, then `incus restart pa-home`: the service comes back on its own each time, connected, with no new login.
5. A permanent error leaves it stopped. Give it an inference-only token, which Remote Control refuses:
   ```sh
   incus exec pa-home -- sh -c 'd=/run/systemd/system/pa-remote-control.service.d && mkdir -p $d &&
     printf "[Service]\nEnvironment=CLAUDE_CODE_OAUTH_TOKEN=bogus\n" >$d/error.conf && systemctl daemon-reload &&
     systemctl restart pa-remote-control'
   # a minute later: failed, NRestarts=4, "Start request repeated too quickly"
   incus exec pa-home -- systemctl show pa-remote-control -p ActiveState -p NRestarts
   incus exec pa-home -- sh -c 'rm -r /run/systemd/system/pa-remote-control.service.d && systemctl daemon-reload'
   deploy/home/install-remote-control.sh
   ```
6. `run-evals.sh`, with the service running, leaves the inbox unchanged.
