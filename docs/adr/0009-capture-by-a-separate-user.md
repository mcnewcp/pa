# Exchanges are captured by a separate user, through a root-owned helper, from hook events

Corrections carry authority over the wiki (ADR-0007), so a forged `assistant_chat` payload is a forged correction, and with real mail in the store, anything the assistant's VM runs could be steered by an injected email. So in the home VM, the chat inbox belongs to a `capture` user, and nothing the assistant runs can write it. The only way in is the capture helper, root's, which the harness's own capture hooks run as `capture` through one narrow sudo rule. The helper builds each exchange from the hook events alone (the owner's prompt from `UserPromptSubmit`, each question and answer from `PostToolUse` on `AskUserQuestion`, and the reply from `Stop`), never from the session's transcript, which the assistant's own process writes. The exchange's message id is the turn's `prompt_id`, so the payload schema and the episode-id rule are unchanged. #81 showed it working from the phone and the Mac; #94 built it.

What holds it:

- The hooks are managed hooks, and only managed hooks run. They act only where the Remote Control unit has turned capture on.
- The assistant's tools can't write the inbox: the managed settings deny it to Write and Edit, and the sandboxed shell writes only to scratch. Ownership stops them too, since the inbox isn't the assistant's.
- The sandboxed shell can't use the sudo rule, because nothing in the sandbox gains privileges. Outside it, the rule allows the helper's three verbs and nothing else.
- `capture` has the devbox user's uid and the assistant has another. The share keeps owners unmapped, so the helper's payloads are the devbox user's, which ingest reads and removes, and the assistant can't write the inbox through the share either.
- The helper writes only a whole turn. It holds the prompt as pending until the turn's `Stop`, so an interrupted or failed turn, which fires no `Stop`, is never written, and a pending prompt expires. It holds the exchange to the `assistant_chat` schema before writing, and ingest still validates strictly.
- `UserPromptSubmit` also fires for prompts nobody typed: a background task's report, a scheduled prompt, another session's message. Its input doesn't say where a prompt came from, so the paths that make them are denied before a session starts. The helper refuses the two it can recognize, task notifications and cross-session messages, as a second line.

## Considered Options

- **Keep the `Stop` hook reading the transcript, as the assistant.** Rejected. Anything the assistant runs could write the inbox directly, or edit the transcript before the hook reads it.
- **Filter prompts in the hook instead of denying the tools that make them.** Rejected. A scheduled prompt looks just like a typed one.
- **A shared group on the inbox (mode 2770) instead of the uid swap.** Rejected. The devbox user would have to join a VM's group, and the swap needs nothing on the devbox.
- **Turn chat capture off for real data.** Rejected by the owner. Corrections from the phone are how the owner keeps the wiki honest.

## Consequences

- This amends [ADR-0004](0004-inbox-directory-sets-source.md)'s consequences. The inbox directory still sets the source, but in the home VM `inbox/assistant_chat/` has one writer, the capture helper running as `capture`, rather than "the transcript hook". So the forged-exchange path that ADR-0007 accepted only while the store was synthetic is closed.
- The assistant moves off uid 1000. A VM rebuilt from the profile creates `capture` first. An existing VM moves the assistant in place, with what it owns on the VM's disk.
- The isolation check now has to show three things on every run: the file tools and Bash can't write the inbox, the sandboxed shell can't use the rule, and the rule works outside the sandbox.
- Capture depends on Claude Code's hook inputs: `prompt_id` in both events, `last_assistant_message` at `Stop`, and `answers` in `AskUserQuestion`'s result. A release that changes them breaks capture, not the fences. Bumping the pin calls for a phone exchange as well as the isolation check.
- The work instance needs its own way to the same guarantee, since its sessions run as the owner's workspace identity.
