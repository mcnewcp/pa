# deploy/home

The PA's two VMs on the devbox:

- the assistant's, `pa-home`: an Incus VM that reaches only the Anthropic API ([The network fence](#the-network-fence)), and reads the store without being able to change it;
- the librarian's, `pa-librarian`: a second Incus VM, made with the same scripts (`--vm librarian`), that also reaches only the Anthropic API and writes nothing but the wiki ([The librarian's VM](#the-librarians-vm)).

Neither is on the tailnet. The owner reaches both with `incus exec` on the devbox.

See the Home hosting bullet in the [roadmap](../../docs/roadmaps/v0.1-prototype.md).

## Setting up

Run the wizard on the devbox. It walks through the steps only the owner can do and calls the scripts below for the rest. Each stage checks its result, and re-running it skips what's done.

```sh
deploy/home/setup-wizard.sh              # both VMs, the vault copy, then the backup drive
deploy/home/setup-wizard.sh --librarian  # the librarian's VM alone
deploy/home/setup-wizard.sh --vault      # the vault copy alone: Obsidian Sync and its headless client
deploy/home/setup-wizard.sh --gmail      # Gmail's Cloud project and consent, or a re-auth
deploy/home/setup-wizard.sh --backup     # the backup drive and its repository alone
```

The scripts that both VMs share take `--vm assistant|librarian`, and work on the assistant's VM without it.

| Script | Does |
|---|---|
| `incus-setup.sh [--vm]` | Creates the `pa` btrfs storage pool (copy-on-write, so snapshots are cheap) and the VM's own bridge, `pabr0`, with IPv6 and DNS off, and its egress ACL, `pa-home-egress` (`egress-acl.json`); and the open bridge the VM is built on, `pabuild0`. For the librarian: `palib0` and `pa-librarian-egress` |
| `install-firewall.sh` | Installs the bridge firewall (`firewall/`) as `pa-vm-firewall.service`, for every bridge, and the matching ufw rules |
| `create-vm.sh [--vm]` | Creates `pa-home` from `profile.yaml` (4 vCPU, 6 GiB RAM, 40 GiB disk, cloud-init, user `assistant`), open, on `pabuild0`. For the librarian: `pa-librarian` from `librarian/profile.yaml` (2 vCPU, 3 GiB RAM, 10 GiB disk, user `librarian`), left stopped |
| `fence-vm.sh` | Fences `pa-home`: installs its fences while it can still reach a mirror, removes Tailscale, moves it onto `pabr0`, pins the API's names, and installs `pa-pins@pa-home.service` on the devbox ([The network fence](#the-network-fence)) |
| `pin-hosts.sh` | Pins `api.anthropic.com` and `platform.claude.com` into `pa-home`'s `/etc/hosts`, at addresses the devbox resolved and checked against the API's range |
| `seed-data-root.sh [--corpus argus\|scale]` | Creates the instance's data root on the devbox from a frozen synthetic corpus (Argus's unless `--corpus scale`), with its owner as the instance owner |
| `install-chat-inbox.sh` | Makes `inbox/assistant_chat/` a 1 GiB filesystem of its own, mounted `nosuid,nodev,noexec` at every boot before Incus (needs sudo), with `install-own-filesystem.sh` |
| `install-wiki.sh` | Does the same for `wiki/` (1 GiB) and `replays/` (4 GiB), which the librarian's VM writes |
| `mount-store.sh` | Mounts L0, the wiki and the status into the VM read-only and `inbox/assistant_chat/` writable; refuses a VM that isn't fenced |
| `update.sh <ref>` | Snapshots the VM, then pushes in the code at `<ref>` (a git bundle from this checkout), a wheelhouse built here, uv and the pinned Claude Code; as root, checks the code out in `/opt/pa` and syncs its venv offline (`install-code.sh`), installs the fences (`install-assistant-fences.sh`), pins the API's names, renders the agent project and installs the Remote Control unit, restarting it once it's enabled ([Updating and rolling back](#updating-and-rolling-back)) |
| `install-assistant-fences.sh` | Installs the assistant's managed settings, the sandbox's packages and AppArmor profile, and its layout (`assistant-layout.sh`) in the VM, as root, and masks the OS's own updates ([The assistant's managed settings and sandbox](#the-assistants-managed-settings-and-sandbox)) |
| `install-remote-control.sh [--enable]` | Installs `pa-remote-control.service` in the VM and restarts it if it's enabled; `--enable` enables it first |
| `update.sh --vm librarian` | Snapshots the librarian's VM, then installs the pinned Claude Code and the managed settings (`install-librarian-claude.sh`) |
| `rollback.sh [--vm] [snapshot]` | Restores the VM to a snapshot, by default the latest one `update.sh` took |
| `ingest.sh` | Ingests the instance's inbox on the devbox, landing the exchanges the VM captured |
| `run-evals.sh [options]` | Runs `pa-eval run` in the VM and copies the run back to `evals/runs/` (or `$PA_EVAL_RUNS_DIR`); a run over a cut pushes the cut once and comes back under the data root |
| `draft-evals.sh --cut DIR [--revise DRAFT --notes FILE]` | Has a session in the VM draft the real-data eval set from a cut (or revise a draft by the owner's notes), and brings the draft back under the data root |
| `check-isolation.sh` | Probes the fences from inside the VM: the network (only the API reachable, no DNS, no Tailscale), the OS's updates and Claude Code's pin, the managed settings, the read-only project, a session's tools, and the sandbox, by running a command in it |
| `check-store.sh` | Checks the store's mounts from inside the VM |
| `librarian-sandbox.sh start\|stop\|claude\|kill` | The librarian's VM as the sandbox its sessions run in: `PA_LIBRARIAN_SANDBOX` |
| `librarian.sh run\|replay\|seed …` | Runs `pa librarian` on the devbox over the data root, its sessions in the librarian's VM |
| `check-librarian.sh` | Checks the librarian VM's fences from inside it |
| `install-vault-sync.sh [--enable]` | Installs Obsidian's headless Sync client at its pinned version on a pinned Node 22, and `pa-vault-sync.service`; `--enable` starts it once the vault is set up pull-only ([The vault copy](#the-vault-copy)) |
| `check-vault-sync.sh` | Checks the vault copy: the pins, pull-only, the service, no VM shared it, and the daily notes arriving, by counts and times |
| `gmail.sh auth\|dry-run …` | Runs `pa gmail` on the devbox: the consent (and re-auth), and the dry run's counts ([Gmail](#gmail)) |
| `install-backup-drive.sh <UUID>` | Mounts the USB SSD with that filesystem UUID at `/mnt/pa-backup`, at every boot, and makes its `restic/` directory the owner's (needs sudo) |
| `backup.sh` | Runs `pa backup` on the devbox: one restic snapshot of L0, the wiki's git directory and the private eval directory, on the USB SSD ([The backup](#the-backup)) |

`tailscale-policy.hujson` holds the tailnet policy as the PA leaves it: no PA VM is on the tailnet, and the owner's devices reach each other.

## The fences

### The network fence

The assistant's VM reaches TCP 443 in the Anthropic API's published range, `160.79.104.0/23`, and nothing else: not the web, the owner's devices, the LAN or the devbox ([the fences spike](../../docs/research/assistant-fences-spike.md), #91). Three fences stand one behind another, as for the librarian's VM:

- **The bridge and its ACL.** `pabr0` has IPv6 off and no DNS: its dnsmasq serves DHCP only (`raw.dnsmasq=port=0`), since a resolver that forwards lookups is a way out. The VM's NIC carries the Incus ACL `pa-home-egress` (`egress-acl.json`), which lets out TCP 443 to the range and drops and logs everything else both ways (`profile.yaml`). Drops show in `journalctl -k`.
- **Host firewall** (`firewall/pa-vm.nft`): its own nftables table on the bridges. From `pabr0` (and `palib0`), only DHCP reaches the devbox, at any of its addresses, and only TCP 443 in the range goes on. A drop in this table is final whatever Incus's, Docker's or Tailscale's own rules accept.
- **ufw**, which drops input and forwarded traffic by default: `install-firewall.sh` lets `pabr0`'s DHCP and its traffic to the range through, and nothing else.

With no DNS, the two names the VM needs are pinned into its `/etc/hosts` by the devbox (`pin-hosts.sh`): `api.anthropic.com`, for sessions and Remote Control, and `platform.claude.com`, for the login's code exchange and every token refresh. The devbox resolves each and pins only an address in the range, or nothing. `fence-vm.sh`, `update.sh` and `rollback.sh` pin them, and `pa-pins@pa-home.service` on the devbox pins them at every boot, after Incus has started the VM; the pins from the last start stay in the VM's `/etc/hosts` in between (cloud-init leaves the file alone).

The VM has no Tailscale. The owner reaches it with `incus exec` from the devbox, and Remote Control reaches the phone through the API.

Nothing in the VM knocks on the fence: `install-assistant-fences.sh` masks apt's timers, unattended upgrades, the clock's sync (the clock comes from the hypervisor) and Ubuntu's news timers, and turns apt's periodic runs off. Claude Code's own updates are off in the managed settings. Updates are pushed in from the devbox ([Updating and rolling back](#updating-and-rolling-back)), and the OS is refreshed by rebuilding the VM ([Rebuilding the assistant's VM](#rebuilding-the-assistants-vm)).

The VM is built open, on `pabuild0`: DNS and the internet, but nothing of the owner's (the firewall drops private, tailnet and link-local ranges and IPv6, as it did for `pabr0` before #91). `create-vm.sh` puts the new VM's NIC there, in place of its profile's, so cloud-init can refresh the OS and install the packages. Nothing is shared into it and no credential entered until `fence-vm.sh` has moved it onto `pabr0`: `mount-store.sh` refuses a VM whose NIC isn't fenced, and so does the wizard's login stage.

The firewall loads at boot from `pa-vm-firewall.service`, after Docker and before Incus. Incus requires it, so if the rules fail to load, Incus doesn't start and the VMs stay down. Don't enable `nftables.service` to persist rules: Ubuntu's `/etc/nftables.conf` starts with `flush ruleset`, which would wipe Docker's and Incus's rules at boot.

### The assistant's managed settings and sandbox

Inside the VM, the assistant's policy is Claude Code's managed settings, `managed-settings.json` here, at `/etc/claude-code/managed-settings.json` in the VM, root's and mode 0644, so the assistant can't change them and every session in the VM runs under them, eval and drafting sessions included ([the fences spike](../../docs/research/assistant-fences-spike.md)). They:

- deny WebSearch, WebFetch, every MCP tool, Remote Control's own `SendUserFile` and `FetchInboxMessage`, and the tools that schedule prompts or reach out remotely, so a headless session has exactly `Bash, Edit, Read, ToolSearch, Write`, whatever its flags;
- deny the file tools every place the assistant could write but scratch, and reads of its credentials;
- admit only their own permission rules and hooks: the three capture hooks ([Exchange capture](#exchange-capture));
- require Claude Code's Bash sandbox, failing if it's unavailable, with no unsandboxed escape, writes only to scratch, and no network;
- lock out bypass mode, and turn off auto memory, claude.ai connectors, auto-updates, telemetry, error reports and the feedback command. Not nonessential traffic, whose switch breaks Remote Control.

The sandbox needs `bubblewrap`, `socat` and `apparmor`, and the `pa-bwrap` AppArmor profile (`apparmor/pa-bwrap`), which lets bubblewrap make its user namespace in place of Ubuntu's `bwrap-userns-restrict`, under which every sandboxed command fails.

What the assistant can't write is root's, outside its home, so no directory above it is the assistant's:

| In the VM | Owner, mode | |
|---|---|---|
| `/srv/pa/agent` | root, 0755 | The rendered agent project and its hook's script, each session's working directory |
| `/opt/pa` | root | The code and its venv, on the VM's own `/usr/bin/python3`, the hook's interpreter |
| `/usr/local/bin/claude`, `/usr/local/bin/uv` | root, 0755 | The pinned Claude Code, and uv, pushed from the devbox |
| `/etc/pa/owner.env` | root, 0644 | The instance owner, which the project renders from |
| `/etc/claude-code/managed-settings.json` | root, 0644 | The policy |
| `/usr/local/libexec/pa-capture` | root, 0755 | The capture helper (`pa-capture`) |
| `/etc/sudoers.d/pa-capture` | root, 0440 | Its sudo rule (`sudoers-pa-capture`) |
| `/srv/pa/inbox/assistant_chat` | capture (uid 1000), 0755 | The chat inbox, a writable share |
| `/var/lib/pa-capture` | capture, 0700 | The helper's pending prompts |
| `~/.claude/settings.json` | root, 0644 | `{}`, since no setting turns user settings off |
| `/srv/pa/scratch` | assistant, 0700 | The one directory the assistant's shell and file tools write |
| `/srv/pa/eval` | assistant, 0700 | Evals' cuts, sets, runs, drafts and throwaway data roots, outside the home directory, whose `CLAUDE.md` files the managed settings exclude |

The home directory is the assistant's, so only the managed settings' deny rules and the sandbox keep its tools out of it; Claude Code itself still writes its state there. `install-assistant-fences.sh` also removes any auto-memory directory left under `~/.claude/projects/`.

### Exchange capture

Only the harness's capture hooks can write an exchange, and they take the owner's words from the hook events, never from the transcript, which the assistant's own process writes ([ADR-0009](../../docs/adr/0009-capture-by-a-separate-user.md), #94). Three managed hooks do nothing unless the session's environment has `PA_CAPTURE_EXCHANGES=1`, which only the Remote Control unit sets; otherwise each runs the capture helper as the `capture` user, through the one sudo rule, with its event on stdin:

- `UserPromptSubmit` → `pa-capture prompt`: the helper holds the owner's prompt as pending for its session. It refuses one that opens as a background task's report (`<task-notification>`) or another session's message (`<cross-session-message`), and drops what was pending.
- `PostToolUse` on `AskUserQuestion` → `pa-capture answer`: it adds each question the assistant asked and the owner's answer.
- `Stop` → `pa-capture stop`: it adds the reply (`last_assistant_message`), holds the exchange to the `assistant_chat` schema, and writes it into the inbox under a hidden name, renamed into place. The message id is the turn's `prompt_id`, which both events carry.

A turn the owner interrupts, or that fails, fires no `Stop`, so it's never written: its prompt is dropped by the session's next prompt, or once it's 24 hours old. A stop whose `prompt_id` isn't the pending prompt's writes nothing either.

The helper (`pa-capture`, at `/usr/local/libexec/pa-capture`) runs the project's `.claude/hooks/capture_exchange.py` with `/opt/pa/.venv/bin/python -I`, the inbox and its state directory written in, since sudo resets the environment. `install-assistant-fences.sh` puts it in, with the sudo rule (`sudoers-pa-capture`, checked by `visudo` before it's moved into `/etc/sudoers.d/`), and makes the `capture` user and its state directory (`capture-user.sh`).

The `capture` user has the devbox user's uid, 1000: the inbox share keeps owners unmapped, so what the helper writes is the devbox user's on the devbox, mode 0644, where ingest reads and removes it, and the assistant, uid 1001, can't write the inbox, in the VM or through the share. A rebuilt VM gets both users from `profile.yaml`; in a VM built before #94, `capture-user.sh` moves the assistant from uid 1000 to 1001 first, with its group, its home directory and what else it owns on the VM's own disk (it refuses while anything runs as the assistant). The sandboxed shell can't use the sudo rule at all: nothing in the sandbox gains privileges.

## The store

The data root stays on the devbox, at `~/.local/share/pa-home-synthetic/`. In v0.11 it is seeded from the frozen synthetic corpus, with the corpus's fictional owner as the instance owner (in `owner.env` beside it), and it is disposable: v0.13 starts a fresh one for real sources. Ingest runs on the devbox, by hand (`ingest.sh`).

Four directories of it are mounted into the VM, and nothing else, the catalog and the wiki's git directory (`wiki.git/`) included:

| On the devbox | In the VM | |
|---|---|---|
| `l0/` | `/srv/pa/l0` | Read-only. Enforced on the devbox, so even root in the VM can't write it |
| `inbox/assistant_chat/` | `/srv/pa/inbox/assistant_chat` | Writable: the VM's only way back. Ingest makes anything written there an `assistant_chat` episode, or quarantines it ([ADR-0004](../../docs/adr/0004-inbox-directory-sets-source.md)) |
| `wiki/` | `/srv/pa/wiki` | Read-only, like L0. The librarian keeps it on the devbox; `mount-store.sh` creates it empty if it doesn't exist yet |
| `status/` | `/srv/pa/status` | Read-only, like L0. It holds `status.json`, which the capture cycle, the librarian and the backup replace by a rename, so the directory is shared, never the file; `mount-store.sh` creates it empty if it doesn't exist yet |

`update.sh` copies `owner.env` into the VM (`/etc/pa/owner.env`, root's) and, as root, renders the agent project from it into `/srv/pa/agent`, pointing it at `/srv/pa/l0` and the status file `/srv/pa/status/status.json`, with `/srv/pa/scratch` as its scratch directory, and at `/srv/pa/wiki` once the librarian has run into the wiki (its `index.md` is there); until then the assistant runs on files alone, so run `update.sh` again after the librarian's first run. Each update deletes the rendered project and renders it again at the same path.

The shares are served by `virtiofsd` running as root on the devbox, with no id mapping. The VM's `assistant` user can write its inbox directory only because its uid is the devbox user's (both 1000); `mount-store.sh` refuses to go on if they differ. What the VM writes into `inbox/assistant_chat/` keeps the VM's owners and modes, so root in the VM could leave root-owned files there, setuid programs and device nodes included, or fill the devbox's disk. So that directory is a filesystem of its own (`install-chat-inbox.sh`): a 1 GiB ext4 image, `chat-inbox.img` in the data root, loop-mounted `nosuid,nodev,noexec` by a systemd mount unit. Nothing in it can run, setuid or not, or act as a device, and the VM can't write more than 1 GiB. The unit mounts it at boot before Incus, and Incus requires it: if it can't be mounted, Incus doesn't start and the VM stays down instead of sharing the bare directory.

Ingest reads only regular files and never follows a symbolic link (a link is quarantined unread), so it can't be made to copy a devbox file into L0, where the VM could read it. A file it isn't allowed to read is quarantined without its content, and one it isn't allowed to remove (root in the VM can take the devbox user's write permission away) is left in the inbox, not landed, so it doesn't land again on every run; `ingest.sh` names it and exits non-zero, and the rest of the inbox still lands. Either way only root on the devbox can clear it.

The wiki and `replays/` are filesystems of their own in the same way (`install-wiki.sh`, `wiki.img` and `replays.img`), since the librarian's VM writes them. The wiki's git directory stays beside the wiki, outside its filesystem.

To start the data root over (stopping a mount stops Incus too, since Incus requires it; the next `incus` command starts it again):

```sh
incus config device remove pa-home pa-l0 pa-chat-inbox pa-wiki
for dir in inbox/assistant_chat wiki replays; do
  sudo systemctl disable --now "$(systemd-escape -p --suffix=mount ~/.local/share/pa-home-synthetic/$dir)"
done
rm -rf ~/.local/share/pa-home-synthetic
deploy/home/seed-data-root.sh && deploy/home/install-chat-inbox.sh && deploy/home/install-wiki.sh
```

### Reseeding with the scale corpus

The live data root can hold the scale corpus instead, with its wiki seeded from the scale eval wiki rather than built by a second replay: the episode ids are the same. Start the data root over as above, but seed it with the scale corpus, then seed the wiki:

```sh
deploy/home/seed-data-root.sh --corpus scale && deploy/home/install-chat-inbox.sh && deploy/home/install-wiki.sh
deploy/home/librarian.sh seed evals/wikis/scale/<librarian version>
deploy/home/update.sh <ref>   # the assistant's project points at the wiki once it has index.md
```

`seed-data-root.sh` ingests the corpus at the time it runs: that's the reseed's capture time. `librarian.sh seed` (`pa librarian seed`, no session) copies the eval wiki's `wiki/` into the data root's empty wiki, regenerates `index.md`, lints it against the data root's L0, and commits it with a cursor that counts every episode in L0 as already in the wiki, so the next `librarian.sh run` takes only what's captured after (chats from the phone, say). Seed right after the reseed; if something was ingested since, give `--captured-through <the reseed's capture time>` (ISO 8601) and it stays new. It refuses a wiki that has runs or files already, and an eval wiki that fails lint.

## The backup

`backup.sh` runs `pa backup`, which takes one restic snapshot of what can't be rebuilt cheaply: the data root's `l0/`, the wiki's git directory `wiki.git/`, and the private eval directory `evals/` (real-data eval sets, cuts, runs and verdicts). `wiki.git/` and `evals/` are left out until they exist. Nothing else goes in: the catalog is rebuilt from L0, the wiki's files are checked out from `wiki.git/`, and the inbox and collector state are captured again (a payload captured twice collapses on ingest). `owner.env` isn't in it either; it's yours to write again. Nothing is ever pruned in v0.13. L0 only grows, and restic stores each file's content once, so a snapshot costs only what changed since the last.

- **The drive.** The USB SSD is mounted at `/mnt/pa-backup` by its filesystem's UUID, so no other drive is ever mounted there, whatever device name it gets. `install-backup-drive.sh` writes the mount unit, `mnt-pa\x2dbackup.mount`. The unit is wanted at boot, never required: without the drive the devbox still boots, and the mount point stays empty.
- **The repository** is `/mnt/pa-backup/restic`. It's encrypted with the password in `~/.config/pa/restic.password`, mode 0600. Keep a copy of the password in your password manager: without it no backup can be restored, and the devbox's copy goes with the devbox. The wizard's backup stages (`setup-wizard.sh --backup`) mount the drive, make the password once, offer to show it so you can save it, and initialize the repository.
- **Configuration.** `pa backup` reads restic's own variables. `backup.sh` sets `RESTIC_REPOSITORY` and `RESTIC_PASSWORD_FILE` to the drive's, and ignores any restic variables already in the environment.
- **The lock and the status.** `pa backup` takes the data root's lock, so it never snapshots a half-run capture or librarian run. It skips, and exits 0, while another run holds the lock. Each backup records its last success or its last failure in the status, and `pa status` prints both. A missing drive shows as a missing repository ("is the backup drive mounted?"). Other failures are a wrong password, a repository restic has locked, an incomplete snapshot, or restic's exit code. `pa backup` never prints restic's own output, since that can name files under the data root. To see it, run restic by hand.
- **A cloud copy, later,** is a second restic repository: run `pa backup` again with that repository's variables, or `restic copy` the drive's snapshots into it. Neither needs a change here.

### Restoring

Restic needs the repository and its password. On a new devbox, first install restic, plug in the SSD, and run `install-backup-drive.sh <UUID>`. Then write the password from your password manager to `~/.config/pa/restic.password`, mode 0600.

```sh
export RESTIC_REPOSITORY=/mnt/pa-backup/restic RESTIC_PASSWORD_FILE=~/.config/pa/restic.password
restic snapshots                                   # one per backup; the newest is "latest"
restic restore latest --target /tmp/pa-restore     # l0/, wiki.git/ and evals/, as backed up
```

To bring back a whole data root, start from an empty one. Move the old one aside if it's still there, and stop anything that runs against it.

```sh
data_root=~/.local/share/pa-home-synthetic          # the instance's data root (lib.sh's PA_DATA_ROOT)
mkdir -p "$data_root" && restic restore latest --target "$data_root"
# owner.env: write the instance owner again (PA_OWNER_NAME, PA_OWNER_EMAILS, PA_TIME_ZONE, ...)
deploy/home/install-chat-inbox.sh && deploy/home/install-wiki.sh   # the VMs' own filesystems
git --git-dir "$data_root/wiki.git" --work-tree "$data_root/wiki" checkout -f main   # the wiki's files
PA_DATA_DIR=$data_root uv run pa catalog rebuild    # the catalog, from L0
```

L0 then holds every episode it held at the snapshot, byte for byte. The wiki is at its last commit before the snapshot, and its cursor is where that commit left it, so the next librarian run takes everything captured after.

To bring back a single file deleted by mistake, restore just that file: `restic restore latest --target "$data_root" --include /l0/<path>`.

Check the repository itself now and then (`restic check`). It reads the repository's structure, not every file's data, unless given `--read-data`.

## Remote Control

`pa-remote-control.service` in the VM runs `claude remote-control` in the rendered agent project, as `assistant`, so the owner chats with the assistant from the Claude app on their phone and from claude.ai/code. Both list it as `pa-home → agent`: the VM's hostname and the project's folder. It's the only thing that turns exchange capture on (`PA_CAPTURE_EXCHANGES=1`), so each exchange of a session lands in the inbox, through the capture helper, for `ingest.sh` ([Exchange capture](#exchange-capture)).

- It runs on the `claude auth login` subscription login. Remote Control refuses `setup-token` tokens. The login refreshes its token through `platform.claude.com` when it's needed, so that name stays pinned; without it Remote Control ends once the access token expires.
- Don't set `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC` for it: Remote Control refuses to start under it (#81).
- It waits until the inbox directory is mounted, so no capture goes to the VM's own disk.
- It restarts on failure, but five failed starts in five minutes leave it stopped, so a permanent error such as a rejected login doesn't loop. `journalctl -u pa-remote-control` in the VM says why; fix it, then run `install-remote-control.sh` to start it again.
- `update.sh` stops it before the render and starts it again after. A session open during an update is cut off.
- The permission mode isn't pinned: the phone picks each session's mode, and the fences hold in every mode.
- Before it's enabled, two questions are answered once from a terminal as `assistant`: running `claude` in the project (theme, the "Claude can make mistakes" notice, folder trust), and running `claude remote-control` (`Enable Remote Control? (y/n)`). With no terminal, the service would wait on that question forever. The wizard asks both, checks the answers in the VM's `~/.claude.json` (`hasTrustDialogAccepted`, `remoteDialogSeen`), and only then enables the service.

Don't chat during an eval run: the run fails if the inbox changes while it runs, and every exchange changes it.

## The librarian's VM

`pa-librarian` runs the librarian's sessions: `claude -p` with the file tools only, over L0 and into the wiki. It follows the [sandbox spike](../../docs/research/librarian-sandbox-spike.md) and [ADR-0007](../../docs/adr/0007-living-wiki-fenced-librarian.md). Start a run by hand on the devbox:

```sh
deploy/home/ingest.sh && deploy/home/librarian.sh run
deploy/home/librarian.sh replay ~/.local/share/pa-home-synthetic/replays/<name>   # a rebuild, or an eval wiki
deploy/home/librarian.sh replay <replays>/<name> --window-start <time>              # a backfill's window only
```

With `--window-start` (ISO 8601), the first batch is the window start's week, from the window start, and nothing that occurred before it is ever offered. The run log records it, so `--resume` keeps it.

`librarian.sh` sets `PA_LIBRARIAN_SANDBOX` to `librarian-sandbox.sh`, which `pa` calls around the run (or the whole replay):

- **start**, before any session:
  - it shares L0 and the run's rendered librarian project read-only, at `/srv/pa/l0` and `/srv/pa/librarian`, and the wiki writable, at `/srv/pa/wiki`;
  - it starts the VM and writes `api.anthropic.com` into its `/etc/hosts`, at an address the devbox resolved and checked against the API's range;
  - it refuses a wiki that isn't on a `nosuid,nodev,noexec` filesystem (the data root's `wiki/`, or a directory under `replays/`), a project inside the wiki, and a VM that's already running, so runs never overlap.
- **claude**, for each session: it runs Claude Code in the VM as `librarian`, in the project, with the setup token passed through stdin into `CLAUDE_CODE_OAUTH_TOKEN`. The token is never on the VM's disk or a command line.
- **kill**, when a session runs out of time: it kills every process of `librarian` in the VM and waits until none is left, before the run discards what the session wrote. Killing `incus exec` on the devbox, which `pa` does first, doesn't stop what it started in the VM.
- **stop**, afterwards, even after a failure:
  - it deletes `~/.claude` in the VM, since the sessions' transcripts hold private data and a snapshot would keep them;
  - it stops the VM and takes the shares off, so the VM at rest shares nothing.

Its fences:

- **Network.**
  - The VM has no Tailscale and a bridge of its own, `palib0`, whose dnsmasq serves DHCP only (`raw.dnsmasq=port=0`), so nothing in the VM can resolve a name or send data out in one.
  - An Incus ACL on its NIC, `pa-librarian-egress` (`librarian/egress-acl.json`), lets out TCP 443 to `160.79.104.0/23`, the API's published inbound range, and drops and logs everything else both ways. Drops show in `journalctl -k`.
  - Two more fences stand beside it: the `pa_vm` table allows only DHCP to the devbox and TCP 443 to that range, and ufw's route rule names only that range.
- **Managed settings** (`librarian/managed-settings.json`, at `/etc/claude-code/managed-settings.json`, root's):
  - they deny every tool but `Read`, `Edit`, `Write`, `Glob` and `Grep`, in every permission mode, and lock out bypass mode;
  - they block every MCP server;
  - they turn off auto memory, claude.ai connectors, nonessential traffic, telemetry and updates.
- **Claude Code** is a pinned release, `PA_LIBRARIAN_CLAUDE_VERSION` in `lib.sh`. The devbox downloads it and checks the manifest's signature against Anthropic's key fingerprint and the binary against the manifest's checksum. It then pushes the binary to `/usr/local/bin/claude`, root's, so the VM never contacts the download host and the librarian can't replace it. Bump the version deliberately, since a release can add tools; `check-librarian.sh` fails on any tool beyond the five.
- **Auth** is a setup token (`claude setup-token`) at `~/.config/pa/librarian.token` on the devbox, mode 0600. It can only make model requests.
- **Mounts**: L0 and the project read-only, enforced on the devbox, and the wiki writable. Nothing else is shared, the wiki's git directory never. The project is the sessions' working directory, and no writable directory is it or above it.

`check-librarian.sh` starts the VM as a run does and checks every fence from inside it. That includes two short sessions on Haiku, one with no flags and one with every flag that tries to reopen a tool (bypass mode, the shell and web tools named, an MCP server). Each must have exactly the five file tools. The wizard runs it.

### Live runs

In v0.12.1 the wiki changes only when a run is started by hand; nightly runs come in v0.13. After chatting from the phone, land the chats and run the librarian:

```sh
deploy/home/ingest.sh && deploy/home/librarian.sh run
```

- The run takes what's been captured since its cursor, so each chat is offered once. A correction the owner made in chat ("Okie is Ben Okafor") shows up in the wiki after this. The assistant reads the wiki at `/srv/pa/wiki` as soon as the run commits.
- `git --git-dir ~/.local/share/pa-home-synthetic/wiki.git log` is the operations log. Each commit records its batch, cursor, librarian version, cost and turns, and lint's warnings. `git revert` of one run's commit undoes exactly that run.
- A failed run leaves the wiki as it was, and its episodes stay new for the next run.
- `pa librarian run` takes the data root's lock, and skips (exiting 0) while a capture cycle or another run holds it. Every run, failed or not, records in the status the time the wiki is current through (its cursor's capture time) and the run's outcome: when it ran, its cost and lint warnings, and a failure's reason, which names only the session it failed in. The assistant reads it at `/srv/pa/status/status.json`, and searches L0 directly for anything captured after that time.

### The Obsidian view

The owner browses the wiki read-only in Obsidian, on the Mac and the phone. The Mac pulls a clone of `wiki.git` over SSH on the tailnet, and iCloud Drive carries the clone's files to the phone. Nothing is ever pushed back to the devbox, and the librarian is the wiki's only writer. Citations are plain `[ep:…]` text, so the graph view shows only people, organizations, topics and day logs.

iCloud is a two-way sync off the owner's devices, which the spec first ruled out (#56, story 109). The owner chose it on 2026-10-22, over Working Copy Pro (#75):
- Apple holds an encrypted copy. It's end-to-end encrypted only if Advanced Data Protection is on.
- An edit made on the phone syncs back only as far as the Mac's clone, never to the devbox.

On the Mac, clone into Obsidian's iCloud folder, with the git directory outside iCloud, since iCloud's sync can corrupt one:

```sh
vault="$HOME/Library/Mobile Documents/iCloud~md~obsidian/Documents/pa-wiki"
git clone --separate-git-dir ~/.pa-wiki.git devbox:.local/share/pa-home-synthetic/wiki.git "$vault"
cd "$vault"
git remote set-url --push origin no-push      # pushing fails
echo .obsidian/ >> ~/.pa-wiki.git/info/exclude   # Obsidian's settings stay out of git
git pull --ff-only                            # to refresh, on the Mac
```

- **Mac:** `pa-wiki` shows up among Obsidian's iCloud vaults.
- **Phone:** Obsidian lists the same vault once iCloud has synced it. The phone sees a refresh only after the Mac pulls.
- **Reading view:** set Obsidian's Settings → Editor → Default view for new tabs to Reading view on both, so an accidental edit doesn't dirty the clone. If `git pull --ff-only` fails because of one, `git checkout -- .` puts the clone back.

A committed eval wiki opens the same way from any clone of this repo: open `evals/wikis/<corpus>/<librarian version>/wiki/` as a vault.

## The vault copy

The vault collector reads the owner's daily notes from a copy of their Obsidian vault on the devbox, at `~/.local/share/pa-vault`. Obsidian's headless Sync client keeps it, pull-only, as `pa-vault-sync.service`. Set it up with `setup-wizard.sh --vault`:

1. **Sync.** The owner subscribes to Obsidian Sync, moves the vault out of iCloud on the Mac and sets it up on Sync, end-to-end encrypted, then sets the phone up from Sync, off iCloud ([Switch to Obsidian Sync](https://obsidian.md/help/sync/switch)). Obsidian advises against Sync on a folder iCloud syncs too.
2. **The client.** `install-vault-sync.sh` installs it under `~/.local/share/pa/vault-sync/`:
   - Node `PA_NODE_VERSION` (22) from nodejs.org, checked against `PA_NODE_SHA256` in `lib.sh` before it's unpacked;
   - `obsidian-headless` at `PA_VAULT_SYNC_VERSION`, with `npm ci` from `vault-sync/package-lock.json`, which pins every package with its checksum. better-sqlite3's install script fetches its prebuilt binary from its GitHub releases.

   The client is in open beta, so bump either pin deliberately: change `lib.sh` and regenerate the lockfile (`npm install --package-lock-only --ignore-scripts` in `vault-sync/`), then run `install-vault-sync.sh`, which restarts the service.
3. **Login.** `ob login` (`~/.local/share/pa/vault-sync/bin/ob`) takes the Obsidian account's email, password and 2FA code. The client keeps its login, each vault's encryption key and a sync log in `~/.config/obsidian-headless/`, mode 0700.
4. **Setup.** `ob sync-setup --vault <name> --path ~/.local/share/pa-vault --device-name pa-devbox` asks for the vault's encryption password, and `ob sync-config --path ~/.local/share/pa-vault --mode pull-only` sets pull-only before the first sync. The wizard then writes `~/.config/pa/vault.env`: `PA_VAULT_DIR` and `PA_VAULT_DAILY_NOTES`, the daily-notes folder relative to the vault, as Obsidian's Daily notes settings name it. The capture cycle reads them.
5. **The service.** `install-vault-sync.sh --enable` starts the unit.

What holds it:

- **Pull-only.** The client downloads what the owner's devices change and ignores any change made on the devbox, so nothing written into the copy ever reaches them. (`mirror-remote` would revert such a change instead; the spec chose pull-only.) The unit runs `pa-vault-sync run`, which refuses to sync a vault that isn't set up pull-only, with status 78. The unit doesn't restart on that, nor on the client's "not logged in" (2) or "no sync set up" (3). It restarts on anything else, forever, 30 seconds apart.
- **Nothing in the PA writes the copy.** The collector only reads it. The unit runs as the devbox user and can write only the copy and the client's state (`ProtectSystem=strict`, `ProtectHome=read-only`).
- **No VM sees it.** The copy and the client's state are outside the data root, and no VM is shared them. `check-store.sh` and `check-vault-sync.sh` both fail if any VM is shared either, a directory in them, or a directory holding them.
- **Note names stay out of the journal.** The client prints each note it downloads by name, so the unit drops its output; its errors still reach the journal. Its own log, in `~/.config/obsidian-headless/sync/<vault id>/sync.log`, keeps every name. That log, the journal and the copy are real content: a development session reads only `check-vault-sync.sh`, which reports counts and times.

To check it, run `check-vault-sync.sh`. It checks the pins, pull-only, the unit (enabled, running, how many restarts), the modes, that no VM is shared the copy, and how many daily notes there are and when the newest changed. The wizard's last stage proves the two directions from the phone:

- a daily note edited on the phone reaches the copy within minutes;
- a file written into the copy (`pa-pull-only-check.md`, removed afterwards) never shows on the phone or the Mac.

Re-auth: if the client's login is revoked, the unit stops with status 2. Run `ob login`, then `sudo systemctl restart pa-vault-sync`. If the vault's encryption password changes, run `ob sync-unlink --path ~/.local/share/pa-vault`, then `setup-wizard.sh --vault` again: its sync stage sets the vault up afresh, pull-only.

## Gmail

The capture cycle (`pa capture`) collects the owner's one Gmail account through the Gmail API, read-only (`gmail.readonly`), when `PA_GMAIL_TOKEN` is set. Every message but spam, trash, and drafts lands in `inbox/gmail/` as its raw bytes, attachments included, behind an `X-Gmail-Labels` header in Takeout's names, so Promotions and Social are captured but never offered to the librarian, and the owner's sent mail passes the forged-mail check. Mail is never reconciled: what the owner deletes or archives in Gmail stays in L0.

- **The OAuth client** comes from the owner's own Google Cloud project: a Desktop app client, the project published "In production" and left unverified, for personal use, so its refresh token doesn't expire weekly. It's at `~/.config/pa/gmail-client.json` (`PA_GMAIL_CLIENT`), mode 0600.
- **The token** is at `~/.config/pa/gmail-token.json` (`PA_GMAIL_TOKEN`), mode 0600, never in the store or the repository. A collection refuses a token anyone else can read.
- **Setup** is the wizard's Gmail stages, run once in the go-live ticket: `deploy/home/setup-wizard.sh --gmail`.
- **Backfill first.** A collection reads Gmail's history from the position the backfill recorded, and fails until there is one: `pa capture --backfill <window start>` (ISO 8601; the owner's time zone without an offset). A position Gmail no longer keeps (the devbox off for a week or more) leads to a listing of the time since the last collection, and ingest collapses what's already in L0.
- **The dry run** counts what a backfill would capture, by category, from labels alone, without fetching any message, and prints counts only:

  ```sh
  deploy/home/gmail.sh dry-run --window-start 2026-09-01
  ```

### Re-auth

The token dies after six months unused or a password change. Gmail's watermark in `pa status` then goes stale, and its last failure says to run `pa gmail auth`. To re-auth, forward the consent's port from the machine with the browser, then ask for consent again on the devbox:

```sh
ssh -L 8765:localhost:8765 <devbox>   # on the machine with the browser, left open
deploy/home/gmail.sh auth             # on the devbox; open the address it prints
```

Google warns that the app isn't verified: it's the owner's own, so click through (Advanced → Go to the app). The wizard's stage G2 (`setup-wizard.sh --gmail`) walks the same steps. The next capture cycle carries on from the stored history position.

## Hand-run tests with the real model

The suite skips the tests that call a real model unless asked, since they cost money and depend on the model. Run them by hand on the devbox:

```sh
PA_SMOKE_CLAUDE=1 uv run pytest -k smoke                 # the claude CLI on the host (Haiku unless PA_SMOKE_CLAUDE_MODEL)
PA_SMOKE_LIBRARIAN=1 uv run pytest -s deploy/home/tests/test_corrections_fixture.py   # the corrections fixture in pa-librarian
```

The corrections fixture (`deploy/home/tests/fixtures/corrections/`) shows the owner correcting the wiki by chat. It seeds a data root with a starting wiki, then makes two real librarian runs in `pa-librarian`, as `librarian.sh run` does, each with a fixed "now":

1. After chats that say Okie is Ben Okafor, that the two Chris Lees are different people, and that a roofer's estimate came in, and that answer a bare "yes" to the assistant's restatement of a changed Thanksgiving plan. The test checks the merge (one page, both names, links rewritten, the chat cited), the split (noted on both pages with the chat's citation), the closed loop, and the decision citing the "yes". It also checks that nothing the assistant said unconfirmed reaches the wiki.
2. After new mail and a note from both Chris Lees and from Okie. The test checks that the split still holds and the merge too.

The data root goes under `replays/` (or `PA_SMOKE_CORRECTIONS_DIR`, which must be on that filesystem too) and is kept for reading. The test prints each run's cost: the two runs come to about $1 on Opus 5.5 at medium effort.

## Evals in the VM

```sh
deploy/home/run-evals.sh                               # --agent claude --judge model
deploy/home/run-evals.sh --agent claude --judge model --agent-model sonnet --judge-model sonnet
deploy/home/run-evals.sh --agent claude --judge model --corpus argus --runs 3   # run-1/ to run-3/ and comparison.md
deploy/home/run-evals.sh --agent claude --judge model --corpus scale --runs 3   # the scale corpus and its 40 questions
deploy/home/run-evals.sh --agent claude --judge model --corpus scale --runs 3 \
  --configuration files-plus-wiki --wiki evals/wikis/scale/<librarian version>   # with the scale eval wiki
uv run pa-eval compare evals/baselines/v0.12/scale-files-alone evals/runs/<run>  # side by side, on the devbox
```

A files-plus-wiki run's `--wiki` is an eval wiki on the devbox: a replay's `wiki/` and `run-log.jsonl` (`pa librarian replay`), kept in `evals/wikis/<corpus>/<librarian version>/` and committed when a reported result uses it, never with its `wiki.git/`. `run-evals.sh` pushes that copy into the VM (replacing the last one) and points the run at it; the run lays it beside its throwaway L0 with nothing writable, and its report names the librarian version that built it.

The run uses the code and agent project at the VM's deployed commit, under the VM's managed settings and sandbox like every session there, builds its throwaway data root on the VM's own disk in `/srv/pa/eval` (never the mounted store) with exchange capture off, and fails if `inbox/assistant_chat/` changed while it ran. The run directory is copied back to `evals/runs/` in this checkout. Claude Code in the VM must be logged in (the wizard's login stage).

### Real-data runs over a cut

```sh
PA_DATA_DIR=<data root> uv run pa cut          # cuts/<as-of>/ under the data root, as of now
deploy/home/run-evals.sh --agent claude --judge model --agent-model sonnet --judge-model sonnet \
  --cut <data root>/cuts/<as-of> --eval-set <data root>/evals/<set>.json --runs 3 \
  [--configuration files-plus-wiki]           # the cut's own wiki; no --wiki
```

A cut holds every episode captured by the moment it was taken (hard-linked from L0), the live wiki at its last commit, and a copy of the status file. `pa cut` takes the data root's lock, so it's refused while a capture cycle or librarian run holds it. `run-evals.sh` pushes the cut into the VM once (`/srv/pa/eval/cuts/<as-of>`) and reuses that copy on later runs; the eval set is pushed every run. The run asks the cut as it is, with no ingest, and its directory comes back to `<data root>/evals/runs/` (or `$PA_EVAL_RUNS_DIR`). The cut, the eval set, and the run directory are refused inside this checkout, by `run-evals.sh` and again by `pa-eval run`. A set naming an episode that isn't in the cut is refused before anything runs.

### Drafting the real-data eval set

```sh
deploy/home/draft-evals.sh --cut <data root>/cuts/<as-of> [--agent-model sonnet]
                                              # <data root>/evals/drafts/<as-of>/draft-<time>.json
deploy/home/draft-evals.sh --cut <data root>/cuts/<as-of> \
  --revise <data root>/evals/drafts/<as-of>/draft-<time>.json \
  --notes <data root>/evals/drafts/<as-of>/notes.md   # a second pass: a new draft beside it
```

A headless session in the assistant's VM reads the cut's L0 (a read-only copy, never the cut's wiki), with exchange capture off, and drafts about 25 questions (`--questions N`), each with an expected answer and evidence episode ids. It's asked at the cut's as-of time, as the owner in the VM's `owner.env`, with the prompt versioned in `evals/src/pa_evals/draft_prompts/`. Questions get opaque ids (`r01`, `r02`, ...), and a draft naming an episode that isn't in the cut is thrown away whole. The draft comes back mode 0600 under `<data root>/evals/drafts/<as-of>/` (or `$PA_EVAL_DRAFTS_DIR/<as-of>/`), and is never written over.

The owner corrects the draft in place, adds questions of their own, and, for a second pass, writes their notes beside it. The second pass revises the draft as the owner left it, keeps the ids of the questions it keeps, and numbers new ones after the last. Drafts and notes are refused inside this checkout, by `draft-evals.sh` and again by `pa-eval draft`; what either prints says how much was drafted, never what. The session may take up to an hour (`--timeout` seconds) before it fails.

## Updating and rolling back

```sh
deploy/home/update.sh <commit|branch|tag>   # snapshot, then deploy
deploy/home/rollback.sh                     # back to the latest pre-update snapshot
incus snapshot list pa-home                 # every snapshot
deploy/home/update.sh --vm librarian        # snapshot the librarian's VM, then install Claude Code
deploy/home/rollback.sh --vm librarian      # back to its latest pre-update snapshot, left stopped
```

The assistant's VM reaches no package index or git host, so `update.sh` pushes everything in from the devbox, and nothing in the VM goes online:

- **The code.** `<ref>` is resolved in this checkout (`git fetch` first, and name it as git does, `origin/v0.13` say), so it needn't be pushed to GitHub. It goes in as a git bundle; `install-code.sh`, as root, checks it out in `/opt/pa`, with no remote.
- **Its dependencies.** The devbox syncs a scratch copy of the commit with a fresh uv cache, for the VM's Python version, and pushes that cache in as a wheelhouse, with the devbox's own uv (`/usr/local/bin/uv`), which reads it as it was written. In the VM, `uv sync --locked --no-dev` runs offline from it (`UV_OFFLINE=1`), on `/usr/bin/python3`, never downloading an interpreter. The bundle and the wheelhouse are removed afterwards.
- **Claude Code** at `PA_ASSISTANT_CLAUDE_VERSION` (`lib.sh`), downloaded on the devbox and checked as the librarian's is: the manifest's signature against Anthropic's key fingerprint, the binary against the manifest's checksum. It goes to `/usr/local/bin/claude`, root's, so the assistant can't replace it, and the copy the assistant once installed itself (`~/.local/bin/claude`) is removed. Its auto-updates are off in the managed settings (`claude update` says updates are disabled by the administrator). Bump the pin deliberately, since a release can add tools: change `lib.sh`, run `update.sh`, then `check-isolation.sh`, which fails on any tool beyond the expected five.

Anything that can fail on the devbox (the ref, the download, the wheelhouse) fails before the VM is snapshotted or touched.

The librarian's VM holds no state between runs (the wiki is on the devbox and the token stays there), so its OS packages come from the image it was created from. To refresh them, delete it (`incus delete pa-librarian`) and run `create-vm.sh --vm librarian` and `update.sh --vm librarian` again.

A rollback restores the VM's disk, not its profile, bridge or firewall, so a VM rolled back to a snapshot from before #91 is still fenced, but has Tailscale again: run `fence-vm.sh`. A snapshot from before #94 has no `capture` user and the assistant at uid 1000: `mount-store.sh`, which `rollback.sh` runs last, refuses it, though the VM is back with its shares as they were. Run `rollback.sh` from a checkout of that snapshot's commit, or `update.sh` again to move the assistant off uid 1000. A snapshot of a VM still on the build bridge (`pre-fence-*` of a rebuilt VM) brings that NIC back, and `mount-store.sh` refuses it until `fence-vm.sh` runs.

### Rebuilding the assistant's VM

The VM's OS is never upgraded in place: refresh it by rebuilding the VM. The order keeps the store and the login away from an open network: the VM is built open before any share or credential exists, and fenced before anything is mounted.

```sh
incus exec pa-home -- systemctl stop pa-remote-control
incus stop pa-home
incus rename pa-home pa-home-old                  # kept until the new one works
incus config set pa-home-old boot.autostart=false
incus config device remove pa-home-old pa-l0 pa-chat-inbox pa-wiki pa-status
deploy/home/create-vm.sh                          # open, on pabuild0
deploy/home/fence-vm.sh                           # onto pabr0, pinned, no Tailscale
deploy/home/mount-store.sh                        # refused before the fence
deploy/home/update.sh <ref>
deploy/home/setup-wizard.sh                       # its login, trust and Remote Control stages
deploy/home/check-isolation.sh && deploy/home/check-store.sh
incus delete pa-home-old                          # its transcripts and login go with it
```

The login, the project's trust and Remote Control's consent live on the old VM's disk, so the owner answers them again (the wizard's stages 11, 15 and 16), and re-opens the assistant on the phone. To try a rebuild without touching the live VM, give the scripts another name, `PA_ASSISTANT_VM=pa-rebuild-test`, then `incus delete` it and `sudo systemctl disable pa-pins@pa-rebuild-test.service`.

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

## Acceptance run (#66)

1. `setup-wizard.sh --librarian` runs, and ends with `check-librarian.sh` passing; the assistant's checks (`check-isolation.sh`, `check-store.sh`) still pass.
2. `deploy/home/librarian.sh run` over the live data root completes a run in the VM and commits it to `wiki.git`; `pa-librarian` is stopped afterwards and shares nothing (`incus config device list pa-librarian`).
3. Roll back:
   ```sh
   deploy/home/update.sh --vm librarian
   incus start pa-librarian && incus exec pa-librarian -- touch /root/rollback-marker && incus stop pa-librarian
   deploy/home/rollback.sh --vm librarian
   incus start pa-librarian && incus exec pa-librarian -- test ! -e /root/rollback-marker && echo "rolled back"
   incus stop pa-librarian
   ```
4. Reboot the devbox: `pa-librarian` stays stopped, the wiki and replays are mounted, and `check-librarian.sh` passes.

## Acceptance run (#87)

1. `setup-wizard.sh --vault` runs, and ends with `check-vault-sync.sh` passing: Node and the client at their pins, the vault pull-only, `pa-vault-sync.service` enabled and running, and no VM shared the copy.
2. Its last stage shows a daily note edited on the phone reaching the copy within minutes, by the newest note's change time, and the wizard's `pa-pull-only-check.md` never reaching the phone.
3. `check-store.sh` passes, its vault line included.
4. Reboot the devbox: `check-vault-sync.sh` passes again.

## Acceptance run (#91)

1. `check-isolation.sh` passes on `pa-home`: the web, the devbox, the LAN and tailnet peers unreachable, no DNS and no Tailscale, the API reachable at its pinned names, the OS's updates masked and Claude Code at its pin.
2. It fails while an ordinary website is reachable, shown on a throwaway VM, which holds no store and no login, while it's still on the build bridge:
   ```sh
   export PA_ASSISTANT_VM=pa-rebuild-test
   deploy/home/create-vm.sh
   deploy/home/check-isolation.sh       # FAIL: not fenced, example.com resolves and is reachable
   ```
3. The rebuild applies the fence before the store is mounted, on the same VM:
   ```sh
   deploy/home/mount-store.sh           # refused: pa-rebuild-test isn't fenced
   deploy/home/fence-vm.sh
   deploy/home/mount-store.sh           # mounts
   deploy/home/check-isolation.sh       # the network lines pass (the session lines need a login)
   incus delete --force pa-rebuild-test && sudo systemctl disable pa-pins@pa-rebuild-test.service
   unset PA_ASSISTANT_VM
   ```
4. `incus exec pa-home -- systemctl restart pa-remote-control`, then `incus restart pa-home`: each time the service comes back connected, with no new login, and the phone reaches it.
5. An update with no internet in the VM: `update.sh <ref>` installs the pinned Claude Code, checks out the ref and syncs the venv offline, and `check-isolation.sh` passes.
