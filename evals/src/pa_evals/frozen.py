"""Where the frozen Argus corpus and its eval set live in the workspace (`evals/corpus/`), and
the frozen scale corpus beside them: its storyline extension, payloads, and eval set.

The Argus ones are the defaults for `pa-eval run` and `pa-eval eval-set build`.
"""

from __future__ import annotations

from dataclasses import dataclass
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

SCALE_EXTENSION = tuple(sorted(CORPUS_ROOT.glob("scale-*.yaml")))
"""The scale corpus's storyline extension: the files that add to the spec, hand-written."""
SCALE_QUESTIONS = CORPUS_ROOT / "questions-scale.yaml"
"""The scale corpus's new questions, hand-written, with evidence named by spec event id."""
SCALE_PAYLOADS = CORPUS_ROOT / "payloads-scale"
"""The extension's frozen payloads, generated from the spec and its extension once. The scale
corpus is these and the Argus payloads together."""
SCALE_EVAL_SET = CORPUS_ROOT / "eval_set-scale.json"
"""The scale corpus's eval set: the Argus questions and the new ones, built over the scale
corpus; never edited by hand. From `evals/corpus/`:

    pa-eval eval-set build --questions questions.yaml questions-scale.yaml \\
      --spec argus.yaml scale-*.yaml --corpus payloads payloads-scale --out eval_set-scale.json
"""


@dataclass(frozen=True)
class Corpus:
    """A frozen corpus `pa-eval run` can be given by name: its payloads and its eval set."""

    payloads: tuple[Path, ...]
    """The directories of raw payloads; the corpus is their union."""
    eval_set: Path


CORPORA: dict[str, Corpus] = {
    "argus": Corpus((PAYLOADS,), EVAL_SET),
    "scale": Corpus((PAYLOADS, SCALE_PAYLOADS), SCALE_EVAL_SET),
}
"""The frozen corpora by name."""
DEFAULT_CORPUS = "argus"
