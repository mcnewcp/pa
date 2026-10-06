"""Errors the PA reports to the owner instead of crashing with a traceback."""


class PaError(Exception):
    """Base class for expected failures with a message meant for the owner."""


class MalformedPayloadError(PaError):
    """A raw payload that cannot be normalized into an envelope."""


class EpisodeConflictError(PaError):
    """An episode id that already exists in L0 with different content."""
