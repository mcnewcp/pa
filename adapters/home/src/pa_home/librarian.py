"""The librarian on the home instance: L0 on disk, its cache under the data root, LadybugDB."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from pa_core.librarian.ports import BackendFor, LibrarianModels
from pa_core.librarian.rebuild import DEFAULT_WORKERS, rebuild
from pa_core.librarian.report import BuildReport
from pa_core.owner import Owner
from pa_home.config import DataRoot
from pa_home.filesystem_l0 import FilesystemL0
from pa_home.graph import extensions
from pa_home.graph.layout import GraphDirectory
from pa_home.graph.store import LadybugBuilder


class FileCache:
    """The librarian's cache as one file per result: `<root>/<stage>/<key[:2]>/<key>.json`."""

    def __init__(self, root: Path) -> None:
        self._root = root

    def get(self, stage: str, key: str) -> str | None:
        path = self._path(stage, key)
        return path.read_text(encoding="utf-8") if path.is_file() else None

    def put(self, stage: str, key: str, value: str) -> None:
        path = self._path(stage, key)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
        temp = Path(temp_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as out:
                out.write(value)
            temp.replace(path)
        finally:
            temp.unlink(missing_ok=True)

    def _path(self, stage: str, key: str) -> Path:
        return self._root / stage / key[:2] / f"{key}.json"


def rebuild_graph(
    data_root: DataRoot,
    owner: Owner,
    *,
    models: LibrarianModels,
    backend_for: BackendFor,
    extensions_root: Path | None = None,
    cache: Path | None = None,
    workers: int = DEFAULT_WORKERS,
) -> BuildReport:
    """Rebuild the claims graph from L0 and publish it; the report is written beside the build.

    The librarian's cache is `cache`, or by default the data root's own.
    """
    fts = extensions.ensure(extensions_root or extensions.extensions_dir(), extensions.FTS)
    graph = GraphDirectory(data_root.graph)
    report = rebuild(
        FilesystemL0(data_root.l0),
        owner,
        models=models,
        backend_for=backend_for,
        cache=FileCache(cache or data_root.librarian_cache),
        builder=LadybugBuilder(graph, fts_extension=fts),
        workers=workers,
    )
    graph.report(report.build_id).write_text(report.model_dump_json(indent=2) + "\n")
    return report
