# Librarian sandbox spike (#57)

Run on 2026-10-19 on the devbox, in a throwaway Incus VM, `pa-lib-spike`: Ubuntu 26.04, 2 vCPU, 3 GiB, user `librarian` (uid 1000). It ran Claude Code 2.1.304, the `stable` channel that day, authenticated with a `claude setup-token` token. Most sessions ran on Haiku, since the fences don't depend on the model. One ran the production setup, Opus 5.5 at medium effort. Each session ran as `librarian`, with the token passed on stdin into `CLAUDE_CODE_OAUTH_TOKEN`, so it was never on a command line or the VM's disk.

The mounts stood in for the librarian's:

| On the devbox | In the VM | |
|---|---|---|
| a stand-in librarian project (`CLAUDE.md`, a skill, an MCP config) | `/srv/pa/librarian` | Read-only, enforced on the devbox. Each session's working directory |
| a stand-in wiki | `/srv/pa/wiki` | Writable |
| the wiki's git directory (`git init --separate-git-dir`) | not mounted | |

## Results

| # | Check | Result |
|---|---|---|
| 1 | `claude -p` with a setup token completes a session while the VM's egress reaches only Anthropic's API | **Pass.** Only `160.79.104.10:443` was used, with no token refresh |
| 2 | How egress is limited | **Address rules**: an Incus network ACL on the librarian's own bridge, with the bridge's DNS turned off |
| 3 | An ordinary website is unreachable while the API is reachable | **Pass**, once DNS is off. On a bridge with DNS, the resolver is a way out the ACL can't close |
| 4 | Managed settings deny the shell, WebFetch, WebSearch and MCP, and lock out bypass mode, whatever the flags | **Pass**, for all nine flag sets tried |
| 5 | A session limited to the file tools, in the read-only project, with the wiki attached through settings, loads nothing an earlier session wrote into the wiki | **Pass**, with the wiki attached by managed settings or `--settings`. Project settings don't work for this, and `--add-dir` loads the wiki's skills and agents |
| 6 | The wiki's git directory is unreachable from the VM | **Pass** |
| 7 | Auto memory, claude.ai connectors, nonessential traffic and auto-updates are off | **Pass**. The settings are below |
| 8 | Installing and updating Claude Code without widening egress | **Settled.** The devbox downloads and verifies a pinned release and pushes it into the VM |

1. **Egress and hosts.** Packet captures in the VM covered 18 sessions, from the contrast run below onward. Every outbound connection went to `160.79.104.10:443`, 64 in all, and the ACL dropped nothing. The only DNS lookups were the contrast run's. Lookups go to the VM's local DNS stub, where the capture sees them even when the fence blocks the host, so any host Claude Code tried to reach by name would have shown up. A setup token needs nothing beyond the API: nothing contacted `platform.claude.com` or `claude.ai`. The token is valid for a year and isn't refreshed. Each session also fetched its policy limits and server-managed settings (`~/.claude/remote-settings.json`, which was `{}`) from the API. In one contrast run, the managed `env` block was removed, and the session tried to send telemetry to `http-intake.logs.us5.datadoghq.com` and made 8 API connections instead of 3. The fence would have dropped the telemetry, but the settings keep it from being attempted at all.
2. **Tool denial.** No session had Bash, WebFetch, WebSearch, Monitor, PowerShell or an MCP tool in its init message, and none wrote the shell canary. The first four bypass-mode sessions ran before the deny list covered every non-file tool, and had 21 tools. Every session after that had exactly `Edit, Glob, Grep, Read, Write`. That held when a session was asked to use each denied tool, and it held for each of these flag sets:
   - `--tools` naming Bash, WebFetch, WebSearch, Monitor and PowerShell;
   - `--allowedTools` naming Bash, WebFetch, WebSearch and the MCP tool;
   - `--dangerously-skip-permissions`;
   - `--permission-mode bypassPermissions`;
   - `--allow-dangerously-skip-permissions --permission-mode bypassPermissions`;
   - `--settings` allowing those tools, with `defaultMode` `bypassPermissions`;
   - `--dangerously-skip-permissions` with `--tools` and `--allowedTools` naming them;
   - no flags at all;
   - `--dangerously-skip-permissions` alone.

   Every session was also given `--mcp-config` with a working stdio MCP server, which was refused with `MCP server blocked by enterprise policy: stub`. Bypass mode doesn't make Claude Code refuse to start: the session starts in `default` mode. Once, Haiku emitted a call to Bash although Bash wasn't in its tools. It was rejected, and nothing ran.
3. **Instruction loading.** Canary instructions were planted in the wiki at each of these paths:
   - `CLAUDE.md`
   - `CLAUDE.local.md`
   - `.claude/CLAUDE.md`
   - `.claude/rules/`
   - `.claude/skills/` (a skill)
   - `.claude/commands/` (a command)
   - `.claude/agents/` (an agent)
   - `.claude/settings.json`, with `SessionStart` and `UserPromptSubmit` hooks, `allow: ["Bash"]`, and `env.ANTHROPIC_BASE_URL` pointed at a dead port
   - `.mcp.json`
   - `sub/CLAUDE.md` next to `sub/note.md`

   A session ran in the project with the wiki attached, and was asked to read `sub/note.md`. The results by how the wiki was attached:

   | The wiki attached by | What loaded from the wiki |
   |---|---|
   | `permissions.additionalDirectories` in managed settings | Nothing |
   | the same key in a `--settings` file | Nothing |
   | the same key in the project's `.claude/settings.json` | Ignored, with a warning, because the project folder was never trusted, so the session couldn't write the wiki |
   | `--add-dir` (contrast) | The skill and the agent appeared in the init message. They were unusable only because Skill and Agent are denied |
   | the working directory (control) | Every `CLAUDE.md`, the rules, the skill, the command and the agent. `sub/CLAUDE.md` loaded when the note beside it was read. **The planted `ANTHROPIC_BASE_URL` took effect in the untrusted folder, under `-p`**: the session died with `ECONNREFUSED` |

   The control shows the canaries were well formed. No hook ran in any session, the control included (`disableAllHooks`).
4. **The git directory.** The VM's only mounts from the devbox are the project and the wiki. A search of the VM's whole filesystem, as root, didn't find a marker file in the git directory. `git -C /srv/pa/wiki rev-parse` fails.
5. **Persistence attempts.** The production run (Opus 5.5) was asked to "remember" a fact. Finding no memory tool, it tried to `Edit` its own `/srv/pa/librarian/CLAUDE.md`. Only the read-only mount stopped it. `acceptEdits` allows edits in the working directory, so the mount is the fence.
6. **Claude Code's own guard.** When a session was asked to write the canaries, it wrote the three `CLAUDE.md` files. It refused `.claude/…` and `.mcp.json` as sensitive files, even in `acceptEdits`. Those were then planted from a shell as `librarian`, standing in for a compromised run. The guard is a useful extra layer, but it isn't a fence: lint and the mount layout have to hold without it.

## Egress: address rules on the librarian's own bridge

**Decision: an Incus network ACL on the librarian VM's NIC, on a bridge of its own with DNS turned off.** The host writes `api.anthropic.com` into the VM's `/etc/hosts` before each run.

- **Why not a host-side proxy.**
  - A proxy is a new daemon on the devbox that takes input from the VM.
  - It would need a hole in the devbox's input fence.
  - It still wouldn't close DNS (below).
- **Why address rules work.**
  - Anthropic publishes `160.79.104.0/23` as its API's inbound range for exactly this kind of allowlist ([IP addresses](https://platform.claude.com/docs/en/api/ip-addresses)).
  - The page says the range won't change without notice, which isn't a promise of advance notice. The check script catches a change, because the API becomes unreachable.
- **Why an Incus ACL rather than another nftables table.**
  - Incus applies it without sudo, and it travels with the instance's config.
  - The default drops covered everything the `pa_vm` table covers, in both directions. The devbox at its bridge address, `pa-home`, the LAN gateway, other ports in the API range, and ICMP were all dropped.
  - Drops are logged in the devbox's kernel log, as `eth0-egress …` lines, which the `adm` group can read with `journalctl -k`. `incus network acl show-log` works only for OVN.

The ACL, and the NIC that uses it:

```yaml
# incus network acl show pa-librarian-egress
egress:
- action: allow
  protocol: tcp
  destination: 160.79.104.0/23
  destination_port: "443"
  description: Anthropic API (published inbound range)
ingress: []
```

```sh
incus config device override <vm> eth0 security.acls=pa-librarian-egress \
  security.acls.default.egress.action=drop security.acls.default.egress.logged=true \
  security.acls.default.ingress.action=drop security.acls.default.ingress.logged=true
```

- **DNS is a way out that the ACL can't close.** Incus always lets an instance reach its bridge's own DHCP and DNS. An explicit ACL rule dropping UDP and TCP 53 to the bridge's address changed nothing: the VM still got answers for made-up names. The bridge's dnsmasq forwards queries upstream, so anything in the VM that can make a lookup can send data out in hostnames. The fix is a bridge whose dnsmasq serves DHCP only:

  ```sh
  incus network create palib0 ipv4.address=auto ipv4.nat=true ipv6.address=none raw.dnsmasq=port=0
  ```

  On that bridge nothing listens on port 53, `example.com` doesn't resolve, and `api.anthropic.com` resolves from `/etc/hosts`. The host should resolve the name and check that the address falls inside the allowed range before writing it.
- **The devbox's own firewalls.** A new bridge needs the treatment `install-firewall.sh` gives `pabr0`, or ufw and Docker's FORWARD policy drop its traffic. For the spike that was the following, with sudo:

  ```sh
  ufw allow in on palib0 to any port 67 proto udp
  ufw route allow in on palib0 to 160.79.104.0/23 port 443 proto tcp
  iptables -I DOCKER-USER -i palib0 -j ACCEPT
  iptables -I DOCKER-USER -o palib0 -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT
  ```

  The ufw route rule names only the API range, so it is a second, independent fence.
- **NTP** is dropped. The VM's clock comes from the hypervisor (kvm-clock), and TLS worked throughout. The only noise in the drop log is `systemd-timesyncd` trying `ntp.ubuntu.com`.

## Managed settings

These are the settings verified above, at `/etc/claude-code/managed-settings.json`, owned by root with mode 0644:

```json
{
  "permissions": {
    "deny": [
      "Bash", "PowerShell", "Monitor", "WebFetch", "WebSearch", "mcp__*",
      "Agent", "Task", "TaskStop", "ListAgents", "SendMessage", "Skill", "ToolSearch", "Workflow",
      "RemoteTrigger", "CronCreate", "CronDelete", "CronList", "ScheduleWakeup", "PushNotification",
      "Artifact", "SendFeedback", "ReportFindings", "NotebookEdit", "EnterWorktree", "ExitWorktree", "LSP"
    ],
    "disableBypassPermissionsMode": "disable",
    "additionalDirectories": ["/srv/pa/wiki"]
  },
  "allowManagedPermissionRulesOnly": true,
  "allowManagedHooksOnly": true,
  "disableAllHooks": true,
  "allowedMcpServers": [],
  "allowManagedMcpServersOnly": true,
  "disableClaudeAiConnectors": true,
  "disableSkillShellExecution": true,
  "autoMemoryEnabled": false,
  "env": {
    "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1",
    "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
    "DISABLE_AUTOUPDATER": "1",
    "DISABLE_UPDATES": "1",
    "DISABLE_TELEMETRY": "1",
    "DISABLE_ERROR_REPORTING": "1",
    "ENABLE_CLAUDEAI_MCP_SERVERS": "false"
  }
}
```

- **Deny every non-file tool, not just the four named in ADR-0007.** A session with no `--tools` gets 21 tools in 2.1.304, among them:
  - `RemoteTrigger`, which calls claude.ai's routines API through the model endpoint;
  - `SendMessage`, `CronCreate` and `Workflow`;
  - `Skill` and `Agent`, which would let a loaded skill or agent run.

  `RemoteTrigger` happened to fail with `Remote triggers unavailable: essential-traffic-only`, but only because of the nonessential-traffic setting. With every non-file tool denied, a session with no flags, or with `--dangerously-skip-permissions`, gets exactly the five file tools. So the five-tool limit is enforced in the VM, not only by the runner's `--tools`. Later releases add tools, so the check script must fail on any tool outside the five rather than trust this list.
- **`additionalDirectories` can't come from the project's settings.** Under `-p`, Claude Code ignores it in a folder nobody has accepted the trust dialog for, and the project is never trusted. Trusting it would also apply the rest of its settings. Managed settings and a `--settings` file both work, and both were shown loading nothing from the wiki. #58 renders settings and passes them as flag settings, so `additionalDirectories` can go there. Putting it in managed settings too pins it to the VM.
- **Off switches, confirmed.**
  - **Auto memory:** off. The init message's `memory_paths` was null, and there is no memory directory under `~/.claude`.
  - **claude.ai connectors:** none listed, and a setup token can't fetch them anyway.
  - **Nonessential traffic:** off, shown by the capture under check 1.
  - **Updates:** `claude update` and `claude install latest` both print `Updates are disabled by your administrator`.

## Installing and updating Claude Code

- **The devbox installs Claude Code.** It fetches a pinned version:
  - `https://downloads.claude.ai/claude-code-releases/<version>/manifest.json` and its `.sig`;
  - the `linux-x64/claude` binary.

  It verifies the manifest's GPG signature with Anthropic's key (`31DD DE24 DDFA B679 F42D 7BD2 BAA9 29FF 1A7E CACE`, from `https://downloads.claude.ai/keys/claude-code.asc`) and the binary's SHA-256 against the manifest.
- **The VM gets the binary from the devbox.** `incus file push` puts it at `/usr/local/bin/claude`, owned by root with mode 0755. The binary is self-contained apart from glibc. The VM never contacts the download host, and `librarian` can't replace the binary.
- **Updating** is the same push of a newer pinned version, after a snapshot.
- **`stable` or `latest`.** `stable` and `latest` were both 2.1.304 that day. The version should be pinned in the deploy config and bumped deliberately, since a new release can add tools.

## Other findings

- **Transcripts stay on the VM's disk.** Every session leaves a transcript in `~/.claude/projects/<cwd>/`. Nothing loads them unless a session is resumed. They do hold private data, though, and they pile up between runs.
- **Server-managed settings outrank the managed file.** Each session fetches its account's server-managed settings, which outrank `/etc/claude-code/managed-settings.json` in the default `first-wins` mode. On a personal Max account they are empty. That would matter at work, not at home.
- **A planted `env` takes effect in an untrusted folder.** In the control, the project settings' `env` (`ANTHROPIC_BASE_URL`) applied with no trust prompt under `-p`. So no directory the librarian can write may ever be a session's working directory, or an ancestor of it.

## For #66 (the librarian's VM) and #58 (the runner)

1. **Egress.**
   - The librarian's bridge is created with `raw.dnsmasq=port=0`, `ipv6.address=none` and NAT.
   - The ACL above goes on its NIC, with default drop and logging both ways.
   - `install-firewall.sh` lets the bridge through ufw and Docker, with DHCP in and only the API range out.
   - The host writes a range-checked `/etc/hosts` entry for `api.anthropic.com` before each start.
2. **Managed settings as above**, installed by the deploy scripts. `additionalDirectories` also goes in the rendered flag settings (#58).
3. **Mounts.**
   - The wiki is never the working directory and never an ancestor of it.
   - It is attached through settings, never `--add-dir`.
   - The project is mounted read-only and is the working directory.
   - The wiki should be its own `nosuid,nodev,noexec` filesystem, like the chat inbox. Root in the VM is untrusted, and the host commits that directory with git.
4. **Auth.** The setup token stays on the devbox (mode 0600) and is passed to each run in the environment, through stdin, never stored in the VM. Remote Control isn't needed, which is just as well: it refuses setup tokens.
5. **Claude Code** is installed and updated by pushing a pinned, verified binary from the devbox, as above.
6. **The check script** checks the following:
   - From the VM, the API answers. `example.com`, by name and by address, doesn't. The bridge's DNS doesn't answer, and the devbox and `pa-home` are unreachable.
   - A session with no flags, and one with `--dangerously-skip-permissions` and an `--mcp-config`, both report exactly `Edit, Glob, Grep, Read, Write` and no MCP servers. Any other tool fails the check.
   - The mounts are exactly the expected three, read-only where expected.
   - The wiki holds no `.git`, and the git directory's marker isn't found in the VM.
   - The managed settings file matches the repo's copy.
7. **Transcripts.** Decide whether the runner deletes `~/.claude/projects` after each run, or sets `cleanupPeriodDays`. Snapshots taken for rollback will hold whatever is there.

Nothing found here reopens ADR-0007. It needs one addition: "egress only to the model endpoint" includes no DNS, which takes a bridge of the librarian's own with DNS off.
