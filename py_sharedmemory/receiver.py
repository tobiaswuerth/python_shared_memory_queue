"""The receiving endpoint returns independent local data."""

from __future__ import annotations

import errno
import pickle
import threading
import time
import weakref
from collections import OrderedDict
from contextlib import suppress
from multiprocessing import util
from multiprocessing.queues import Queue
from multiprocessing.shared_memory import SharedMemory
from queue import Empty
from types import TracebackType
from typing import TYPE_CHECKING, Any, Self, cast

from ._sharing import release_queues, share_queues
from ._timeouts import normalize_timeout
from .convert import SharedMemoryMessage, attach, drop_spare_descriptor, load, validate_message

if TYPE_CHECKING:
    from multiprocessing.synchronize import Lock

# Remapping a block for every message costs page faults on every page read. Each receiving
# thread keeps the blocks the sender reuses mapped, within the same limits as its pool.
_MAX_CACHED_MAPPINGS = 8
_MAX_CACHED_BYTES = 256 * 2**20

# How often a waiting get() checks whether its receiver was closed in the meantime.
_WAKE_INTERVAL = 0.2


def _wait_for_exit() -> None:
    """Block a daemon thread until the exiting interpreter stops it."""
    threading.Event().wait()


def _remaining(block: bool, deadline: float | None) -> float | None:
    if not block:
        return 0
    return None if deadline is None else max(0.0, deadline - time.monotonic())


class _MappingCache:
    """One thread's mappings of reusable blocks, least recently used first.

    Only its own thread reads or evicts them, so no mapping is closed while being read.
    """

    def __init__(self) -> None:
        self.blocks: OrderedDict[tuple[str, int], SharedMemory] = OrderedDict()

    def find(self, key: tuple[str, int]) -> SharedMemory | None:
        allocation = self.blocks.get(key)
        if allocation is not None:
            self.blocks.move_to_end(key)
        return allocation

    def add(self, key: tuple[str, int], allocation: SharedMemory, limit: int) -> None:
        self.blocks[key] = allocation
        # Evict the oldest mappings, but always keep the one about to be read.
        while len(self.blocks) > 1 and (
            len(self.blocks) > limit
            or sum(block.size for block in self.blocks.values()) > _MAX_CACHED_BYTES
        ):
            self.blocks.popitem(last=False)[1].close()

    def close(self) -> None:
        while self.blocks:
            # A mapping its thread is still reading stays open until that thread lets go.
            with suppress(BufferError):
                self.blocks.popitem()[1].close()


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
        capacity: int | None = None,
    ) -> None:
        self._data_queue = data_queue
        self._ack_queue = ack_queue
        # The sender never has more blocks alive than its capacity, so mapping more is waste.
        self._capacity = capacity
        # Unused here, but holding it lets the creating process drop its sender: with spawn
        # and forkserver on Linux and macOS, releasing a lock's last reference deletes its
        # name, and a child that has not started yet could no longer open it.
        self._owner_lock = owner_lock
        self._closed = False
        self._init_process_state()

    def _init_process_state(self) -> None:
        self._mappings = threading.local()
        # Every thread's cache, for close(); a finished thread's cache is freed with it.
        self._caches: weakref.WeakSet[_MappingCache] = weakref.WeakSet()
        share_queues(self._data_queue, self)

    def __getstate__(self) -> dict[str, Any]:
        state = self.__dict__.copy()
        del state["_mappings"], state["_caches"]  # Mappings belong to this process
        return state

    def __setstate__(self, state: dict[str, Any]) -> None:
        self.__dict__.update(state)
        self._init_process_state()

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
            BrokenPipeError: The receiver, or the pair's queue in this process, is closed.
            FileNotFoundError: The sender freed the message before it was received.

        Unpickling errors propagate unchanged; the message is still acknowledged.
        """
        if self._closed:
            raise BrokenPipeError("Receiver is closed")
        message = self._next_message(block, normalize_timeout(timeout))
        # A block that is gone was already freed by its sender, which expects no acknowledgement.
        allocation, kept = self._map(message)
        try:
            return load(message, allocation)
        finally:
            if not kept:
                allocation.close()
            # Failed decoding must still release the sender's memory and capacity.
            self._ack_queue.put_nowait(message.sequence)

    def get_nowait(self) -> Any:
        """Equivalent to `get(block=False)`."""
        return self.get(block=False)

    def _map(self, message: SharedMemoryMessage) -> tuple[SharedMemory, bool]:
        """The message's block, and whether this thread keeps it mapped for later messages."""
        validate_message(message)
        if not message.block:
            return attach(message), False
        cache = getattr(self._mappings, "cache", None)
        if cache is None:
            cache = self._mappings.cache = _MappingCache()
            self._caches.add(cache)
        key = (message.name, message.block)
        allocation = cache.find(key)
        if allocation is None:
            try:
                allocation = attach(message)
            except OSError as error:
                if error.errno != errno.EMFILE or not cache.blocks:
                    raise
                # Kept mappings hold descriptors; give them back rather than fail the get.
                cache.close()
                allocation = attach(message)
            drop_spare_descriptor(allocation)
            limit = min(_MAX_CACHED_MAPPINGS, self._capacity or _MAX_CACHED_MAPPINGS)
            cache.add(key, allocation, limit)
        return allocation, True

    def _next_message(self, block: bool, timeout: float | None) -> Any:
        # multiprocessing.Queue.get holds the queue's cross-process read lock while it waits,
        # so a consumer killed while idle would leave it taken and starve every other
        # consumer. Wait without it, and take it only to read a message that has arrived.
        queue: Any = self._data_queue  # Its reader, read lock and semaphore are private
        deadline = None if timeout is None else time.monotonic() + max(timeout, 0)
        while True:
            self._ensure_pair_open()
            remaining = _remaining(block, deadline)
            # Wait in slices, so that closing the receiver wakes a get() without a timeout.
            step = _WAKE_INTERVAL if remaining is None else min(remaining, _WAKE_INTERVAL)
            try:
                if not queue._reader.poll(step):
                    if remaining is not None and remaining <= step:
                        raise Empty
                    continue
                if not queue._rlock.acquire(block, _remaining(block, deadline)):
                    raise Empty
                try:
                    if not queue._reader.poll():
                        continue  # Another consumer read it first
                    data = queue._reader.recv_bytes()
                    queue._sem.release()
                finally:
                    queue._rlock.release()
            except OSError as error:
                # The queue feeder closes the pipe once the pair is closed in this process,
                # and multiprocessing's exit handlers close it as the process ends.
                try:
                    self._ensure_pair_open()
                except BrokenPipeError:
                    # On Windows, the frames of a read cut off this way hold an overlapped
                    # request that can no longer be cancelled; release it now, not at exit.
                    error.__traceback__ = None
                    raise
                if not util.is_exiting():
                    raise
                error.__traceback__ = None
                if threading.current_thread().daemon:
                    # An exception printed while the interpreter shuts down can abort the
                    # process, so let the exit stop this thread instead.
                    _wait_for_exit()
                raise BrokenPipeError("The process is exiting") from error
            return pickle.loads(data)  # What multiprocessing's ForkingPickler.loads is

    def _ensure_pair_open(self) -> None:
        if self._closed:
            raise BrokenPipeError("Receiver is closed")
        if cast(Any, self._data_queue)._closed:  # By an endpoint in this process
            raise BrokenPipeError("The pair's queue was closed in this process")

    def close(self) -> None:
        """Release kept mappings and close this process's queue handles.

        Acknowledgements are flushed first. Idempotent. The queues stay open while the
        pair's sender in this process is open; it closes them. Other processes sharing the
        receiver are unaffected.
        """
        if self._closed:
            return
        self._closed = True
        for cache in list(self._caches):
            cache.close()
        if not release_queues(self._data_queue, self):
            return
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
