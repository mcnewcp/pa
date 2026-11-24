# What you left can't be accepted yet

What you left in your output directory isn't in the form CLAUDE.md asks for, so none of it can
be checked or passed on. This is what is wrong. Each file is named from the directory above
your output directory, so `output/findings.json` is your `findings.json`:

${problems}

Put it right where it is, with the Write tool. Leave exactly the files CLAUDE.md names, each
within its size, and findings that keep to the schema: a field it doesn't name, or text over a
cap, is refused. Change nothing that isn't wrong.

End with one line saying what you put right.
