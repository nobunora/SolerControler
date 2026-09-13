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
    """Make every exception after a started settings write terminal for this task.

    ``_apply_settings_profile`` has a safe pre-write region (payload/confirm) and
    a mutation region beginning when ``client.write_setting`` is invoked. Once
    that mutation region starts, *any* later exception must not cause another
    automatic SET: the provider may already have accepted the write, a
    completion/detail request may have failed after success, or read-back may be
    stale. The caller therefore loses write ownership for the remainder of the
    Cloud Run task and platform retry is suppressed at the outermost runner.

    Errors before ``write_setting`` is called remain ordinary ``Exception``
    failures and retain the existing retry behavior.
    """

    @wraps(original)
    def guarded(*args: Any, **kwargs: Any) -> _ResultT:
        client = kwargs.get("client")
        if client is None or not hasattr(client, "write_setting"):
            return original(*args, **kwargs)

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
