"""Run a coroutine on the AsyncRunner and get the result back on the Qt UI thread."""

from __future__ import annotations

from typing import Any, Callable, Coroutine

from PySide6.QtCore import QObject, Signal

from .aio import AsyncRunner

_pending: set["_Call"] = set()


class _Call(QObject):
    done = Signal(object, object)  # result, exception


def run_async(runner: AsyncRunner, coro: Coroutine[Any, Any, Any],
              callback: Callable[[Any, BaseException | None], None]):
    """callback(result, error) runs on the UI thread (queued signal)."""
    call = _Call()
    _pending.add(call)

    def _finish(result, error) -> None:
        _pending.discard(call)
        callback(result, error)

    call.done.connect(_finish)

    def _on_done(fut) -> None:  # loop thread
        if fut.cancelled():
            call.done.emit(None, None)
            return
        err = fut.exception()
        call.done.emit(None if err else fut.result(), err)

    future = runner.submit(coro)
    future.add_done_callback(_on_done)
    return future
