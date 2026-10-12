"""The claims graph for an eval run: the librarian over the run's throwaway data root.

The build is what `pa librarian rebuild` does, published into the throwaway root's graph
directory. The librarian's cache lives outside that root, so it outlasts the run: a later run
over the same corpus at the same extractor version extracts nothing again.
"""

from __future__ import annotations

from pathlib import Path

from pa_core.librarian.ports import BackendFor, LibrarianModels
from pa_core.librarian.report import BuildReport
from pa_core.owner import Owner
from pa_home.config import DataRoot
from pa_home.librarian import rebuild_graph


class EvalLibrarian:
    """Builds and publishes the claims graph of a run's data root, caching in `cache`."""

    def __init__(
        self,
        cache: Path,
        *,
        models: LibrarianModels,
        backend_for: BackendFor,
        extensions_root: Path | None = None,
    ) -> None:
        self.cache = cache
        self.models = models
        self._backend_for = backend_for
        self.extensions_root = extensions_root

    def build(self, data_root: DataRoot, owner: Owner) -> BuildReport:
        return rebuild_graph(
            data_root,
            owner,
            models=self.models,
            backend_for=self._backend_for,
            extensions_root=self.extensions_root,
            cache=self.cache,
        )
