"""Create endpoints with primitives from one multiprocessing context."""

import multiprocessing as mp
import sys
from contextlib import ExitStack
from multiprocessing import resource_tracker
from multiprocessing.context import BaseContext

from .receiver import SharedMemoryReceiver
from .sender import SharedMemorySender, validate_capacity

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
    validate_capacity(capacity)
    context = mp.get_context() if ctx is None else ctx
    if _SHARE_RESOURCE_TRACKER:
        resource_tracker.ensure_running()
    # A semaphore rather than a shared Value: a Value's slot in the shared heap is handed to
    # the next pair once the creating process drops it, which merged two pairs' owners.
    owner_lock = context.Lock()
    with ExitStack() as cleanup:
        data_queue = context.Queue()
        cleanup.callback(data_queue.close)
        ack_queue = context.Queue()
        cleanup.callback(ack_queue.close)
        sender = SharedMemorySender(capacity, data_queue, ack_queue, owner_lock=owner_lock)
        receiver = SharedMemoryReceiver(
            data_queue, ack_queue, owner_lock=owner_lock, capacity=capacity
        )
        cleanup.pop_all()  # The endpoints now own both queues.
    return sender, receiver
