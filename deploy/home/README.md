# deploy/home

The PA's two VMs on the devbox:

- the assistant's, `pa-home`: an Incus VM that reaches the public internet and nothing of the owner's, and reads the store without being able to change it;
- the librarian's, `pa-librarian`: a second Incus VM, made with the same scripts (`--vm librarian`), that reaches only the Anthropic API and writes nothing but the wiki ([The librarian's VM](#the-librarians-vm)).

See the Home hosting bullet in the [roadmap](../../docs/roadmaps/v0.1-prototype.md).

## Setting up

Run the wizard on the devbox. It walks through the steps only the owner can do and calls the scripts below for the rest. Each stage checks its result, and re-running it skips what's done.

```sh
deploy/home/setup-wizard.sh              # both VMs, then the vault copy
deploy/home/setup-wizard.sh --librarian  # the librarian's VM alone
deploy/home/setup-wizard.sh --vault      # the vault copy alone: Obsidian Sync and its headless client
```

The scripts that both VMs share take `--vm assistant|librarian`, and work on the assistant's VM without it.

| Script | Does |
|---|---|
| `incus-setup.sh [--vm]` | Creates the `pa` btrfs storage pool (copy-on-write, so snapshots are cheap) and the VM's own bridge, `pabr0`, with IPv6 off. For the librarian: `palib0`, with DNS off too, and its egress ACL |
| `install-firewall.sh` | Installs the bridge firewall (`firewall/`) as `pa-vm-firewall.service`, for both bridges |
| `create-vm.sh [--vm]` | Creates `pa-home` from `profile.yaml` (4 vCPU, 6 GiB RAM, 40 GiB disk, cloud-init, user `assistant`, Tailscale). For the librarian: `pa-librarian` from `librarian/profile.yaml` (2 vCPU, 3 GiB RAM, 10 GiB disk, user `librarian`), left stopped |
| `seed-data-root.sh [--corpus argus\|scale]` | Creates the instance's data root on the devbox from a frozen synthetic corpus (Argus's unless `--corpus scale`), with its owner as the instance owner |
| `install-chat-inbox.sh` | Makes `inbox/assistant_chat/` a 1 GiB filesystem of its own, mounted `nosuid,nodev,noexec` at every boot before Incus (needs sudo), with `install-own-filesystem.sh` |
| `install-wiki.sh` | Does the same for `wiki/` (1 GiB) and `replays/` (4 GiB), which the librarian's VM writes |
| `mount-store.sh` | Mounts L0 and the wiki into the VM read-only and `inbox/assistant_chat/` writable |
| `update.sh <ref>` | Snapshots the VM, puts the repo at `<ref>` and runs `uv sync`, then renders the agent project and installs the Remote Control unit, restarting it once it's enabled; the first run installs uv and Claude Code |
| `install-remote-control.sh [--enable]` | Installs `pa-remote-control.service` in the VM and restarts it if it's enabled; `--enable` enables it first |
| `update.sh --vm librarian` | Snapshots the librarian's VM, then installs the pinned Claude Code and the managed settings (`install-librarian-claude.sh`) |
| `rollback.sh [--vm] [snapshot]` | Restores the VM to a snapshot, by default the latest one `update.sh` took |
| `ingest.sh` | Ingests the instance's inbox on the devbox, landing the exchanges the VM captured |
| `run-evals.sh [options]` | Runs `pa-eval run` in the VM and copies the run back to `evals/runs/` (or `$PA_EVAL_RUNS_DIR`); a run over a cut pushes the cut once and comes back under the data root |
| `draft-evals.sh --cut DIR [--revise DRAFT --notes FILE]` | Has a session in the VM draft the real-data eval set from a cut (or revise a draft by the owner's notes), and brings the draft back under the data root |
| `check-isolation.sh` | Probes the fences from inside the VM |
| `check-store.sh` | Checks the store's mounts from inside the VM |
| `librarian-sandbox.sh start\|stop\|claude\|kill` | The librarian's VM as the sandbox its sessions run in: `PA_LIBRARIAN_SANDBOX` |
| `librarian.sh run\|replay\|seed …` | Runs `pa librarian` on the devbox over the data root, its sessions in the librarian's VM |
| `check-librarian.sh` | Checks the librarian VM's fences from inside it |
| `install-vault-sync.sh [--enable]` | Installs Obsidian's headless Sync client at its pinned version on a pinned Node 22, and `pa-vault-sync.service`; `--enable` starts it once the vault is set up pull-only ([The vault copy](#the-vault-copy)) |
| `check-vault-sync.sh` | Checks the vault copy: the pins, pull-only, the service, no VM shared it, and the daily notes arriving, by counts and times |

`tailscale-policy.hujson` holds the parts of the tailnet policy the VM needs, for merging into the policy in the admin console.

## The fences

- **Host firewall** (`firewall/pa-vm.nft`): its own nftables table on the bridge. Anything the VM sends to the devbox, at any of its addresses, is dropped except DNS and DHCP. Anything it sends on to private ranges (RFC1918, which covers the LAN and Docker's networks), the tailnet range (100.64.0.0/10) or link-local is dropped, and so is all IPv6. A drop in this table is final whatever Incus's, Docker's or Tailscale's own rules accept.
- **Tailnet policy**: the VM joins as `tag:pa`. The owner's devices may reach it, and it is the source of no rule. Its traffic to tailnet peers travels inside Tailscale's tunnel, so only the tailnet policy can stop it.

The devbox also runs ufw, which drops input and forwarded traffic by default. `install-firewall.sh` adds ufw rules letting the bridge's DHCP, DNS and outbound traffic through, and the `pa_vm` table still fences everything else.

The firewall loads at boot from `pa-vm-firewall.service`, after Docker and before Incus. Incus requires it, so if the rules fail to load, Incus doesn't start and the VM stays down. Don't enable `nftables.service` to persist rules: Ubuntu's `/etc/nftables.conf` starts with `flush ruleset`, which would wipe Docker's and Incus's rules at boot.

## The store

The data root stays on the devbox, at `~/.local/share/pa-home-synthetic/`. In v0.11 it is seeded from the frozen synthetic corpus, with the corpus's fictional owner as the instance owner (in `owner.env` beside it), and it is disposable: v0.13 starts a fresh one for real sources. Ingest runs on the devbox, by hand (`ingest.sh`).

Three directories of it are mounted into the VM, and nothing else, the catalog and the wiki's git directory (`wiki.git/`) included:

| On the devbox | In the VM | |
|---|---|---|
| `l0/` | `/srv/pa/l0` | Read-only. Enforced on the devbox, so even root in the VM can't write it |
| `inbox/assistant_chat/` | `/srv/pa/inbox/assistant_chat` | Writable: the VM's only way back. Ingest makes anything written there an `assistant_chat` episode, or quarantines it ([ADR-0004](../../docs/adr/0004-inbox-directory-sets-source.md)) |
| `wiki/` | `/srv/pa/wiki` | Read-only, like L0. The librarian keeps it on the devbox; `mount-store.sh` creates it empty if it doesn't exist yet |

`update.sh` copies `owner.env` into the VM and renders the agent project from it into `/home/assistant/agent`, pointing it at `/srv/pa/l0`, with `/home/assistant/scratch` as its scratch directory, and at `/srv/pa/wiki` once the librarian has run into the wiki (its `index.md` is there); until then the assistant runs on files alone, so run `update.sh` again after the librarian's first run. Each update deletes the rendered project and renders it again at the same path.

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

## Remote Control

`pa-remote-control.service` in the VM runs `claude remote-control` in the rendered agent project, as `assistant`, so the owner chats with the assistant from the Claude app on their phone and from claude.ai/code. Both list it as `pa-home → agent`: the VM's hostname and the project's folder. It's the only thing that turns exchange capture on (`PA_CAPTURE_EXCHANGES=1`, with `PA_CAPTURE_INBOX` set to the inbox directory), so each exchange of a session lands in the inbox for `ingest.sh`.

- It runs on the `claude auth login` subscription login. Remote Control refuses `setup-token` tokens.
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

The run uses the code and agent project at the VM's deployed commit, builds its throwaway data root on the VM's own disk (never the mounted store) with exchange capture off, and fails if `inbox/assistant_chat/` changed while it ran. The run directory is copied back to `evals/runs/` in this checkout. Claude Code in the VM must be logged in (the wizard's login stage).

### Real-data runs over a cut

```sh
PA_DATA_DIR=<data root> uv run pa cut          # cuts/<as-of>/ under the data root, as of now
deploy/home/run-evals.sh --agent claude --judge model --agent-model sonnet --judge-model sonnet \
  --cut <data root>/cuts/<as-of> --eval-set <data root>/evals/<set>.json --runs 3 \
  [--configuration files-plus-wiki]           # the cut's own wiki; no --wiki
```

A cut holds every episode captured by the moment it was taken (hard-linked from L0), the live wiki at its last commit, and a copy of the status file. `pa cut` takes the data root's lock, so it's refused while a capture cycle or librarian run holds it. `run-evals.sh` pushes the cut into the VM once (`~/.cache/pa-eval/cuts/<as-of>`) and reuses that copy on later runs; the eval set is pushed every run. The run asks the cut as it is, with no ingest, and its directory comes back to `<data root>/evals/runs/` (or `$PA_EVAL_RUNS_DIR`). The cut, the eval set, and the run directory are refused inside this checkout, by `run-evals.sh` and again by `pa-eval run`. A set naming an episode that isn't in the cut is refused before anything runs.

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

The librarian's VM holds no state between runs (the wiki is on the devbox and the token stays there), so its OS packages come from the image it was created from. To refresh them, delete it (`incus delete pa-librarian`) and run `create-vm.sh --vm librarian` and `update.sh --vm librarian` again.

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
