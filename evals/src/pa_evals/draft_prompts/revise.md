# Revise your owner's eval set

This time you're not answering a question. You drafted the eval set your owner's personal
assistant is scored against, and your owner has read it. Revise it as their notes say.

The questions are asked as of ${as_of}, in your owner's time zone, ${time_zone}. L0, as CLAUDE.md
describes it, holds every episode captured by then, from these sources: ${sources}. The watermarks
at the as-of time are:
${watermarks}

## The eval set as it stands

Your owner may have corrected answers or added questions in it by hand. Their edits are the
ground truth: keep them unless their notes say otherwise.

```json
${draft}
```

## Your owner's notes

${notes}

## How to revise

- Apply every note. Where a note asks for a new question, or one replaced, read L0 for it.
- Keep every question the notes don't touch exactly as it is, with its `id`.
- Keep a changed question's `id`. Give a new question no `id`: it's numbered for you.
- Evidence names episode ids exactly as L0 names them. Each id must be an episode in L0: a
  revision that names any other id is thrown away whole.
- Categories stay one of: ${categories}.

## Your reply

Reply with one JSON object and nothing else: the whole revised set's questions, in order.

```json
{
  "questions": [
    {
      "id": "r01",
      "category": "single_fact_recall",
      "question": "...",
      "expected_answer": "...",
      "evidence": ["<episode id>"]
    }
  ]
}
```
