"""Shared cancellation signal; no process outside this run is controlled."""
class Cancelled(RuntimeError):
    pass


def checkpoint(cancel):
    if cancel is not None and cancel.is_set():
        raise Cancelled('AutoLoop cancelled; original weights are preserved.')
