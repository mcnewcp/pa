"""Claude Code for the VMs: a pinned release, downloaded on the devbox and checked (its manifest's
signature by Anthropic's key, the binary against the manifest's checksum) before it's pushed into
a VM that never reaches the download host. `claude_code_binary` runs here on a stand-in `curl`
serving a fake release and a stand-in `gpg` that vouches for the signature or doesn't."""

from __future__ import annotations

import hashlib
import json
import stat
import subprocess
from pathlib import Path

LIB = Path(__file__).resolve().parents[1] / "lib.sh"
FINGERPRINT = "31DDDE24DDFAB679F42D7BD2BAA929FF1A7ECACE"
VERSION = "2.1.308"

CURL = """#!/bin/bash
# curl -fsSL -o OUT URL: serves the fake release's file for URL.
echo "$*" >>"$CURL_CALLS"
out=$3 url=$4
case $url in
  */keys/claude-code.asc) cp "$RELEASE/claude-code.asc" "$out" ;;
  */claude-code-releases/*) cp "$RELEASE/${url#*/claude-code-releases/}" "$out" ;;
  *) exit 22 ;;
esac
"""

GPG = f"""#!/bin/bash
case "$*" in
  *--fingerprint*) echo "fpr:::::::::{FINGERPRINT}:" ;;
  *--verify*) [[ $SIGNED == 1 ]] && echo "[GNUPG:] VALIDSIG {FINGERPRINT} 2026-10-20" ;;
esac
exit 0
"""


def release(tmp_path: Path, *, binary: bytes = b"claude", checksum_of: bytes | None = None) -> Path:
    root = tmp_path / "release"
    (root / VERSION / "linux-x64").mkdir(parents=True)
    (root / "claude-code.asc").write_text("key")
    (root / VERSION / "linux-x64" / "claude").write_bytes(binary)
    digest = hashlib.sha256(checksum_of if checksum_of is not None else binary).hexdigest()
    manifest = {"version": VERSION, "platforms": {"linux-x64": {"checksum": digest}}}
    (root / VERSION / "manifest.json").write_text(json.dumps(manifest))
    (root / VERSION / "manifest.json.sig").write_text("sig")
    return root


def fetch(tmp_path: Path, root: Path, *, signed: bool = True) -> subprocess.CompletedProcess[str]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    for name, text in (("curl", CURL), ("gpg", GPG)):
        script = bin_dir / name
        script.write_text(text)
        script.chmod(script.stat().st_mode | stat.S_IXUSR)
    return subprocess.run(
        ["bash", "-c", f'set -euo pipefail; source "{LIB}"; claude_code_binary {VERSION}'],
        capture_output=True,
        text=True,
        env={
            "PATH": f"{bin_dir}:/usr/bin:/bin",
            "HOME": str(tmp_path / "home"),
            "RELEASE": str(root),
            "SIGNED": "1" if signed else "0",
            "CURL_CALLS": str(tmp_path / "curl-calls"),
        },
        check=False,
    )


def test_the_pinned_release_is_downloaded_checked_and_its_binary_named(tmp_path: Path):
    result = fetch(tmp_path, release(tmp_path, binary=b"the release"))

    assert result.returncode == 0, result.stderr
    binary = Path(result.stdout.strip())
    assert binary.read_bytes() == b"the release"
    assert binary.is_relative_to(tmp_path / "home" / ".cache" / "pa" / "claude-code" / VERSION)


def test_a_binary_that_isnt_the_one_the_signed_manifest_names_is_refused(tmp_path: Path):
    result = fetch(tmp_path, release(tmp_path, binary=b"tampered", checksum_of=b"the release"))

    assert result.returncode != 0
    assert "doesn't match the signed manifest" in result.stderr
    assert result.stdout == ""


def test_a_manifest_not_signed_by_anthropics_key_is_refused(tmp_path: Path):
    result = fetch(tmp_path, release(tmp_path), signed=False)

    assert result.returncode != 0
    assert f"isn't signed by {FINGERPRINT}" in result.stderr
    assert result.stdout == ""


def test_a_release_already_downloaded_and_checked_isnt_downloaded_again(tmp_path: Path):
    root = release(tmp_path)
    fetch(tmp_path, root)
    (tmp_path / "curl-calls").unlink()

    result = fetch(tmp_path, root)

    assert result.returncode == 0, result.stderr
    assert not (tmp_path / "curl-calls").exists()
