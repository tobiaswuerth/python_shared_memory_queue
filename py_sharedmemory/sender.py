"""The producing endpoint owns each allocation until it is acknowledged."""

from __future__ import annotations

import os
import threading
import time
import warnings
from multiprocessing.queues import Queue
from multiprocessing.shared_memory import SharedMemory
from multiprocessing.util import Finalize
from queue import Empty, Full
from types import TracebackType
from typing import TYPE_CHECKING, Any, Self

from .convert import SharedMemoryMessage, release_shared_memory, serialize

if TYPE_CHECKING:
    from multiprocessing.sharedctypes import Synchronized


def _validate_capacity(capacity: int | None) -> None:
    if capacity is None:
        return
    if not isinstance(capacity, int) or isinstance(capacity, bool):
        raise TypeError("capacity must be an integer or None")
    if capacity < 0:
        raise ValueError("capacity must not be negative")


class SharedMemorySender:
    """Producing endpoint of a pair; create it with `create_shared_memory_pair`.

    Each `put` copies one object into a new shared-memory block that this sender owns
    until a receiver acknowledges it. The sender is thread-safe, but only one process
    may produce: pass it to that process as a `Process` argument before the first
    `put`. Call `wait_for_all_ack` before closing, because closing frees unread blocks.
    Leaving a `with` block or exiting the process normally while messages are still
    unacknowledged emits a `RuntimeWarning`.
    """

    def __init__(
        self,
        capacity: int | None,
        data_queue: Queue[SharedMemoryMessage],
        ack_queue: Queue[str],
        *,
        producer_owner: Synchronized[int] | None = None,
    ) -> None:
        _validate_capacity(capacity)
        self.capacity = capacity
        self._data_queue = data_queue
        self._ack_queue = ack_queue
        self._closed = False
        self._blocks: dict[str, SharedMemory] = {}
        self._pending_count = 0
        self._condition = threading.Condition()
        self._producer_owner = producer_owner
        self._process_id: int | None = None
        self._ack_thread: threading.Thread | None = None
        self._ack_error: Exception | None = None
        self._finalizer: Finalize[None] | None = None

    @property
    def closed(self) -> bool:
        """Whether `close` was called or the acknowledgement channel failed."""
        return self._closed

    def put(self, data: Any, block: bool = True, timeout: float | None = None) -> None:
        """Send a picklable object through shared memory.

        Args:
            data: Any object that pickle protocol 5 can serialize. NumPy arrays and
                other buffers are copied out-of-band without an extra pickle copy.
            block: Wait for capacity when `capacity` messages are outstanding.
            timeout: Maximum seconds to wait for capacity; `None` waits forever.

        Raises:
            queue.Full: No capacity became available in time.
            BrokenPipeError: The sender is closed.
            RuntimeError: Called from a process other than the producing process.

        Serialization errors propagate unchanged, and the sender stays usable.
        """
        self._reserve_slot(block, timeout)
        try:
            allocation, message = serialize(data)
        except BaseException:
            self._release_slot()
            raise
        self._publish(allocation, message)

    def put_nowait(self, data: Any) -> None:
        """Equivalent to `put(data, block=False)`."""
        self.put(data, block=False)

    def wait_for_all_ack(self, timeout: float | None = None) -> bool:
        """Wait until every sent message has been received and its memory freed.

        Returns:
            `True` once nothing is outstanding, `False` if `timeout` seconds pass first.

        Raises:
            BrokenPipeError: The sender is closed, or closes while waiting.
        """
        self._check_process()
        with self._condition:
            self._ensure_open()
            drained = self._condition.wait_for(
                lambda: self._pending_count == 0 or self._closed, timeout=timeout
            )
            self._ensure_open()
            return drained

    def close(self) -> None:
        """Free unread messages, stop the acknowledgement thread, and close the queues.

        Idempotent. Unread messages are discarded silently, so drain with
        `wait_for_all_ack` first. Also runs automatically when the producing process
        exits normally, warning if that discards messages.
        """
        self._check_process()
        errors: list[Exception] = []
        with self._condition:
            if self._closed:
                return
            self._closed = True
            discarding_messages = self._pending_count > 0
            for name in list(self._blocks):
                try:
                    self._release_message(name)
                except Exception as error:
                    errors.append(error)
            self._condition.notify_all()
            if self._finalizer is not None:
                self._finalizer.cancel()

        if self._ack_thread is not None and self._ack_thread is not threading.current_thread():
            self._ack_thread.join(timeout=1)
        for queue in (self._data_queue, self._ack_queue):
            try:
                if discarding_messages:
                    # Unread metadata must not keep the queue feeder alive at exit.
                    queue.cancel_join_thread()
                queue.close()
                if not discarding_messages:
                    queue.join_thread()
            except Exception as error:
                errors.append(error)
        if errors:
            raise ExceptionGroup("Failed to close shared-memory sender", errors)

    def __enter__(self) -> Self:
        self._check_process()
        self._ensure_open()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        try:
            # An exception already signals the failure; only warn on a clean exit.
            if exc_type is None:
                self._warn_if_discarding()
        finally:
            self.close()

    def __getstate__(self) -> dict[str, Any]:
        if self._process_id is not None:
            raise RuntimeError("Pass the sender to its process before the first put")
        state = self.__dict__.copy()
        del state["_condition"]
        return state

    def __setstate__(self, state: dict[str, Any]) -> None:
        self.__dict__.update(state)
        self._condition = threading.Condition()

    def _check_process(self) -> None:
        if self._process_id is not None and self._process_id != os.getpid():
            raise RuntimeError("An initialized sender cannot be used in another process")

    def _close_at_exit(self) -> None:
        try:
            self._warn_if_discarding()
        finally:
            self.close()

    def _warn_if_discarding(self) -> None:
        # Explicit close() discards silently; implicit closes make the data loss visible.
        self._check_process()
        with self._condition:
            unacknowledged = 0 if self._closed else len(self._blocks)
        if unacknowledged:
            noun = "message" if unacknowledged == 1 else "messages"
            warnings.warn(
                f"Closing the sender discards {unacknowledged} unacknowledged {noun} that "
                "receivers may not have read; call wait_for_all_ack() first",
                RuntimeWarning,
                stacklevel=3,
            )

    def _ensure_open(self) -> None:
        if self._closed:
            raise BrokenPipeError("Sender is closed") from self._ack_error

    def _reserve_slot(self, block: bool, timeout: float | None) -> None:
        self._check_process()
        deadline = None
        if block and timeout is not None:
            deadline = time.monotonic() + max(0, timeout)
        with self._condition:
            self._ensure_open()
            self._start_ack_thread()
            while self.capacity and self._pending_count >= self.capacity:
                if not block:
                    raise Full
                remaining = None if deadline is None else deadline - time.monotonic()
                if remaining is not None and remaining <= 0:
                    raise Full
                self._condition.wait(remaining)
                self._ensure_open()
            self._pending_count += 1

    def _publish(self, allocation: SharedMemory, message: SharedMemoryMessage) -> None:
        with self._condition:
            try:
                # Transfer ownership before metadata can reach the receiver.
                self._blocks[allocation.name] = allocation
            except BaseException:
                self._release_allocation(allocation)
                raise
            try:
                self._ensure_open()
                self._data_queue.put_nowait(message)
            except BaseException:
                self._release_message(allocation.name)
                raise

    def _release_slot(self) -> None:
        with self._condition:
            self._pending_count -= 1
            self._condition.notify_all()

    def _release_allocation(self, allocation: SharedMemory) -> None:
        try:
            release_shared_memory(allocation)
        finally:
            # Capacity becomes available only after memory is freed.
            self._release_slot()

    def _release_message(self, name: str) -> None:
        with self._condition:
            allocation = self._blocks.pop(name, None)
            if allocation is not None:
                self._release_allocation(allocation)

    def _start_ack_thread(self) -> None:
        if self._process_id is not None:
            return
        process_id = os.getpid()
        if self._producer_owner is not None:
            with self._producer_owner.get_lock():
                if self._producer_owner.value not in (0, process_id):
                    raise RuntimeError("A shared-memory pair supports one producing process")
                self._producer_owner.value = process_id
        self._process_id = process_id
        try:
            self._ack_thread = threading.Thread(
                target=self._process_acknowledgements, name="shared-memory-acks", daemon=True
            )
            self._finalizer = Finalize(self, self._close_at_exit, exitpriority=10)
            self._ack_thread.start()
        except BaseException:
            if self._finalizer is not None:
                self._finalizer.cancel()
            self._finalizer = None
            self._ack_thread = None
            self._process_id = None
            if self._producer_owner is not None:
                with self._producer_owner.get_lock():
                    self._producer_owner.value = 0
            raise

    def _process_acknowledgements(self) -> None:
        while not self._closed:
            try:
                self._release_message(self._ack_queue.get(timeout=0.05))
            except Empty:
                continue
            except Exception as error:
                with self._condition:
                    self._ack_error = error
                try:
                    self.close()
                except Exception as cleanup_error:
                    with self._condition:
                        self._ack_error = ExceptionGroup(
                            "Acknowledgement and cleanup failed", [error, cleanup_error]
                        )
                return
