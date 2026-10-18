"""The receiving endpoint returns independent local data."""

from __future__ import annotations

from multiprocessing.queues import Queue
from types import TracebackType
from typing import Any, Self

from .convert import SharedMemoryMessage, attach, load


class SharedMemoryReceiver:
    """Consuming endpoint of a pair; create it with `create_shared_memory_pair`.

    Several threads and processes may share one receiver; each message is delivered to
    exactly one of them. Every received block is acknowledged, even when decoding fails,
    so the sender can free its memory.
    """

    def __init__(self, data_queue: Queue[SharedMemoryMessage], ack_queue: Queue[int]) -> None:
        self._data_queue = data_queue
        self._ack_queue = ack_queue
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
            The object, which stays valid after the shared memory is freed. Arrays keep
            their original writability.

        Raises:
            queue.Empty: No message arrived in time.
            BrokenPipeError: The receiver is closed.
            FileNotFoundError: The sender freed the message before it was received.

        Unpickling errors propagate unchanged; the message is still acknowledged.
        """
        if self._closed:
            raise BrokenPipeError("Receiver is closed")
        message = self._data_queue.get(block, timeout)
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
