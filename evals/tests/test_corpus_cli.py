import hashlib
from pathlib import Path

import pytest

from pa_core.model_client import FakeModelBackend, ModelRequest
from pa_evals import corpus_cli
from pa_evals.cli import main

SAMPLE_SPEC = Path(__file__).parent / "fixtures" / "sample_spec.yaml"


def prose(request: ModelRequest) -> dict[str, str]:
    return {"text": f"Prose {hashlib.sha256(request.prompt.encode()).hexdigest()[:8]}."}


@pytest.fixture
def claude(monkeypatch: pytest.MonkeyPatch) -> list[str | None]:
    """Stands in for the claude -p backend; records the model each one was made for."""
    models: list[str | None] = []

    def fake_claude(model: str | None = None) -> FakeModelBackend:
        models.append(model)
        return FakeModelBackend.replying(prose)

    monkeypatch.setattr(corpus_cli, "ClaudeCliBackend", fake_claude)
    return models


def test_corpus_generate_writes_a_payload_per_spec_item(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], claude: list[str | None]
) -> None:
    out = tmp_path / "corpus"

    status = main(["corpus", "generate", str(SAMPLE_SPEC), str(out), "--model", "sonnet"])

    assert status == 0
    payloads = sorted(path.relative_to(out).parent.name for path in out.rglob("*.*"))
    assert payloads == ["gmail"] * 3 + ["icloud_calendar"] * 4 + ["obsidian"] * 3
    assert f"generated 10 payloads in {out}" in capsys.readouterr().out
    assert claude == ["sonnet"]


def test_corpus_generate_reports_a_bad_spec(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], claude: list[str | None]
) -> None:
    spec = tmp_path / "spec.yaml"
    spec.write_text("owner: argus\n")

    status = main(["corpus", "generate", str(spec), str(tmp_path / "corpus")])

    assert status == 1
    assert "pa-eval: storyline spec" in capsys.readouterr().err
    assert not (tmp_path / "corpus").exists()
