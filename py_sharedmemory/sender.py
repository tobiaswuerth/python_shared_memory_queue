"""The producing endpoint owns each block until it is acknowledged, then reuses it."""

from __future__ import annotations

import mmap
import os
import sys
import threading
import time
import warnings
from multiprocessing.queues import Queue
from multiprocessing.shared_memory import SharedMemory
from multiprocessing.synchronize import SEM_VALUE_MAX  # type: ignore[attr-defined]  # not in typeshed
from multiprocessing.util import Finalize
from queue import Empty, Full
from types import TracebackType
from typing import TYPE_CHECKING, Any, Self

from ._timeouts import normalize_timeout
from .convert import SharedMemoryMessage, create_shared_memory, release_shared_memory, serialize

if TYPE_CHECKING:
    from multiprocessing.synchronize import Lock

# A POSIX block lives on by name until it is unlinked, so the sender can unmap the blocks it
# cannot keep mapped and close their file descriptors. Windows frees a block with its last
# handle, so it keeps every unread block mapped.
_UNMAP_EXCESS_BLOCKS = sys.platform != "win32"

# Each mapped POSIX block holds a file descriptor. Unread blocks beyond this many are
# unmapped right after writing, and freed rather than reused once acknowledged.
_MAX_MAPPED_BLOCKS = 8

# Writing to pages that are already mapped costs a fraction of faulting in fresh ones, so
# acknowledged blocks are reused. A message that no pooled block fits frees one, so blocks
# never outnumber the capacity; these limits also bound the memory kept while idle.
_MAX_POOLED_BLOCKS = 8
_MAX_POOLED_BYTES = 256 * 2**20

# A pooled block serves a message that fills at least half of it, or any that fits a small one.
_SMALL_BLOCK_BYTES = 2**20

# The metadata queue holds at most SEM_VALUE_MAX unread items (32,767 on macOS); beyond
# that its put would raise queue.Full even for a blocking put on an unbounded sender.
_MAX_OUTSTANDING = SEM_VALUE_MAX


def _validate_capacity(capacity: int | None) -> None:
    if capacity is None:
        return
    if not isinstance(capacity, int) or isinstance(capacity, bool):
        raise TypeError("capacity must be an integer or None")
    if capacity < 0:
        raise ValueError("capacity must not be negative")


def _fits(allocation: SharedMemory, size: int) -> bool:
    return size <= allocation.size <= max(2 * size, _SMALL_BLOCK_BYTES)


class SharedMemorySender:
    """Producing endpoint of a pair; create it with `create_shared_memory_pair`.

    Each `put` copies one object into a shared-memory block that this sender owns until a
    receiver acknowledges it; acknowledged blocks are reused for later messages. The
    sender is thread-safe, but only one process may produce: pass it to that process as a
    `Process` argument before the first `put`. Call `wait_for_all_ack` before closing,
    because closing frees unread blocks. Leaving a `with` block or exiting the process
    normally while messages are still unacknowledged emits a `RuntimeWarning`.
    """

    def __init__(
        self,
        capacity: int | None,
        data_queue: Queue[SharedMemoryMessage],
        ack_queue: Queue[int],
        *,
        owner_lock: Lock | None = None,
    ) -> None:
        _validate_capacity(capacity)
        self.capacity = capacity
        self._data_queue = data_queue
        self._ack_queue = ack_queue
        self._closed = False
        # Unacknowledged blocks by message sequence number, oldest first.
        self._blocks: dict[int, SharedMemory] = {}
        # Acknowledged, still-mapped blocks kept for reuse, oldest first.
        self._pool: list[SharedMemory] = []
        self._mapped_in_flight = 0
        self._sequence = 0
        self._pending_count = 0
        self._condition = threading.Condition()
        # The producing process takes this pair-wide lock on its first put and never
        # releases it, so any other process trying to produce is refused.
        self._owner_lock = owner_lock
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
            data: Any object that pickle protocol 5 can serialize. NumPy arrays, other
                buffers, and large bytes-like objects are copied without an extra
                pickle copy.
            block: Wait for capacity when `capacity` messages are outstanding.
            timeout: Maximum seconds to wait for capacity; `None` waits forever.

        Raises:
            queue.Full: No capacity became available in time.
            BrokenPipeError: The sender, or the pair's queue in this process, is closed.
            RuntimeError: Called from a process other than the producing process.

        Serialization errors propagate unchanged, and the sender stays usable.
        """
        sequence = self._reserve_slot(block, timeout)
        try:
            allocation, message = serialize(data, sequence, self._allocate)
        except BaseException:
            self._release_slot()
            raise
        self._publish(allocation, message)

    def put_nowait(self, data: Any) -> None:
        """Equivalent to `put(data, block=False)`."""
        self.put(data, block=False)

    def wait_for_all_ack(self, timeout: float | None = None) -> bool:
        """Wait until every sent message has been received and its block released.

        Returns:
            `True` once nothing is outstanding, `False` if `timeout` seconds pass first.

        Raises:
            BrokenPipeError: The sender is closed, or closes while waiting.
        """
        self._check_process()
        timeout = normalize_timeout(timeout)
        with self._condition:
            self._ensure_open()
            drained = self._condition.wait_for(
                lambda: self._pending_count == 0 or self._closed, timeout=timeout
            )
            self._ensure_open()
            return drained

    def close(self) -> None:
        """Free unread messages and reusable blocks, stop acknowledgements, close the queues.

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
            for sequence in list(self._blocks):
                try:
                    self._discard(sequence)
                except Exception as error:
                    errors.append(error)
            while self._pool:
                try:
                    release_shared_memory(self._pool.pop())
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

    def _reserve_slot(self, block: bool, timeout: float | None) -> int:
        self._check_process()
        timeout = normalize_timeout(timeout)
        deadline = None
        if block and timeout is not None:
            deadline = time.monotonic() + max(0, timeout)
        with self._condition:
            self._ensure_open()
            self._start_ack_thread()
            limit = min(self.capacity or _MAX_OUTSTANDING, _MAX_OUTSTANDING)
            while self._pending_count >= limit:
                if not block:
                    raise Full
                remaining = None if deadline is None else deadline - time.monotonic()
                if remaining is not None and remaining <= 0:
                    raise Full
                self._condition.wait(remaining)
                self._ensure_open()
            self._pending_count += 1
            self._sequence += 1
            return self._sequence

    def _allocate(self, size: int) -> SharedMemory:
        with self._condition:
            fitting = [allocation for allocation in self._pool if _fits(allocation, size)]
            if fitting:
                allocation = min(fitting, key=lambda candidate: candidate.size)
                self._pool.remove(allocation)
                return allocation
            if self._pool:
                # The pool no longer matches the messages being sent. Freeing a block for
                # the new one keeps the number of blocks within the capacity.
                release_shared_memory(self._pool.pop(0))
        # Whole pages cost no extra memory and let slightly larger messages reuse the block.
        return create_shared_memory(-(-size // mmap.PAGESIZE) * mmap.PAGESIZE)

    def _publish(self, allocation: SharedMemory, message: SharedMemoryMessage) -> None:
        with self._condition:
            try:
                if _UNMAP_EXCESS_BLOCKS and self._mapped_in_flight >= _MAX_MAPPED_BLOCKS:
                    allocation.close()
                # Transfer ownership before metadata can reach the receiver.
                self._blocks[message.sequence] = allocation
            except BaseException:
                self._release_allocation(allocation)
                raise
            if allocation.buf is not None:
                self._mapped_in_flight += 1
            if allocation.buf is None or allocation.size > _MAX_POOLED_BYTES:
                # Freed once acknowledged, so receivers should not keep it mapped.
                message = message._replace(block=0)
            try:
                self._ensure_open()
                self._data_queue.put_nowait(message)
            except BaseException as error:
                self._discard(message.sequence)
                if isinstance(error, ValueError):  # Only a closed multiprocessing.Queue
                    raise BrokenPipeError("The pair's queue was closed in this process") from error
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

    def _discard(self, sequence: int) -> None:
        with self._condition:
            allocation = self._blocks.pop(sequence)
            if allocation.buf is not None:
                self._mapped_in_flight -= 1
            self._release_allocation(allocation)

    def _acknowledge(self, sequence: int) -> None:
        with self._condition:
            # Unknown and repeated acknowledgements never release a newer message's block.
            allocation = self._blocks.pop(sequence, None)
            if allocation is None:
                return
            try:
                if allocation.buf is None:
                    release_shared_memory(allocation)
                else:
                    self._mapped_in_flight -= 1
                    self._recycle(allocation)
            finally:
                # Capacity becomes available only once the block is freed or reusable.
                self._release_slot()

    def _recycle(self, allocation: SharedMemory) -> None:
        if allocation.size > _MAX_POOLED_BYTES:
            release_shared_memory(allocation)
            return
        self._pool.append(allocation)
        # Free the oldest blocks first; the newest best match the messages being sent.
        while len(self._pool) > _MAX_POOLED_BLOCKS or (
            sum(pooled.size for pooled in self._pool) > _MAX_POOLED_BYTES
        ):
            release_shared_memory(self._pool.pop(0))

    def _start_ack_thread(self) -> None:
        if self._process_id is not None:
            return
        if self._owner_lock is not None and not self._owner_lock.acquire(block=False):
            raise RuntimeError("A shared-memory pair supports one producing process")
        self._process_id = os.getpid()
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
            if self._owner_lock is not None:
                self._owner_lock.release()
            raise

    def _process_acknowledgements(self) -> None:
        while not self._closed:
            try:
                self._acknowledge(self._ack_queue.get(timeout=0.05))
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
