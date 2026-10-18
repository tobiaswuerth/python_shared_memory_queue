"""Serialize protocol-5 pickle data through a single shared-memory block."""

import errno
import pickle
import sys
from collections.abc import Callable
from contextlib import suppress
from multiprocessing.shared_memory import SharedMemory
from typing import Any, NamedTuple, cast

# Receivers never unlink. Tracking their attachments would leak tracker entries and
# warnings when a receiver does not share the sender's resource tracker.
_ATTACH_OPTIONS: dict[str, Any] = {"track": False} if sys.version_info >= (3, 13) else {}


class SharedMemoryMessage(NamedTuple):
    name: str
    buffer_sizes: tuple[int, ...]
    # Unique per sender. Blocks are reused, so acknowledgements name the message, not the block.
    sequence: int = 0

    @property
    def size(self) -> int:
        return sum(self.buffer_sizes)


def create_shared_memory(size: int) -> SharedMemory:
    return SharedMemory(create=True, size=size)


def release_shared_memory(allocation: SharedMemory) -> None:
    """Release both the mapping and its name, including on error paths."""
    try:
        allocation.close()
    finally:
        with suppress(FileNotFoundError):
            allocation.unlink()


class _PickleChunks(list[memoryview]):
    """Pickle output kept as views of what pickle wrote rather than one joined copy."""

    def write(self, data: Any) -> None:
        # Pickle passes bytes and bytearrays of 64 KiB or more as the objects themselves.
        # A view also stops a bytearray from being resized before it is copied, which would
        # leave its length header in the stream wrong.
        self.append(memoryview(data))


def serialize(
    data: Any,
    sequence: int = 0,
    allocate: Callable[[int], SharedMemory] = create_shared_memory,
) -> tuple[SharedMemory, SharedMemoryMessage]:
    """Copy a pickle and its out-of-band buffers into an owned block of at least that size.

    Large bytes, bytearrays, and contiguous arrays are copied once, straight from the
    original objects into shared memory. `allocate` returns a new or reused block; this
    function frees it if copying fails.
    """
    chunks = _PickleChunks()
    buffers: list[memoryview] = []
    allocation = None

    def append_buffer(buffer: pickle.PickleBuffer) -> None:
        buffers.append(buffer.raw())

    try:
        pickle.Pickler(chunks, protocol=5, buffer_callback=append_buffer).dump(data)
        pickle_size = sum(chunk.nbytes for chunk in chunks)
        buffer_sizes = (pickle_size, *(buffer.nbytes for buffer in buffers))
        allocation = allocate(sum(buffer_sizes))
        shared = cast(memoryview, allocation.buf)
        offset = 0
        for view in (*chunks, *buffers):
            shared[offset : offset + view.nbytes] = view
            offset += view.nbytes
        return allocation, SharedMemoryMessage(allocation.name, buffer_sizes, sequence)
    except BaseException:
        if allocation is not None:
            release_shared_memory(allocation)
        raise
    finally:
        for view in (*chunks, *buffers):
            view.release()


def deserialize(message: SharedMemoryMessage) -> Any:
    """Unpickle with owned local buffers and always close the shared mapping."""
    return load(message, attach(message))


def attach(message: SharedMemoryMessage) -> SharedMemory:
    """Map the block that valid metadata names, or explain why it no longer exists."""
    _validate_message(message)
    try:
        allocation = SharedMemory(name=message.name, **_ATTACH_OPTIONS)
    except FileNotFoundError as error:
        raise _freed_block_error(message) from error
    # Attaching on Windows is not atomic: a name freed in between is recreated as a new,
    # zero-filled block. Every pickle starts with a nonzero opcode.
    if cast(memoryview, allocation.buf)[0] == 0:
        allocation.close()
        raise _freed_block_error(message)
    return allocation


def _freed_block_error(message: SharedMemoryMessage) -> FileNotFoundError:
    return FileNotFoundError(
        errno.ENOENT,
        "Shared-memory block was freed before it was received; the sender probably "
        "closed without calling wait_for_all_ack()",
        message.name,
    )


def load(message: SharedMemoryMessage, allocation: SharedMemory) -> Any:
    """Unpickle an attached block with owned local buffers, then close the mapping."""
    try:
        if message.size > allocation.size:
            raise ValueError("Shared-memory block is smaller than its metadata")
        shared = cast(memoryview, allocation.buf)
        pickle_size = message.buffer_sizes[0]
        buffers = _copy_buffers(shared, pickle_size, message.buffer_sizes[1:])
        with shared[:pickle_size] as pickle_data:
            return pickle.loads(pickle_data, buffers=buffers)
    finally:
        allocation.close()


def _validate_message(message: SharedMemoryMessage) -> None:
    if not isinstance(message, SharedMemoryMessage) or not isinstance(message.name, str):
        raise ValueError("Invalid shared-memory metadata")
    sizes = message.buffer_sizes
    if (
        not message.name
        or type(message.sequence) is not int
        or message.sequence < 0
        or not isinstance(sizes, tuple)
        or not sizes
        or any(type(size) is not int or size < 0 for size in sizes)
        or sizes[0] == 0
    ):
        raise ValueError("Invalid shared-memory metadata")


def _copy_buffers(shared: memoryview, offset: int, sizes: tuple[int, ...]) -> list[memoryview]:
    # A separate writable copy per buffer starts aligned for any dtype and is freed on its own,
    # so a small array cannot keep a large neighbour alive.
    buffers = []
    for size in sizes:
        with shared[offset : offset + size] as shared_buffer:
            buffers.append(memoryview(bytearray(shared_buffer)))
        offset += size
    return buffers
