"""The receiving endpoint returns independent local data."""

from multiprocessing.queues import Queue
from typing import Any, Self

from .convert import SharedMemoryMessage, deserialize


class SharedMemoryReceiver:
    """Receive objects and acknowledge them even when decoding fails."""

    def __init__(self, data_queue: Queue, ack_queue: Queue):
        self._data_queue = data_queue
        self._ack_queue = ack_queue
        self._closed = False

    @property
    def closed(self) -> bool:
        return self._closed

    def get(self, block: bool = True, timeout: float | None = None) -> Any:
        """Receive data; raise queue.Empty when no message arrives in time."""
        if self._closed:
            raise BrokenPipeError("Receiver is closed")
        message = self._data_queue.get(block, timeout)
        try:
            return deserialize(message)
        finally:
            # Failed decoding must still release the sender's memory and capacity.
            if isinstance(message, SharedMemoryMessage):
                self._ack_queue.put_nowait(message.name)

    def get_nowait(self) -> Any:
        return self.get(block=False)

    def close(self) -> None:
        """Close local queues and flush acknowledgements before returning."""
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

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()
