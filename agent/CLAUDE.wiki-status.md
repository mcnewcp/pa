
The status's `wiki.current_through` is the time the wiki is current through: episodes captured
after it aren't in any page or day log yet. So for anything newer, search L0 directly, beyond
what the wiki leads you to: look for episodes whose `captured_at` is after that time (the one
use of `captured_at`). `wiki.last_run` says when the librarian last ran, and whether it
succeeded.
