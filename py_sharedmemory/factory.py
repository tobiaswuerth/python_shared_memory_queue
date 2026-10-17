"""Create endpoints with primitives from one multiprocessing context."""

import multiprocessing as mp
import sys
from contextlib import ExitStack
from multiprocessing import resource_tracker
from multiprocessing.context import BaseContext

from .receiver import SharedMemoryReceiver
from .sender import SharedMemorySender, _validate_capacity

# Before Python 3.13, attaching registers the block with the receiver's tracker.
# Children forked before any tracker starts would otherwise each run their own.
_SHARE_RESOURCE_TRACKER = sys.platform != "win32" and sys.version_info < (3, 13)


def create_shared_memory_pair(
    capacity: int | None = 0, *, ctx: BaseContext | None = None
) -> tuple[SharedMemorySender, SharedMemoryReceiver]:
    """Create a connected sender and receiver.

    Args:
        capacity: Maximum messages sent but not yet received and freed; `put` blocks
            when it is reached. `0` or `None` means unbounded.
        ctx: Multiprocessing context for the underlying queues, such as
            `multiprocessing.get_context("spawn")`. Defaults to the default context;
            use the same context to start the worker processes.

    Returns:
        `(sender, receiver)`. Pass them to worker processes as `Process` arguments
        before the first `put`. One process may produce; many may consume.
    """
    _validate_capacity(capacity)
    context = mp.get_context() if ctx is None else ctx
    if _SHARE_RESOURCE_TRACKER:
        resource_tracker.ensure_running()
    producer_owner = context.Value("q", 0)
    with ExitStack() as cleanup:
        data_queue = context.Queue()
        cleanup.callback(data_queue.close)
        ack_queue = context.Queue()
        cleanup.callback(ack_queue.close)
        sender = SharedMemorySender(capacity, data_queue, ack_queue, producer_owner=producer_owner)
        receiver = SharedMemoryReceiver(data_queue, ack_queue)
        cleanup.pop_all()  # The endpoints now own both queues.
    return sender, receiver
