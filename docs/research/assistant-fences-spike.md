# Assistant fences spike (#81)

Run from 2026-10-24 to 2026-10-26 on the devbox, in a throwaway Incus VM, `pa-fence-spike`: Ubuntu 26.04, 2 vCPU, 4 GiB. It was built from a profile derived from the assistant's (`profile.yaml`), with Tailscale left out. It ran Claude Code 2.1.308, the `stable` channel that day, downloaded and verified on the devbox as `install-librarian-claude.sh` does, and pushed to `/usr/local/bin/claude`, owned by root.

Each fence was first shown in headless sessions (`claude -p --output-format stream-json`), on Haiku and on the librarian's setup token, since the fences don't depend on the model or the login. Then the owner logged in with the subscription and drove Remote Control sessions from the phone and from claude.ai/code on the Mac, in manual and auto mode. Those sessions ran on Opus 5.5.

The VM, its profile, its ACL and its inbox were deleted at 15:06 UTC on 2026-10-26, and the owner's login went with the VM.

The VM's layout, as the fenced assistant would have it:

| In the VM | Owner, mode | |
|---|---|---|
| `/srv/pa/agent` | root, 0755 | The rendered agent project, and each session's working directory. Outside the assistant's home, so no ancestor of it is writable |
| `/opt/pa` | root | The code and its venv: the capture helper's interpreter |
| `/srv/pa/scratch` | assistant, 0700 | The only directory the assistant writes. Outside its home |
| `/srv/pa/inbox/assistant_chat` | capture (uid 1000), 0755 | The chat inbox, a writable share |
| `/srv/pa/l0`, `/srv/pa/wiki` | the devbox user | Read-only shares |
| `/usr/local/libexec/pa-capture` | root, 0755 | The capture helper |
| `/var/lib/pa-capture` | capture, 0700 | The helper's pending prompts |
| `/etc/claude-code/managed-settings.json` | root, 0644 | The policy |

`assistant` is uid 1001, and `capture` uid 1000, the devbox user's (see **The share**).

## Results

| # | Fence | Result |
|---|---|---|
| 1 | Remote Control from the phone and the Mac, with egress only to TCP 443 in Anthropic's API range, DNS off, and the hostnames pinned | **Pass.** Two hostnames are needed |
| 2 | The subscription login refreshes its token unattended for a day | **Pass**: three refreshes in 37 hours, through `platform.claude.com` |
| 3 | Bash runs in Claude Code's sandbox, fails closed, has no unsandboxed escape, and writes only to scratch | **Pass**, with an AppArmor profile for bubblewrap |
| 4 | Root-owned managed settings deny the web tools, MCP, Remote Control's own tools, and the tools that schedule prompts or reach out remotely, whatever the flags | **Pass**, for 12 flag sets. Remote Control's own two tools were found from the phone, denied, and gone from a later phone session |
| 5 | Every way a prompt can reach a session other than the owner typing it is denied or never captured | **Pass**, for the 12 found |
| 6 | Neither Bash nor the file tools can write the chat inbox, the agent project, the hook, or the managed settings, also through a symlink in scratch | **Pass** |
| 7 | A real exchange from the phone lands in the inbox, through the capture helper, with the prompt and reply from hook events | **Pass** |
| 8 | The capture user writes the inbox through the share, and the devbox can read and remove what lands | **Pass**: capture is uid 1000 |
| 9 | Nothing an earlier session wrote loads in a later one | **Pass**, given that the home directory is unwritable (6) |

No fence failed, so nothing here stops the milestone.

## The network

The VM sat on the librarian's bridge, `palib0`, whose dnsmasq serves DHCP only, behind an ACL of its own that is a copy of `pa-librarian-egress`: TCP 443 to `160.79.104.0/23`, everything else dropped and logged, both ways. The devbox pinned names into `/etc/hosts` after checking that each resolved into the range.

- **Hostnames to pin: `api.anthropic.com` and `platform.claude.com`.** TLS client hellos, captured in the VM, named only these two:
  - `platform.claude.com` serves the login's OAuth code exchange and every token refresh (below), so it stays pinned.
  - Everything else went to `api.anthropic.com`: sessions, Remote Control's registration and polling, and each session's own connection (`--sdk-url https://api.anthropic.com/v1/code/sessions/…`).
  - The VM never contacted `claude.ai`, even during the login, which the owner's browser does.

  Every connection in the spike went to `160.79.104.10:443`.
- **What else was tried, by name.** Lookups go to the VM's local DNS stub, which a capture on `lo` sees, and fail there:
  - `http-intake.logs.us5.datadoghq.com`: telemetry, 120 lookups, until `DISABLE_TELEMETRY` was set.
  - `github.com`, `raw.githubusercontent.com` and `downloads.claude.ai`: from the interactive login session only. They are the official marketplace's auto-install and the release notes. `CLAUDE_CODE_DISABLE_OFFICIAL_MARKETPLACE_AUTOINSTALL` is now set.

  Nothing else was looked up in two days of sessions. Over the last 37 hours, Remote Control idle and then three phone sessions, the VM made no lookup at all.
- **Environment settings that break Remote Control.**
  - `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC` does: `claude remote-control` exits with `Error: Remote Control requires feature-flag evaluation, which is disabled because CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC is set.`
  - The docs also name `DISABLE_GROWTHBOOK` (not tried).
  - `DISABLE_TELEMETRY=1` doesn't: every phone and Mac session ran with it set. That settles the docs' disagreement for 2.1.308. The Remote Control page's rule ("stays available", from v2.1.283) holds, and the data-usage page's warning that it "can make Remote Control unavailable" doesn't apply.
  - Remote Control also refuses `CLAUDE_CODE_OAUTH_TOKEN`, a setup token, an API key, and `ANTHROPIC_BASE_URL` pointing anywhere but `api.anthropic.com`, so none of those may be set.
- **The ACL's drop log** held only the VM's unicast DHCP renewals to the bridge, and the probes below. The renewals are dropped. The broadcast rebind gets through, so the VM kept its address all day.
- **What's unreachable from the VM.**
  - **TCP probes, all dropped:** SSH on the devbox at its bridge, LAN and tailnet addresses; the current assistant VM; port 80 in the API range; example.com by address.
  - **Ping, unanswered:** the LAN gateway, a tailnet peer, and 1.1.1.1.
  - **Names:** example.com doesn't resolve.
  - **IPv6:** there's no route out.

  TCP 443 elsewhere in the range answers.
- **Remote Control survives a restart of its service and a reboot of the VM** without a new login, as in v0.11.

### Token refresh

The login wrote `~/.claude/.credentials.json` at 00:56 UTC on 2026-10-25, with an access token good for 8 hours and a refresh token. The VM then stayed up with Remote Control running and no session in use until 14:59 UTC on 2026-10-26.
- **Refreshes.** Claude Code contacted `platform.claude.com` three times: at 14:41 on the 25th, at 03:36 on the 26th, and at 14:59 on the 26th, as the owner opened a session. Each time was some hours after the token had expired, so the refresh happens when it's needed, not on a timer. The file was rewritten at the last one, owned by the assistant, mode 0600, with a new 8-hour expiry.
- **Remote Control stayed up throughout.** The service never restarted, its journal shows no error after it started on the 25th, and it polled `api.anthropic.com` 4–12 times an hour.
- **The session after it worked.** The owner opened a new phone session 38 hours after the login, and its three exchanges were captured.

So an unattended day costs nothing, and the egress fence must let the refresh through: losing `platform.claude.com` would end Remote Control once the access token expired.

## The sandbox

- **Packages.** `bubblewrap` and `socat` are installed while the VM is built with its network open.
- **AppArmor.** Ubuntu's `images:` cloud image ships without the `apparmor` package, so `kernel.apparmor_restrict_unprivileged_userns` is 0 and the sandbox just works. Once the package is installed, the setting is 1, and its `bwrap-userns-restrict` profile lets bubblewrap make its namespace. But it moves bubblewrap's children into `unpriv_bwrap`, which denies `CAP_SYS_ADMIN`. Claude Code's seccomp step then can't set up its nested namespace, and every sandboxed command fails with `apply-seccomp: write /proc/self/setgroups (nested userns is capability-restricted; caller must provide CAP_SYS_ADMIN): Permission denied`. That's closed, not open: nothing runs. The fix replaces Ubuntu's profile with one that lets bubblewrap use user namespaces, unconfined:

  ```
  # /etc/apparmor.d/pa-bwrap
  abi <abi/4.0>,
  include <tunables/global>

  profile pa-bwrap /usr/bin/bwrap flags=(unconfined) {
    userns,
    include if exists <local/pa-bwrap>
  }
  ```

  It goes with `ln -s /etc/apparmor.d/bwrap-userns-restrict /etc/apparmor.d/disable/`. Both survive a reboot. The VM should install `apparmor` and this profile, so that the restriction stays on for everything but bubblewrap and the setup doesn't depend on what the image happens to ship.
- **Inside the sandbox**, a command runs with `NoNewPrivs: 1` and seccomp in filter mode. Two things follow:
  - **No sudo.** `sudo` exits with `The "no new privileges" flag is set, which prevents sudo from running as root`, so the sandboxed shell can't use the capture helper's sudo rule.
  - **No Unix sockets.** Creating one fails with `EPERM`. That keeps the sandboxed shell away from the sessions' messaging sockets in `/tmp/cc-socks/` (see **Prompts nobody typed**).
- **Writes.** With `filesystem.allowWrite` set to scratch and the managed `Edit` denies merged in, a sandboxed command could write:
  - scratch;
  - its own `$TMPDIR` (`/tmp/claude-1001`);
  - `/dev/shm`, which is a private tmpfs inside the sandbox that vanishes with the command.

  It couldn't write the home directory, `~/.claude`, `~/.bashrc`, `/tmp`, `/var/tmp`, the agent project, the hook, the managed settings, the helper, or the inbox. A symlink in scratch to `~/.claude/CLAUDE.md` is written through to a read-only file.
  - Denying `Edit(//tmp/**)` also makes the sandbox's own `$TMPDIR` read-only, which breaks Bash: `/tmp/claude-1001/cwd-…: Read-only file system`. So `/tmp` isn't denied.
  - The sandbox leaves empty files in the home directory, owned by the assistant, as mount points for the dotfiles it protects (`.bash_profile`, `.gitconfig`, `.npmrc`, `.zshrc`, …). They're harmless.
- **Reads.** `~/.claude.json` is readable from the sandbox by default. With `filesystem.denyRead` it isn't, and neither is `~/.claude/.credentials.json` or the helper's state.
- **Network.**
  - **In default and auto mode,** the sandbox's proxy refuses `api.anthropic.com` (`CONNECT tunnel failed, response 403`).
  - **In bypass mode,** before bypass was disabled, the proxy let it through.
  - **With `allowManagedDomainsOnly` and no allowed domains,** nothing gets out, and no prompt asks to allow it.
- **No escape.** A Bash call with `dangerouslyDisableSandbox: true` still ran sandboxed (`Read-only file system`), because `allowUnsandboxedCommands` is false.
- **Fails closed.**
  - **bubblewrap or socat missing:** with `failIfUnavailable`, the session refuses to start (`sandbox required but unavailable … refusing to start without a working sandbox`).
  - **Ubuntu's restrictive profile restored:** the session starts, but every command fails before it runs. The isolation check must catch this one by running a command, since the session itself starts.
- **A nested session.** `claude -p` run from the sandboxed shell fails. It can't read the credentials, it can't reach the API, and its hooks couldn't sudo anyway.
- **Under a `--settings` file** that sets `sandbox.enabled: false` and `allowUnsandboxedCommands: true`, in auto mode, the sandbox stayed on with every limit above.

## Managed settings

These are the settings verified above. The file is `/etc/claude-code/managed-settings.json`, owned by root, mode 0644:

```json
{
  "permissions": {
    "deny": [
      "WebFetch", "WebSearch", "mcp__*",
      "RemoteTrigger", "CronCreate", "CronDelete", "CronList", "ScheduleWakeup", "PushNotification",
      "Monitor", "Agent", "Task", "TaskStop", "ListAgents", "SendMessage", "Workflow", "Skill",
      "DesignSync", "Artifact", "SendFeedback", "ReportFindings", "NotebookEdit",
      "EnterWorktree", "ExitWorktree", "LSP", "PowerShell", "FetchInboxMessage", "SendUserFile",
      "Edit(//home/**)", "Edit(//var/tmp/**)", "Edit(//dev/shm/**)", "Edit(//run/**)",
      "Edit(//var/lock/**)", "Edit(//srv/pa/inbox/**)", "Edit(//srv/pa/agent/**)", "Edit(//etc/**)",
      "Edit(//opt/**)", "Edit(//usr/**)", "Edit(//var/lib/pa-capture/**)",
      "Read(//home/assistant/.claude.json)", "Read(//home/assistant/.claude/.credentials.json)",
      "Read(//var/lib/pa-capture/**)"
    ],
    "disableBypassPermissionsMode": "disable",
    "additionalDirectories": ["/srv/pa/l0", "/srv/pa/wiki", "/srv/pa/scratch"]
  },
  "allowManagedPermissionRulesOnly": true,
  "allowManagedHooksOnly": true,
  "hooks": {
    "UserPromptSubmit": [{"hooks": [{"type": "command", "timeout": 30,
      "command": "[ \"$PA_CAPTURE_EXCHANGES\" = 1 ] || exit 0; exec /usr/bin/sudo -n -u capture /usr/local/libexec/pa-capture prompt"}]}],
    "PostToolUse": [{"matcher": "AskUserQuestion", "hooks": [{"type": "command", "timeout": 30,
      "command": "[ \"$PA_CAPTURE_EXCHANGES\" = 1 ] || exit 0; exec /usr/bin/sudo -n -u capture /usr/local/libexec/pa-capture answer"}]}],
    "Stop": [{"hooks": [{"type": "command", "timeout": 30,
      "command": "[ \"$PA_CAPTURE_EXCHANGES\" = 1 ] || exit 0; exec /usr/bin/sudo -n -u capture /usr/local/libexec/pa-capture stop"}]}]
  },
  "sandbox": {
    "enabled": true,
    "failIfUnavailable": true,
    "allowUnsandboxedCommands": false,
    "autoAllowBashIfSandboxed": true,
    "filesystem": {
      "allowWrite": ["/srv/pa/scratch"],
      "denyRead": ["/home/assistant/.claude.json", "/home/assistant/.claude/.credentials.json", "/var/lib/pa-capture"]
    },
    "network": {"allowedDomains": [], "allowManagedDomainsOnly": true}
  },
  "allowedMcpServers": [],
  "allowManagedMcpServersOnly": true,
  "disableClaudeAiConnectors": true,
  "disableSkillShellExecution": true,
  "strictPluginOnlyCustomization": true,
  "autoMemoryEnabled": false,
  "claudeMdExcludes": ["/home/assistant/**"],
  "outputStyle": "default",
  "crossSessionInbound": "refuse",
  "channelsEnabled": false,
  "autoContinueAtUsageLimit": false,
  "env": {
    "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1",
    "CLAUDE_CODE_DISABLE_CRON": "1",
    "CLAUDE_CODE_DISABLE_BACKGROUND_TASKS": "1",
    "CLAUDE_CODE_DISABLE_AGENT_VIEW": "1",
    "CLAUDE_CODE_DISABLE_OFFICIAL_MARKETPLACE_AUTOINSTALL": "1",
    "DISABLE_AUTOUPDATER": "1",
    "DISABLE_UPDATES": "1",
    "DISABLE_TELEMETRY": "1",
    "DISABLE_ERROR_REPORTING": "1",
    "DISABLE_FEEDBACK_COMMAND": "1",
    "ENABLE_CLAUDEAI_MCP_SERVERS": "false"
  }
}
```

- **The tools a session gets.**
  - **Without managed settings,** a headless session in 2.1.308 gets 25 tools, among them `CronCreate`, `ScheduleWakeup`, `RemoteTrigger`, `SendMessage`, `ListAgents`, `Monitor`, `PushNotification`, `Workflow` and `DesignSync`, which is new since #57.
  - **With them,** a headless session gets exactly `Bash, Edit, Read, ToolSearch, Write`.
  - **A Remote Control session also gets** `AskUserQuestion`, `EnterPlanMode`, `ExitPlanMode`, and two tools of Remote Control's own, without an `mcp__` prefix: `SendUserFile` and `FetchInboxMessage`. Both were allowed in the owner's first sessions. Asked to remind the owner in two minutes, the assistant ran `sleep 120` in the foreground and then called `SendUserFile` with `"status": "proactive"`, which failed only because the session wasn't on a project thread. `SendUserFile` is a way out of the VM that nothing needs, and `FetchInboxMessage` reads messages from other sessions, so both are denied now.
  - **Under the final settings,** a phone session listed exactly `AskUserQuestion, Bash, Edit, Read, ToolSearch, Write, EnterPlanMode, ExitPlanMode`.

  A later release can add tools, so the check must fail on any tool outside the expected set rather than trust this list.
- **Flags change nothing.** Twelve flag sets were tried:
  - none;
  - `--dangerously-skip-permissions`;
  - `--permission-mode bypassPermissions`, also with `--allow-dangerously-skip-permissions`;
  - `--permission-mode auto`, and `acceptEdits`;
  - `--tools`, and `--allowedTools`, each naming the denied tools;
  - `--settings` with a file that allows them, sets `defaultMode` to bypass, adds hooks, turns the sandbox off, turns auto memory on, and sets a status line;
  - that file again with `--dangerously-skip-permissions` and an `--mcp-config`;
  - `--mcp-config` alone;
  - `--setting-sources user,project,local`.

  No session had bypass mode (a bypass request starts the session in `default`), a tool outside the five, an MCP server (`MCP server blocked by enterprise policy: stub`), or a hook or status line from the flags.
- **Auto mode and acceptEdits stay available.** The owner switched the phone and the Mac to auto. Nothing in the fences depends on the mode, since each limit is a deny rule, the sandbox, or file ownership, and all three were shown holding in auto. `permissions.disableAutoMode` exists if that ever changes.
- **`CLAUDE_CODE_SUBPROCESS_ENV_SCRUB` forces every session into `default` mode** (`Permission mode forced to default — CLAUDE_CODE_SUBPROCESS_ENV_SCRUB is set`). Under Remote Control the credentials are a file, not in the environment, so it isn't set.
- **Off switches, confirmed.**
  - **Auto memory:** the init message's `memory_paths` is null, and a `MEMORY.md` planted in the auto-memory directory didn't load.
  - **Updates:** `claude update` and `claude install latest` both print `Updates are disabled by your administrator`.
  - **Telemetry and error reporting:** nothing was looked up after they were turned off.
- **Who can start a session with other settings.** A session's settings and environment come from whatever starts it: the Remote Control unit, root-owned, or `incus exec` from the devbox. Claude Code also reads a `CLAUDE_CODE_MANAGED_SETTINGS_PATH` variable. So nothing the assistant can run may start an unsandboxed session. That holds:
  - a sandboxed shell starts only sandboxed processes;
  - its `claude --bg` is refused (`'--bg' is disabled by CLAUDE_CODE_DISABLE_AGENT_VIEW`);
  - it can't reach a messaging socket.

## Prompts nobody typed

`UserPromptSubmit` fires for more than the owner's typing, and its input has no field that says where a prompt came from. In a contrast run with the scheduling tools allowed, it fired for each of these, any of which a hook would have captured as the owner's words:
- a background shell's completion, as `<task-notification>…`;
- a `CronCreate` prompt;
- a `ScheduleWakeup` prompt;
- another local session's `SendMessage`, as `<cross-session-message from="uds:/tmp/cc-socks/….sock" …>`.

So each path is closed before a session starts, not filtered in the hook:

| Path | Closed by | Shown |
|---|---|---|
| Scheduled tasks: `CronCreate`, `CronList`, `CronDelete` | denied; `CLAUDE_CODE_DISABLE_CRON` | Not in the tools. The contrast run fired one |
| `/loop`, typed by the owner, and the `loop` and `schedule` skills | `Skill` denied; `CLAUDE_CODE_DISABLE_CRON` | `/loop 1m say hi`, from the phone and headless: "isn't installed in this session" |
| `ScheduleWakeup` | denied | The contrast run fired one |
| Background shells | `CLAUDE_CODE_DISABLE_BACKGROUND_TASKS` | `run_in_background` is gone, and the shell ran in the foreground. The contrast run fired a `<task-notification>` |
| `Monitor` | denied | Not in the tools. The docs say `CLAUDE_CODE_DISABLE_BACKGROUND_TASKS` doesn't cover it. In 2.1.308 it's also unavailable while `DISABLE_TELEMETRY` is set |
| Background subagents | `Agent` and `Task` denied | Not in the tools. The docs list their reports among `UserPromptSubmit`'s triggers |
| Messages from other local sessions | `SendMessage` and `ListAgents` denied; `crossSessionInbound: "refuse"`; no Unix sockets in the sandbox | With `SendMessage` allowed, a message was delivered but never reached the session's prompt hook under `refuse`, and did reach it without that setting |
| Remote Control's own tools: `FetchInboxMessage` reads those messages, `SendUserFile` reaches out | denied | Found in the owner's tool list from the phone, and gone from it once denied |
| Background sessions and agent view: `claude --bg`, `claude agents` | `CLAUDE_CODE_DISABLE_AGENT_VIEW` | From the sandboxed shell: `'--bg' is disabled` |
| Remote triggers and cloud routines | `RemoteTrigger` denied; the egress fence | Routines run in Anthropic's cloud, not in the VM, and could reach it only as a new session, which the owner starts |
| Channels | `channelsEnabled: false`; MCP denied | Needs `--channels` on the command line, which is the unit's |
| Hooks that continue a turn or wake a session (`decision: block`, `additionalContext`, `asyncRewake`) | `allowManagedHooksOnly` | Hooks planted in user and flag settings never ran |
| Auto-continue after a usage limit | `autoContinueAtUsageLimit: false` | Not shown: it needs a usage limit |
| `PushNotification` | denied | Outbound, not a prompt, but it reaches out |

- **`/goal`** is a session-scoped Stop hook the owner types. It continues the owner's own turn, and `Stop` fires once that turn ends, so the reply it captures is the last one. Not tried.
- **A compaction summary** is a user message too. Nothing suggests `UserPromptSubmit` fires for one. Not shown, since no session in the spike ran long enough to compact.
- **Defense in depth.** The helper could also refuse a prompt that opens with `<task-notification>` or `<cross-session-message`. It can't tell a cron prompt from a typed one, though, so the denials are the fence.

## Capture

- **The user and the helper.** A `capture` user owns the chat inbox inside the VM. The helper, `/usr/local/libexec/pa-capture`, owned by root, runs with `/opt/pa/.venv/bin/python -I`. One sudo rule lets the assistant run it as `capture` with exactly one of three verbs:

  ```
  # /etc/sudoers.d/pa-capture, root, 0440
  assistant ALL=(capture) NOPASSWD: /usr/local/libexec/pa-capture prompt, /usr/local/libexec/pa-capture answer, /usr/local/libexec/pa-capture stop
  ```

  Ubuntu 26.04's `sudo` is sudo-rs (0.2.13). It rejects `Defaults … !requiretty` as an unknown setting, and needs nothing like it.
  - Any other verb, `sudo -u capture /bin/sh`, and `sudo -u root` are all refused.
  - The sandboxed shell can't use the rule at all (no new privileges).
- **The hooks.** They're managed hooks, gated on `PA_CAPTURE_EXCHANGES=1`, which only the Remote Control unit sets. Each Remote Control session is a child process, `claude --print --sdk-url …`, and it inherits the unit's environment along with the managed `env`. The hooks hand the helper their input on stdin:
  - **`UserPromptSubmit`:** the helper holds the prompt as pending for its session.
  - **`PostToolUse` on `AskUserQuestion`:** the helper adds each question asked and the owner's answer. The tool's response has them under `answers`.
  - **`Stop`:** the helper takes the reply from `last_assistant_message`, then writes the whole exchange into the inbox under a hidden name, renamed into place.

  `encode_exchange` holds the payload to the `assistant_chat` schema first. Nothing reads the transcript. Both events carry `session_id` and `prompt_id`, the same in a turn's `UserPromptSubmit` and `Stop`. So `prompt_id` should be the exchange's message id: a recapture then gets the same episode id, as today. The spike's prototype used a random id instead.
- **From the phone and the Mac**, in manual and in auto mode, every finished turn landed as one exchange:
  - 11 exchanges from 12 prompts, the last three after the token refresh;
  - one with the assistant's question and the owner's answer (`Long`) between the prompt and the reply;
  - one sent from the Mac into the session the phone started.
- **An interrupted turn is never written.** `Stop` doesn't fire when the owner presses stop. The prompt stays pending, and the session's next prompt replaces it. Shown from the phone and headless (`SIGINT` mid-turn). `StopFailure`, for API errors, isn't hooked, so a failed turn isn't written either.
- **Pending prompts** from interrupted turns stay in `/var/lib/pa-capture/pending/` until their session's next prompt. The helper should expire them.

## What the assistant can't write

- **The file tools** (Write and Edit, in auto and acceptEdits) were refused with `File is in a directory that is denied by your permission settings` for:
  - the inbox, the agent project's `CLAUDE.md`, the capture hook, the managed settings, and the helper;
  - `~/.claude/CLAUDE.md`, `~/.claude/settings.json` and `~/.bashrc`;
  - `/dev/shm`.

  They wrote scratch and `/tmp/claude-1001`. Through symlinks in scratch (to `~/.claude/CLAUDE.md`, to a file in the inbox, and to `~/.claude/rules/` through a link to `~/.claude`), every write was refused with `Permission to edit … has been denied`. The docs say a deny rule applies when either the requested or the resolved path matches.
- **Ownership is a second fence** for everything but the home directory. The inbox, the project, the hook, its interpreter, the helper and the managed settings belong to root or to `capture`, so writing them fails at the OS even where no deny rule names them. The deny rules are the only fence for the home directory, which the assistant owns and Claude Code writes.
- **From the phone**, in auto mode, a request to write `~/.claude/CLAUDE.md` and `touch ~/x` was declined by the model itself, with no tool call, so it didn't test the fence. Told to make the calls anyway as a test, it refused again, and Anthropic's safeguards flagged the request. So the phone can't drive this test, and the setup shouldn't try. The evidence is the headless runs: a phone session is the same binary under the same managed settings and sandbox, and its tool list matches theirs plus Remote Control's interaction tools.

## The share

`capture` is uid 1000 in the VM, the devbox user's, and `assistant` is uid 1001. An Incus VM's virtiofs share passes owners through unmapped, so:
- a payload the helper writes is the devbox user's on the devbox, mode 0644, in a directory the devbox user owns. `ingest` reads and removes it without any change (shown);
- the assistant, a different uid, can't write the inbox's root (0755), in the VM or through the share;
- the assistant reads L0 and the wiki, which are world-readable (0664 files, 0775 directories).

This swaps today's uids: in `pa-home` the assistant is uid 1000. The rebuilt VM's cloud-init should create `capture` with uid 1000 first. A group-based alternative (a shared gid, mode 2770) would need the devbox user in that group, so it isn't needed.

## No persistence

Canary instructions were planted at every loading location, as in #57:
- `CLAUDE.md`, `CLAUDE.local.md` and `.claude/CLAUDE.md`;
- rules, a skill, an agent, a command, and an output style;
- `.claude/settings.json` and `settings.local.json`, with hooks, permission allows, `env`, an output style and a status line;
- `.mcp.json`;
- `sub/CLAUDE.md` beside a note.

| Planted in | What a later session loaded |
|---|---|
| scratch, which is an additional directory, and `$TMPDIR`, the two places the assistant can write | Nothing, not even `sub/CLAUDE.md` when the note beside it was read. No hook, MCP server or status line ran |
| `~/.claude`, which the assistant can't write (contrast) | Before the last settings: the user `CLAUDE.md`, the user rules, the output style, and the user settings' `env`. A planted `ANTHROPIC_BASE_URL` killed the session (`ECONNREFUSED`). The skill, agent and command never appeared in the init message, and no hook, MCP server or status line ran |
| `~/.claude`, with `claudeMdExcludes: ["/home/assistant/**"]` and a managed `outputStyle` | Only the user settings' `env`. No key turns user settings off, so a root-owned `{}` at `~/.claude/settings.json` stands in front of it. Headless sessions work with it there, and Remote Control starts and connects |

So the home directory is where persistence would live, and the managed `Edit(//home/**)` deny and the sandbox's write limit are what keep it out. Claude Code itself keeps writing the home directory:
- `~/.claude.json`;
- transcripts;
- `~/.claude/bridge-spawn/`, where Remote Control writes each session's `--append-system-prompt-file`.

None of it is writable by the assistant's tools.

## Other findings

- **Remote Control's server mode** runs one child process per session, and a session is created as soon as the server starts (`Capacity: 1/32`). Each child is a headless session on the `--sdk-url` of its session.
- **Transcripts still pile up** in `~/.claude/projects/-srv-pa-agent/` and hold private data. #66 had the same question for the librarian: decide on `cleanupPeriodDays`, and on what VM snapshots keep.
- **The `images:` image has no `apparmor` package**, so its user-namespace restriction is off, unlike stock Ubuntu.
- **An untrusted project's settings are ignored under `-p`**, with a warning, as in #57. The agent project is trusted once at setup, by the owner's interactive login.

## For the fence tickets

1. **Egress.**
   - The assistant gets a bridge of its own like `palib0`: no DNS, no IPv6, NAT.
   - It gets an ACL like `pa-librarian-egress`, and ufw rules allowing DHCP in and only the API range out.
   - Tailscale is left out.
   - The devbox pins `api.anthropic.com` and `platform.claude.com`, range-checked, at each start.
   - The VM is built on an open bridge first (packages, the venv), then moved behind the fence before any share or credential.
2. **Packages and AppArmor.** `bubblewrap`, `socat` and `apparmor` are installed, with the `pa-bwrap` profile and Ubuntu's `bwrap-userns-restrict` disabled. Automatic upgrades and timesyncd are off.
3. **Layout and users.**
   - `capture` is uid 1000 and `assistant` uid 1001.
   - The project at `/srv/pa/agent` and the code at `/opt/pa` belong to root.
   - Scratch is at `/srv/pa/scratch`.
   - The helper, its state directory and the sudo rule are as above.
   - `~/.claude/settings.json` is a root-owned `{}`.
4. **Managed settings as above.** The capture hooks move there from the project's settings. The project's settings deny the web tools too, for the work instance. Paths under `/home/assistant` follow the VM's user.
5. **The helper** is built test-first:
   - `prompt_id` as the message id;
   - pending prompts expired;
   - `<task-notification>` and `<cross-session-message` prompts refused;
   - input size capped;
   - the schema checked by `encode_exchange` before anything is written.
6. **Claude Code** is pushed in from the devbox at a pinned version, as for the librarian.
7. **The isolation check** checks the following:
   - **The network:** the API answers; example.com doesn't, by name or by address; the devbox, the LAN, and tailnet peers are unreachable.
   - **The tools:** a headless session with no flags, and one with `--dangerously-skip-permissions` and an `--mcp-config`, each report exactly `Bash, Edit, Read, ToolSearch, Write`, no MCP server, and not bypass mode.
   - **The sandbox:** a sandboxed command runs and reports `NoNewPrivs: 1`. It can write scratch and can't write the home directory or the inbox, and `sudo` fails in it.
   - **The file tools:** a Write to the inbox and to `~/.claude/CLAUDE.md` is refused.
   - **The files:** the managed settings match the repo's copy, mode 0644, owned by root, and the project, the hook and the helper belong to root.

   A Remote Control session's tool list can't be read headlessly, so the owner's setup run asks for it once from the phone. Writes are tested headlessly only: a phone session refuses to attempt them.

Nothing here changes ADR-0007. ADR-0004's consequences change once the capture ticket lands: only the helper, as the capture user, writes the inbox. An ADR should record capture by a separate user through a root-owned helper and hook events, as #80 planned.
