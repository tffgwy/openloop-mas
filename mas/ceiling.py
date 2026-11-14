"""The per-call wall-clock ceiling of the run in flight.

The loop has to be able to stop a call that outlives the run's
wall-clock budget. The ceiling travels through the thread context
instead of through the provider signature on purpose: a new keyword
argument on ``complete`` would break every provider written before it
existed - including the custom ones a user writes against this
package - and the failure would show up as a ``TypeError`` in the
middle of a run.

The state is thread-local because the loop runs its parallel workers
and its reviewer panel in separate threads. Every one of them has to
respect the same deadline, so the pool carries the ceiling into its
worker threads explicitly.
"""

from __future__ import annotations

import threading
from contextlib import contextmanager
from typing import Iterator, Optional

_CALL_CEILING = threading.local()


def current_call_ceiling() -> Optional[float]:
    """The wall-clock ceiling of the call in flight, if there is one."""
    return getattr(_CALL_CEILING, "seconds", None)


@contextmanager
def call_ceiling(seconds: Optional[float]) -> Iterator[None]:
    """Run the enclosed block with a per-call wall-clock ceiling."""
    previous = getattr(_CALL_CEILING, "seconds", None)
    _CALL_CEILING.seconds = seconds
    try:
        yield
    finally:
        _CALL_CEILING.seconds = previous
