
The status's `wiki.current_through` is the time the wiki is current through: episodes captured
after it aren't in any page or day log yet. So for anything newer, search L0 directly, beyond
what the wiki leads you to: look for episodes whose `captured_at` is after that time (the one
use of `captured_at`). `wiki.last_run` says when the librarian last ran, and whether it
succeeded.

`wiki.last_dreaming` is the librarian's last dreaming run: its review of the whole wiki, about
once a week, which fixes what L0 settles and asks the owner the rest as proposals. It gives
when it ran (`at`), whether it succeeded (`succeeded`) and, if not, why (its `reason`), what it
cost (`cost_usd`, in US dollars), how many candidate problems it judged (`candidates`), how
many of those it `applied`, `proposed` and `dismissed` (`outcomes`), how many more it left
alone because an earlier run had dismissed them or already asked the owner (`held_back`), and
how many it left for the next run because a run judges only so many (`left_over`).
`wiki.open_proposals` is how many proposals are waiting for the owner's answer.

When the owner asks whether the wiki is being reviewed, how the last review went, or how much
is waiting on them, report it from these: when it ran, in the owner's local time, whether it
succeeded, the counts, what it cost, and how many proposals are open.

- If `wiki.last_dreaming` is null, say that no dreaming run has been recorded yet.
- If it failed, say so and give its `reason`. A failed run is tried again the next night.
- If it judged 0 candidates, it found nothing to judge that week: say so, and how many were
  held back.
- These are counts only. What each open proposal asks is in `proposals.md`, at the wiki's
  root.
