"""Create endpoints with primitives from one multiprocessing context."""

import multiprocessing as mp
from contextlib import ExitStack
from multiprocessing.context import BaseContext

from .receiver import SharedMemoryReceiver
from .sender import SharedMemorySender, _validate_capacity


def create_shared_memory_pair(
    capacity: int | None = 0, *, ctx: BaseContext | None = None
) -> tuple[SharedMemorySender, SharedMemoryReceiver]:
    """One producer process, multiple consumers. Zero/None capacity is unbounded."""
    _validate_capacity(capacity)
    context = mp.get_context() if ctx is None else ctx
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
