from __future__ import annotations

from functools import wraps
from typing import Any, Callable, TypeVar, cast

from app.kpnet import workflow

_ResultT = TypeVar("_ResultT")


class KpNetUnknownTaskTerminal(BaseException):
    """Abort the current Cloud Run control task after an ambiguous KP-NET write."""


def guard_unknown_write_terminal(
    original: Callable[..., _ResultT],
) -> Callable[..., _ResultT]:
    """Promote unresolved or post-write failures to the task-terminal boundary."""

    @wraps(original)
    def guarded(*args: Any, **kwargs: Any) -> _ResultT:
        client = kwargs.get("client")
        if client is None or not hasattr(client, "write_setting"):
            try:
                return original(*args, **kwargs)
            except workflow.KpNetUnknownWriteTerminal as exc:
                raise KpNetUnknownTaskTerminal(
                    "KP-NET write remains unknown; abort remaining writes in this task"
                ) from exc

        original_write = client.write_setting
        write_started = False

        @wraps(original_write)
        def tracked_write(*write_args: Any, **write_kwargs: Any) -> Any:
            nonlocal write_started
            write_started = True
            return original_write(*write_args, **write_kwargs)

        client.write_setting = tracked_write
        try:
            return original(*args, **kwargs)
        except workflow.KpNetUnknownWriteTerminal as exc:
            raise KpNetUnknownTaskTerminal(
                "KP-NET write remains unknown; abort remaining writes in this task"
            ) from exc
        except Exception as exc:
            if write_started:
                raise KpNetUnknownTaskTerminal(
                    "KP-NET write started but the final verified outcome is unavailable; "
                    "abort remaining writes in this task"
                ) from exc
            raise
        finally:
            client.write_setting = original_write

    setattr(guarded, "__kpnet_unknown_task_guard__", True)
    return guarded


def install_unknown_write_guard() -> None:
    """Install the Cloud Run-only post-write terminal guard once."""

    current = workflow._apply_settings_profile
    if getattr(current, "__kpnet_unknown_task_guard__", False):
        return
    workflow._apply_settings_profile = cast(
        Callable[..., dict[str, Any]],
        guard_unknown_write_terminal(current),
    )
