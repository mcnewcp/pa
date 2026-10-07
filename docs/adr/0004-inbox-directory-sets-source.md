# Ingest takes an episode's source from its inbox directory

The inbox has one directory per source (`inbox/gmail/`, `inbox/icloud_calendar/`, `inbox/obsidian/`, `inbox/assistant_chat/`), and ingest takes each payload's source from the directory it sits in, never from the payload. Each source has one expected payload format, so a payload in the wrong directory, or at the inbox root, is quarantined. We did this to confine the assistant's VM: it reads untrusted email with tools, and its only write path back into L0 is the inbox. Only `inbox/assistant_chat/` is mounted into the VM, so nothing the VM writes can ever become an email, a calendar event, or a daily note, however the payload presents itself.

## Considered Options

- **Keep the flat inbox and pick the normalizer by file extension.** Rejected: the VM could then write a `.eml` that lands as an email from anyone.
- **Give only `assistant_chat` its own directory and keep extension dispatch at the root.** Rejected: two ingest paths for one rule, and each collector added in v0.13 gets the same confinement for free under one rule.

## Consequences

- Every writer of the inbox (the corpus generator, the eval harness, the transcript hook, and the future collectors) writes into its source's directory. The frozen corpus is laid out the same way.
- Episode ids are unchanged: they still come from the raw payload alone (ADR-0001). The directory chooses the normalizer; it doesn't feed the id.
- Ingest validates `assistant_chat` payloads strictly against their schema, since they come from the least trusted writer.
