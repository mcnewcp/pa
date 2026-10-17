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
| `mount-store.sh` | Mounts L0 and the claims graph into the VM read-only and `inbox/assistant_chat/` writable |
| `update.sh <ref>` | Snapshots the VM, puts the repo at `<ref>`, runs `uv sync` and fetches LadybugDB's pinned full-text extension, then renders the agent project and installs the Remote Control unit, restarting it once it's enabled; the first run installs uv and Claude Code |
| `install-remote-control.sh [--enable]` | Installs `pa-remote-control.service` in the VM and restarts it if it's enabled; `--enable` enables it first |
| `rollback.sh [snapshot]` | Restores the VM to a snapshot, by default the latest one `update.sh` took |
| `ingest.sh` | Ingests the instance's inbox on the devbox, landing the exchanges the VM captured |
| `run-evals.sh [options]` | Runs `pa-eval run` in the VM and copies the run back to `evals/runs/` |
| `check-isolation.sh` | Probes the fences from inside the VM |
| `check-store.sh` | Checks the store's mounts from inside the VM |
| `install-explorer.sh [--serve]` | Builds our Ladybug Explorer image (`explorer/`) and runs it as the `pa-explorer` container; `--serve` also puts it on the tailnet with `tailscale serve` |
| `check-explorer.sh` | Checks the explorer: the current build, the refused routes, no writes, and who can reach it |

`tailscale-policy.hujson` holds the parts of the tailnet policy the VM needs, for merging into the policy in the admin console.

## The fences

- **Host firewall** (`firewall/pa-vm.nft`): its own nftables table on the bridge. Anything the VM sends to the devbox, at any of its addresses, is dropped except DNS and DHCP. Anything it sends on to private ranges (RFC1918, which covers the LAN and Docker's networks), the tailnet range (100.64.0.0/10) or link-local is dropped, and so is all IPv6. A drop in this table is final whatever Incus's, Docker's or Tailscale's own rules accept.
- **Tailnet policy**: the VM joins as `tag:pa`. The owner's devices may reach it, and it is the source of no rule. Its traffic to tailnet peers travels inside Tailscale's tunnel, so only the tailnet policy can stop it.

The devbox also runs ufw, which drops input and forwarded traffic by default. `install-firewall.sh` adds ufw rules letting the bridge's DHCP, DNS and outbound traffic through, and the `pa_vm` table still fences everything else.

The firewall loads at boot from `pa-vm-firewall.service`, after Docker and before Incus. Incus requires it, so if the rules fail to load, Incus doesn't start and the VM stays down. Don't enable `nftables.service` to persist rules: Ubuntu's `/etc/nftables.conf` starts with `flush ruleset`, which would wipe Docker's and Incus's rules at boot.

## The store

The data root stays on the devbox, at `~/.local/share/pa-home-synthetic/`. In v0.11 it is seeded from the frozen synthetic corpus, with the corpus's fictional owner as the instance owner (in `owner.env` beside it, with the owner's time zone, `PA_TIME_ZONE`), and it is disposable: v0.13 starts a fresh one for real sources. Ingest runs on the devbox, by hand (`ingest.sh`).

Three directories of it are mounted into the VM, and nothing else, the catalog and the librarian's cache included:

| On the devbox | In the VM | |
|---|---|---|
| `l0/` | `/srv/pa/l0` | Read-only. Enforced on the devbox, so even root in the VM can't write it |
| `inbox/assistant_chat/` | `/srv/pa/inbox/assistant_chat` | Writable: the VM's only way back. Ingest makes anything written there an `assistant_chat` episode, or quarantines it ([ADR-0004](../../docs/adr/0004-inbox-directory-sets-source.md)) |
| `graph/` | `/srv/pa/graph` | Read-only, like L0. The claims graph's published builds and its `current` pointer, which the PA MCP server reads |

`update.sh` copies `owner.env` into the VM and renders the agent project from it into `/home/assistant/agent`, pointing it at `/srv/pa/l0`, with `/home/assistant/scratch` as its scratch directory and the PA MCP server reading `/srv/pa/graph`. Each update deletes the rendered project and renders it again at the same path.

The shares are served by `virtiofsd` running as root on the devbox, with no id mapping. The VM's `assistant` user can write its inbox directory only because its uid is the devbox user's (both 1000); `mount-store.sh` refuses to go on if they differ. What the VM writes into `inbox/assistant_chat/` keeps the VM's owners and modes, so root in the VM could leave root-owned files there, setuid programs and device nodes included, or fill the devbox's disk. So that directory is a filesystem of its own (`install-chat-inbox.sh`): a 1 GiB ext4 image, `chat-inbox.img` in the data root, loop-mounted `nosuid,nodev,noexec` by a systemd mount unit. Nothing in it can run, setuid or not, or act as a device, and the VM can't write more than 1 GiB. The unit mounts it at boot before Incus, and Incus requires it: if it can't be mounted, Incus doesn't start and the VM stays down instead of sharing the bare directory.

Ingest reads only regular files and never follows a symbolic link (a link is quarantined unread), so it can't be made to copy a devbox file into L0, where the VM could read it. A file it isn't allowed to read is quarantined without its content, and one it isn't allowed to remove (root in the VM can take the devbox user's write permission away) is left in the inbox, not landed, so it doesn't land again on every run; `ingest.sh` names it and exits non-zero, and the rest of the inbox still lands. Either way only root on the devbox can clear it.

To start the data root over (stopping the inbox mount stops Incus too, since Incus requires it; the next `incus` command starts it again):

```sh
incus config device remove pa-home pa-l0 pa-chat-inbox pa-graph
unit=$(systemd-escape -p --suffix=mount ~/.local/share/pa-home-synthetic/inbox/assistant_chat)
sudo systemctl disable --now "$unit"
rm -rf ~/.local/share/pa-home-synthetic
deploy/home/seed-data-root.sh && deploy/home/install-chat-inbox.sh && deploy/home/mount-store.sh
```

## The claims graph in the VM

The assistant reads the claims graph through the PA MCP server, which runs in the VM over stdio and reads whichever build `/srv/pa/graph/current` points at when each call starts. Run `pa librarian rebuild` on the devbox, and the next call in a running session reads the new build, with nothing restarted.

- **Declared by the agent project.** The rendered `.mcp.json` starts the server as `<repo venv>/bin/python -m pa_home mcp serve --graph /srv/pa/graph --time-zone <PA_TIME_ZONE>`. The server has no clock; a date the assistant gives without an offset is read in that time zone, so `until=2026-10-08` covers that whole local day. A data root seeded before v0.12 needs `PA_TIME_ZONE=America/Chicago` added to its `owner.env`. Claude Code starts one server per session (and one per `claude mcp list`).
- **The only MCP server.** The rendered `settings.json` lets only a server named `pa` start (`allowedMcpServers`), allows its tools (`mcp__pa`), keeps claude.ai connectors off (`disableClaudeAiConnectors`), and pre-approves `pa` (`enabledMcpjsonServers`, since Remote Control has nobody to ask). `allowedMcpServers` is the fence, and Claude Code honours it outside managed settings: from the project's `settings.json` in Remote Control's trusted folder, and from the same file passed with `--settings` to `claude -p`. `enabledMcpjsonServers` with `enableAllProjectMcpServers: false` is no fence: it stops neither a second `.mcp.json` server nor a user-scope one. There's no deny rule on MCP tools: a deny wins over every allow, so `mcp__*` would deny the PA server's tools too. Denying single `pa` tools by name (the query-only eval configuration) removes just those.
- **How the fence was proven** (Claude Code 2.1.296, 2026-10-12, as `assistant` in the VM, in a rendered copy of the project marked trusted): a stand-in stdio server was added at user scope (`claude mcp add --scope user`) and a second one to the copy's `.mcp.json`, each writing a marker file when started. With the rendered settings, `claude -p --settings <copy>/.claude/settings.json --output-format stream-json --verbose` reported only `pa` in `mcp_servers` and only `mcp__pa__*` tools, and neither stand-in started; a `claude remote-control` started in the copy (its pre-created session is the same `--print --sdk-url …` process a phone session runs in) started `pa` alone, per its session debug log and `ps`. With `allowedMcpServers` alone, the same. With the MCP keys removed, or with `enabledMcpjsonServers` alone, both stand-ins started in both, and `-p` listed their tools. The query-only settings left `find`, `schema` and `query`. Re-run this after a Claude Code upgrade, then remove the stand-ins (`claude mcp remove --scope user <name>`, and the copy's trust entry in `~/.claude.json`).
- **Remote Control and `claude -p` load the same configuration.** Remote Control runs in the rendered project, a trusted folder, so it reads `.mcp.json` and the project's `settings.json`. `claude -p` reads `.mcp.json` from its working directory without asking, and the evals pass the project's `settings.json` with `--settings`, since `-p` never trusts a folder.
- **The full-text extension.** The server loads LadybugDB's full-text extension from `~/.local/share/pa/lbug-extensions` and never downloads it, so `update.sh` fetches it there with `pa librarian extensions`, which checks its SHA-256.
- `claude mcp list`, run as `assistant` in `/home/assistant/agent`, shows whether the server starts: `pa: … - ✔ Connected`.

## Remote Control

`pa-remote-control.service` in the VM runs `claude remote-control` in the rendered agent project, as `assistant`, so the owner chats with the assistant from the Claude app on their phone and from claude.ai/code. Both list it as `pa-home → agent`: the VM's hostname and the project's folder. It's the only thing that turns exchange capture on (`PA_CAPTURE_EXCHANGES=1`, with `PA_CAPTURE_INBOX` set to the inbox directory), so each exchange of a session lands in the inbox for `ingest.sh`.

- It runs on the `claude auth login` subscription login. Remote Control refuses `setup-token` tokens.
- It waits until the inbox directory is mounted, so no capture goes to the VM's own disk.
- It restarts on failure, but five failed starts in five minutes leave it stopped, so a permanent error such as a rejected login doesn't loop. `journalctl -u pa-remote-control` in the VM says why; fix it, then run `install-remote-control.sh` to start it again.
- `update.sh` stops it before the render and starts it again after. A session open during an update is cut off.
- The permission mode isn't pinned: the phone picks each session's mode, and the fences hold in every mode.
- Before it's enabled, two questions are answered once from a terminal as `assistant`: running `claude` in the project (theme, the "Claude can make mistakes" notice, folder trust), and running `claude remote-control` (`Enable Remote Control? (y/n)`). With no terminal, the service would wait on that question forever. The wizard asks both, checks the answers in the VM's `~/.claude.json` (`hasTrustDialogAccepted`, `remoteDialogSeen`), and only then enables the service.

Don't chat during an eval run: the run fails if the inbox changes while it runs, and every exchange changes it.

## Evals in the VM

```sh
deploy/home/run-evals.sh                               # --agent claude --judge model
deploy/home/run-evals.sh --agent claude --judge model --agent-model sonnet --judge-model sonnet
deploy/home/run-evals.sh --agent claude --judge model --corpus argus --runs 3   # run-1/ to run-3/ and comparison.md
deploy/home/run-evals.sh --agent claude --judge model --corpus scale --runs 3   # the scale corpus and its 40 questions
deploy/home/run-evals.sh --agent claude --judge model --configuration graph-plus-files   # or query-only; default files-alone
PA_LIBRARIAN_EXTRACTION_MODEL=claude-opus-5-5 deploy/home/run-evals.sh --agent claude --judge model --configuration graph-plus-files
uv run pa-eval compare evals/baselines/v0.11 evals/runs/<run>                  # side by side, on the devbox
```

The run uses the code and agent project at the VM's deployed commit, builds its throwaway data root on the VM's own disk (never the mounted store) with exchange capture off, and fails if `inbox/assistant_chat/` changed while it ran. The run directory is copied back to `evals/runs/` in this checkout. Claude Code in the VM must be logged in (the wizard's login stage).

`--configuration` says what the agent gets besides L0's files: nothing (`files-alone`, the v0.11 configuration), the PA MCP server with all its tools (`graph-plus-files`), or only `find`, `schema` and `query` (`query-only`, by per-tool deny rules in the rendered settings). A graph configuration runs the librarian over the run's throwaway L0 after ingest, with the librarian models `pa librarian rebuild` uses (`PA_LIBRARIAN_MODEL` and the rest, default Sonnet 5.5 at medium; `run-evals.sh` forwards every `PA_LIBRARIAN_*` setting in the devbox's environment into the run), and its cache in `~/.cache/pa-eval/librarian` on the VM's disk (`--librarian-cache`), so only the first run at an extractor version extracts. Then the injection checks run over the eval set's adversarial episodes, and a failure stops the run before any question is asked. The report's "Claims graph" section has the extractor version, cache hits and misses, and the librarian's model calls by stage.

## Ladybug Explorer

The owner looks at the claims graph in Ladybug Explorer, to debug bad merges and missing links and to find connections. It runs on the devbox, not in the VM, as the Docker container `pa-explorer`, and Docker starts it at boot.

- **Our image** (`explorer/Dockerfile`): upstream's source at a pinned commit, on the LadybugDB version the builder and the PA MCP server use. `explorer/package.json` and its lockfile pin it, and a test checks it against `adapters/home/pyproject.toml`. To move LadybugDB, change both, regenerate the lockfile with the image's npm, and re-run `install-explorer.sh`.
- **Nothing to change things with.** `/api/db` (reopen any path or mount) and `/api/gpt` (send the schema and questions to OpenAI) are deleted from the server, and `openai` from the dependencies, so they answer 404. In read-only mode upstream doesn't mount `/api/reset` or `/api/import`.
- **Read-only.** The graph directory is mounted `:ro` and opened read-only, and nothing in the container is writable: no tmpfs, no `/dev/shm`. So a query can't change a build, write a file anywhere, or install an extension. A 512 MiB buffer pool and a 10 s query timeout make a runaway query fail fast.
- **Who reaches it.** Docker publishes it on `127.0.0.1:18000` only. `tailscale serve` forwards port 18000 on the devbox's tailnet name to it, so the owner's devices reach it at `http://devbox.<tailnet>.ts.net:18000`. Plain HTTP: the tailnet encrypts it already. The VM reaches it at none of the devbox's addresses: the bridge firewall drops its traffic to the devbox, and `tag:pa` is granted nothing on the tailnet.
- **New builds.** The server opens its build once and never looks at `current` again, so the image's entrypoint, `explorer/follow-current`, reads `current` every 2 s and restarts the server on each new build. A rebuild shows up within a few seconds, with no command to run. If the server dies, the container exits and Docker restarts it.

```sh
deploy/home/install-explorer.sh --serve   # build, run, serve on the tailnet (sudo for tailscale serve)
deploy/home/check-explorer.sh             # from the devbox and the VM
docker logs pa-explorer                   # which build it serves: "follow-current: serving builds/<id>"
```

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

## Acceptance run (#46)

1. `install-explorer.sh --serve` runs, and `check-explorer.sh` passes: it serves the current build, read-only, on the pinned LadybugDB; `/api/db` and `/api/gpt` answer 404; `CREATE`, `CHECKPOINT`, `COPY … TO` and `INSTALL` are refused and the graph directory is unchanged byte for byte; it's published on `127.0.0.1` only, nothing answers on the devbox's LAN address, `tailscale serve` forwards to it; and the VM can't reach it via the bridge, the LAN address, or the tailnet address.
2. From the phone with Tailscale on, `http://devbox.<tailnet>.ts.net:18000` opens the explorer. With Tailscale off on the home Wi-Fi, `http://<devbox LAN address>:18000` doesn't load.
3. A rebuild shows up:
   ```sh
   set -a; source ~/.local/share/pa-home-synthetic/owner.env; set +a
   PA_DATA_DIR=~/.local/share/pa-home-synthetic uv run pa librarian rebuild   # cached: model calls: none
   docker logs pa-explorer | tail -n 4   # follow-current: serving builds/<the new id>
   deploy/home/check-explorer.sh
   ```
4. Reboot the devbox: `pa-explorer` is running again and `check-explorer.sh` passes.

## Acceptance run (#45)

1. `mount-store.sh`, then `update.sh <ref>`. `check-store.sh` passes: the graph is readable at `/srv/pa/graph`, nobody in the VM can write it or the current build, root included, and the VM is shared exactly L0, the inbox directory and the graph.
2. As `assistant` in `/home/assistant/agent`, `claude mcp list` shows `pa` connected, and `.mcp.json` names `/srv/pa/graph`.
3. A rebuild is picked up: hold a session open, run `pa librarian rebuild` on the devbox (see #46's step 3), and the session's next graph call answers from the new build.
4. A `claude -p` session in the rendered project answers "What's happened with the kitchen remodel so far?" with `mcp__pa__timeline` among its tool calls and `[ep:…]` citations.
5. From the phone, a chat about the kitchen remodel uses the graph's tools (the app shows the `pa` tool calls).
