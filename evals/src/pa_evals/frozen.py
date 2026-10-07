"""Where the frozen Argus corpus and its eval set live in the workspace (`evals/corpus/`).

They are the defaults for `pa-eval run` and `pa-eval eval-set build`.
"""

from __future__ import annotations

from pathlib import Path

CORPUS_ROOT = Path(__file__).resolve().parents[2] / "corpus"
SPEC = CORPUS_ROOT / "argus.yaml"
"""The storyline spec, hand-written."""
PAYLOADS = CORPUS_ROOT / "payloads"
"""The frozen corpus: the raw payloads generated from the spec once, then committed."""
QUESTIONS = CORPUS_ROOT / "questions.yaml"
"""The eval set's questions, hand-written, with evidence named by spec event id."""
EVAL_SET = CORPUS_ROOT / "eval_set.json"
"""The eval set built from the questions (`pa-eval eval-set build`); never edited by hand."""
