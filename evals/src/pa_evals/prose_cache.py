"""The prose cache: every reply the corpus generator gets from the model, kept by its prompt.

A generation interrupted partway (a failed call, a killed process) resumes from the cache
without asking the model again for anything it already wrote. Each reply is kept as soon as it
arrives, in a file of its own named by a hash of the prompt and the output schema, and written
whole or not at all.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
from pathlib import Path

from pydantic import BaseModel, ValidationError

from pa_core.model_client import ModelClient


class ProseCache:
    """Model replies under `directory`, one file per prompt and output schema."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory

    def generate[T: BaseModel](self, client: ModelClient, prompt: str, output: type[T]) -> T:
        """The reply to `prompt` from the cache, or else from `client`, then kept."""
        path = self.directory / f"{_key(prompt, output)}.json"
        try:
            return output.model_validate_json(path.read_bytes())
        except (FileNotFoundError, ValidationError):
            pass  # not asked yet, or kept in a shape that no longer fits
        reply = client.generate(prompt, output)
        self._keep(path, reply.model_dump_json().encode())
        return reply

    def _keep(self, path: Path, raw: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        partial = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.partial")
        try:
            partial.write_bytes(raw)
            partial.replace(path)
        finally:
            partial.unlink(missing_ok=True)


def _key(prompt: str, output: type[BaseModel]) -> str:
    request = {"prompt": prompt, "schema": output.model_json_schema()}
    return hashlib.sha256(json.dumps(request, sort_keys=True).encode()).hexdigest()
