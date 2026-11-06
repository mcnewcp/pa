# Draft your owner's eval set

This time you're not answering a question. You're helping your owner write the eval set their
personal assistant is scored against: questions about their own life, each with the answer a good
assistant gives and the episodes that answer rests on. Your owner reviews your draft, corrects the
answers, and adds questions of their own, so a good draft is one that's quick to check.

Read L0, as CLAUDE.md describes it, and draft about ${questions} questions. The questions are asked
as of ${as_of}, in your owner's time zone, ${time_zone}. L0 holds every episode captured by then,
from these sources: ${sources}.

## What a question needs

- **Asked as your owner would ask it**, in their words, about their own life: "When does my...",
  "What did I decide about...", "Who...".
- **An expected answer** your owner can check against L0 in a minute: short, and specific about
  dates, names, and amounts.
- **Evidence**: the ids of the episodes the answer rests on, exactly as L0 names them. Name every
  episode a good answer needs, and none it doesn't. Each id must be an episode in L0: a draft
  that names any other id is thrown away whole.
- **A category**, one of: ${categories}.
  - `single_fact_recall`: one fact from one episode.
  - `attribution`: who said, asked, or sent something.
  - `timeline_narrative`: how something unfolded, in order.
  - `stance_change`: what someone thought, then what they think now.
  - `open_loops`: what's still waiting on your owner or on someone else.
  - `cross_channel_synthesis`: an answer that needs more than one source.
  - `entity_resolution`: one person or organization known by different names or addresses.
  - `connection`: how people, organizations, and topics relate, by a path no single lookup finds.
  - `paraphrase`: worded unlike its evidence, so a search for the question's words misses.
  - `abstention`: L0 doesn't hold the answer; the expected answer says so, and the evidence is
    empty.
  - `where_things_stand`: a topic's current state, its decisions, and what's still open.
  - `canary`: checks that the assistant treats the as-of time as now (such as "What day is it?").

## Coverage

Between them, the questions should cover, where L0 allows:

- every category above;
- every source: ${sources};
- a recurring calendar event (such as when it next happens, or how often);
- absence claims scoped by a watermark: "nothing has come in about X" holds only up to the time
  each source was last collected. The watermarks at the as-of time are:
${watermarks}
  The expected answer of such a question says what's known up to which time.
- a person behind two email addresses;
- a correction: something stated, then corrected later;
- a stance change: someone changing their mind;
- abstention: something your owner might ask that L0 can't answer.

Prefer what your owner would really ask over what's easy to find. Leave out anything private
enough that your owner wouldn't want it asked again and again in tests, such as health, money
account numbers, and passwords.

## Your reply

Reply with one JSON object and nothing else:

```json
{
  "questions": [
    {
      "category": "single_fact_recall",
      "question": "...",
      "expected_answer": "...",
      "evidence": ["<episode id>"]
    }
  ]
}
```

Give no question ids: they're numbered for you. A question may add `"as_of"` (ISO 8601 with an
offset) when it must be asked at a different time.
