# Real content is diagnosed in the assistant's VM, and only checked findings reach a development session

A development session never reads real content (principle 7): it holds a shell, the host's credentials, web access, and push rights to a public repository, and real mail is text anyone can send. So it sees an eval failure on real data as numbers only, and can't say why an answer failed or what would fix it. v0.14 showed the cost. Its real-data comparison failed, and the reasons sat in private analyses that only the owner could read, or a session the owner started by hand outside the repository.

So real content is diagnosed by a diagnostician: a session in the assistant's VM, given a referral and a case. It runs as eval drafting and eval runs do: as the assistant, behind the same fences, with exchange capture off. A development session starts it with one command and reads only what the devbox releases.

## The diagnosis

- **The referral** is the development session's question. It holds nothing real.
- **The case** is what the session reads, and every part of it is frozen: a cut, an eval set, eval runs over the cut, dreamed copies of the cut's wiki, and earlier private analyses. The live store is never a case; a cut of it is.
- **The session leaves three things:**
  - a private analysis, for the owner, which stays under the real data root;
  - findings: where the fault lies and how it happens, in a fixed schema, naming questions and episodes by their opaque ids;
  - a reproduction: fictional raw payloads, wiki pages, and eval questions that fail the same way.
- **The release check** runs on the devbox, never in the VM, since whatever runs in the VM could be steered by what it read. It releases the findings and the reproduction to a directory a development session can read only when all of these hold:
  - the findings fit the schema, and every path they name is in the repository;
  - neither holds a real identifier: an address, a name, or a slug from the case or the owner's identity;
  - neither repeats a run of words from the case;
  - the reproduction's payloads ingest and its wiki pages lint.

  A refusal says which check failed and how often, never what it found. Release is automatic (the owner's choice), so a diagnosis needs no hand-off.
- **Everything the check lets through is the devbox's to say.** The check lets findings repeat what the referral holds, so it reads the devbox's own copy of the referral, as it reads the devbox's copy of the case. Nothing that comes back from the VM tells the check what to excuse, and a referral that comes back changed fails the diagnosis unchecked. A reproduction's payloads are released byte for byte, so the check reads every decoding of every part and header, and refuses a payload that holds anything it can't decode to text.
- **A refused diagnosis is revised, at most twice.** The check's notes say what it found and where, so they quote real content. They go back into the VM with the refused output, to a new session there, which puts right what they name. That is the one place real content flows back into a session, and it flows to where the content came from. The same check runs on each revision, exactly as strict, and a diagnosis still refused after two ends unreleased, so retries never wear the check down. Every pass and the notes on it are kept with the private analysis.
- **The check is part of the eval harness.** It holds findings to the schema the diagnosis owns and reads eval sets, runs, and a reproduction's questions, which the home adapter can't import. It still runs only on the devbox: the VM's command leaves a diagnosis and never checks one.

The development session then fixes the fault against the reproduction, test-first, and the reproduction stays as a fixture. The real-data eval set stays unread by the session that changes the code, so it stays a fair test.

## Considered Options

- **Let development sessions read the real data root.** Rejected. That session would combine private data, untrusted content, and external communication, which principle 7 forbids for every component, and the rules that fence it are code it can push.
- **A private repository for real-data evals.** Rejected. It moves where files live and leaves the session that reads them as it is.
- **A session the owner starts by hand outside the repository, from a written brief.** This is what v0.14 did. Rejected as the standing way: it needs the owner for every question, the session has a shell and the network, and nothing checks its report.
- **A VM of its own on the librarian's recipe**, with file tools only. Rejected. The assistant's VM already holds cuts and eval sets behind fences the v0.13 spike proved ([findings](../research/assistant-fences-spike.md)), and a diagnosis needs the sandboxed shell to compare two wikis and tally runs.
- **The librarian's VM.** Rejected. The diagnostician reads eval sets and their expected answers, and the librarian writes the memory those sets test.
- **The owner releases each diagnosis.** Rejected by the owner: it puts the hand-off back.
- **The check in the VM.** Rejected for the reason above.

## Consequences

- **A paraphrase can pass the check.** It catches identifiers and quoted text, not meaning, so a reproduction can echo the gist of a private matter, and a development session may commit it. The diagnostician's instructions say to change the subject and keep the shape, and the owner accepted what's left.
- **The findings are untrusted.** They are written by a session that read untrusted content, so an instruction in them is data, and the development session's instructions say so. The schema keeps free text short, and the session acts on the findings only by reading code and running the reproduction.
- **Opaque ids stay out of the repository.** Findings name questions and episodes by id so the development session can inspect an episode's structure. The ids stay out of fixtures, commits, and issues, as the public verdict already keeps them out.
- **The real identifiers are gathered on the devbox** from the case's envelopes, its wiki pages, and the owner's identity. The check prints counts only, as `pa inspect` prints structure only.
- **In a reproduction nothing the repository holds is excused.** In findings, a dotted name the repository or the referral holds is code or a file's name and passes, unless it is written as a link. A reproduction becomes a fixture, so there only the reserved example domains pass, with the names of its own files.
- **The session could change its own copy of the case, and that changes nothing the check reads.** The case is laid read-only (files 0444, directories 0555) in a throwaway directory, but the copy is the session's own user's, and the session's tools are given that directory. The diagnostician's project denies the file tools' writes to it and to everything a diagnosis keeps beside its output, and the sandboxed shell's writes to the case. What remains:
  - The assistant VM's managed settings make themselves the only source of permission rules, so there the project's rules for the file tools are ignored, and the modes are what holds them. The managed settings aren't changed for this: a diagnosis runs behind the assistant's fences as they are (ADR-0007, ADR-0009).
  - Whether the project's sandbox rule holds the shell in the VM is for the first real diagnosis to show. If it doesn't, the shell can change modes on files its own user owns.
  - Either way a session can spoil only the throwaway copy it reads, which is gone when it ends, and what it leaves. The check reads the devbox's copy of the case and of the referral, and holds what the session left to every rule.
- **Diagnosis transcripts stay on the assistant's VM**, as chat, eval, and drafting transcripts do.
- **The assistant's fences now guard this too.** The isolation check has to keep holding for a session that reads eval answers, and a diagnosis fails if the chat inbox changed while it ran, as drafting does.
- **A diagnosis spends the same usage limit** as the assistant and the librarian, so it stays clear of the nightly job.
- **The hand-started session is retired** once the diagnostician has answered a real question. Principle 7's line on development sessions gains the diagnostician as the way real content is read.
