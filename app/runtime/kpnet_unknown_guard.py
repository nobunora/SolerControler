from __future__ import annotations

from functools import wraps
from typing import Any, Callable, TypeVar, cast

from app.kpnet import workflow

_ResultT = TypeVar("_ResultT")


class KpNetUnknownTaskTerminal(BaseException):
    """Abort the current Cloud Run control task after an ambiguous KP-NET write.

    This intentionally inherits directly from BaseException so the existing
    03:00 controller's broad ``except Exception`` fail-safe does not attempt a
    standby SET after a write whose provider/device outcome is still unknown.
    The Cloud Run entrypoint catches this sentinel at the outermost boundary and
    exits successfully to suppress a platform retry of the ambiguous mutation.
    """


def guard_unknown_write_terminal(
    original: Callable[..., _ResultT],
) -> Callable[..., _ResultT]:
    """Promote workflow UNKNOWN into a task-terminal sentinel.

    The workflow already performs the only allowed read-only reconciliation.
    If it still reports ``KpNetUnknownWriteTerminal``, no caller inside the
    control sequence may convert that state into a later device SET.
    """

    @wraps(original)
    def guarded(*args: Any, **kwargs: Any) -> _ResultT:
        try:
            return original(*args, **kwargs)
        except workflow.KpNetUnknownWriteTerminal as exc:
            raise KpNetUnknownTaskTerminal(
                "KP-NET write remains unknown; abort remaining writes in this task"
            ) from exc

    setattr(guarded, "__kpnet_unknown_task_guard__", True)
    return guarded


def install_unknown_write_guard() -> None:
    """Install the Cloud Run-only UNKNOWN-write terminal guard once."""

    current = workflow._apply_settings_profile
    if getattr(current, "__kpnet_unknown_task_guard__", False):
        return
    workflow._apply_settings_profile = cast(
        Callable[..., dict[str, Any]],
        guard_unknown_write_terminal(current),
    )
