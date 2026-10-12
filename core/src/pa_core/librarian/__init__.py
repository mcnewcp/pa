"""The librarian: the batch process that derives the claims graph from the episodes in L0.

`rebuild` runs its stages and hands the records to a `GraphBuilder`. Each stage that calls a
model caches its results in a `LibrarianCache`, keyed by everything its output depends on.
"""
