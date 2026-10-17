import hashlib
from pathlib import Path
from typing import Any

import pytest

from pa_core.model_client import FakeModelBackend, ModelRequest
from pa_evals import corpus_cli
from pa_evals.cli import main

FIXTURES = Path(__file__).parent / "fixtures"
SAMPLE_SPEC = FIXTURES / "sample_spec.yaml"
EXTENSION = [FIXTURES / "sample_extension" / name for name in ("garden.yaml", "nursery.yaml")]


def prose(request: ModelRequest) -> dict[str, Any]:
    digest = hashlib.sha256(request.prompt.encode()).hexdigest()[:8]
    if "turns" in request.schema["properties"]:
        return {"turns": [{"role": "owner", "text": digest}, {"role": "assistant", "text": digest}]}
    return {"text": f"Prose {digest}."}


@pytest.fixture(autouse=True)
def cache_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Where the prose cache goes by default, kept out of the real home directory."""
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache-home"))
    return tmp_path / "cache-home"


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


def test_corpus_generate_writes_an_extension_from_its_files(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], claude: list[str | None]
) -> None:
    out, cache = tmp_path / "extension", tmp_path / "prose-cache"

    specs = [str(SAMPLE_SPEC), *map(str, EXTENSION)]
    options = ["--cache-dir", str(cache), "--workers", "2"]

    status = main(["corpus", "generate", *specs, str(out), *options])

    assert status == 0
    payloads = sorted(path.relative_to(out).parent.name for path in out.rglob("*.*"))
    assert (
        payloads == ["assistant_chat"] * 3 + ["gmail"] * 4 + ["icloud_calendar"] + ["obsidian"] * 2
    )
    assert f"generated 10 payloads in {out}" in capsys.readouterr().out
    assert len(list(cache.rglob("*.json"))) == 4 + 2 + 3  # emails, note sections, chats


def test_corpus_generate_keeps_its_prose_in_the_users_cache_by_default(
    tmp_path: Path, cache_home: Path, claude: list[str | None]
) -> None:
    status = main(
        ["corpus", "generate", str(SAMPLE_SPEC), str(tmp_path / "corpus"), "--model", "opus"]
    )

    assert status == 0
    # One file per model reply, kept apart for each model.
    assert len(list((cache_home / "pa" / "corpus-prose" / "opus").glob("*.json"))) == 7
