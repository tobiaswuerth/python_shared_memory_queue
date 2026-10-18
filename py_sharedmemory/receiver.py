"""The receiving endpoint returns independent local data."""

from __future__ import annotations

import time
from multiprocessing.queues import Queue
from multiprocessing.reduction import ForkingPickler
from queue import Empty
from types import TracebackType
from typing import TYPE_CHECKING, Any, Self

from .convert import SharedMemoryMessage, attach, load

if TYPE_CHECKING:
    from multiprocessing.synchronize import Lock


def _remaining(block: bool, deadline: float | None) -> float | None:
    if not block:
        return 0
    return None if deadline is None else max(0.0, deadline - time.monotonic())


class SharedMemoryReceiver:
    """Consuming endpoint of a pair; create it with `create_shared_memory_pair`.

    Several threads and processes may share one receiver; each message is delivered to
    exactly one of them. Every received block is acknowledged, even when decoding fails,
    so the sender can free its memory.
    """

    def __init__(
        self,
        data_queue: Queue[SharedMemoryMessage],
        ack_queue: Queue[int],
        *,
        owner_lock: Lock | None = None,
    ) -> None:
        self._data_queue = data_queue
        self._ack_queue = ack_queue
        # Unused here, but holding it lets the creating process drop its sender: with spawn
        # and forkserver on Linux and macOS, releasing a lock's last reference deletes its
        # name, and a child that has not started yet could no longer open it.
        self._owner_lock = owner_lock
        self._closed = False

    @property
    def closed(self) -> bool:
        """Whether `close` was called."""
        return self._closed

    def get(self, block: bool = True, timeout: float | None = None) -> Any:
        """Receive the next object as an independent local copy.

        Args:
            block: Wait for a message when none is available.
            timeout: Maximum seconds to wait; `None` waits forever.

        Returns:
            The object, which stays valid after the block is reused or freed. Arrays keep
            their original writability.

        Raises:
            queue.Empty: No message arrived in time.
            BrokenPipeError: The receiver is closed.
            FileNotFoundError: The sender freed the message before it was received.

        Unpickling errors propagate unchanged; the message is still acknowledged.
        """
        if self._closed:
            raise BrokenPipeError("Receiver is closed")
        message = self._next_message(block, timeout)
        # A block that is gone was already freed by its sender, which expects no acknowledgement.
        allocation = attach(message)
        try:
            return load(message, allocation)
        finally:
            # Failed decoding must still release the sender's memory and capacity.
            self._ack_queue.put_nowait(message.sequence)

    def get_nowait(self) -> Any:
        """Equivalent to `get(block=False)`."""
        return self.get(block=False)

    def _next_message(self, block: bool, timeout: float | None) -> Any:
        # multiprocessing.Queue.get holds the queue's cross-process read lock while it waits,
        # so a consumer killed while idle would leave it taken and starve every other
        # consumer. Wait without it, and take it only to read a message that has arrived.
        queue: Any = self._data_queue  # Its reader, read lock and semaphore are private
        if queue._closed:
            raise ValueError(f"Queue {queue!r} is closed")
        deadline = None if timeout is None else time.monotonic() + max(timeout, 0)
        while True:
            if not queue._reader.poll(_remaining(block, deadline)):
                raise Empty
            if not queue._rlock.acquire(block, _remaining(block, deadline)):
                raise Empty
            try:
                if not queue._reader.poll():
                    continue  # Another consumer read it first
                data = queue._reader.recv_bytes()
                queue._sem.release()
            finally:
                queue._rlock.release()
            return ForkingPickler.loads(data)

    def close(self) -> None:
        """Close this process's queue handles after flushing acknowledgements.

        Idempotent. Other processes sharing the receiver are unaffected.
        """
        if self._closed:
            return
        self._closed = True
        try:
            self._data_queue.cancel_join_thread()
            self._data_queue.close()
        finally:
            self._ack_queue.close()
            self._ack_queue.join_thread()

    def __enter__(self) -> Self:
        if self._closed:
            raise BrokenPipeError("Receiver is closed")
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()
