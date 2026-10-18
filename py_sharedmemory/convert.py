"""Serialize protocol-5 pickle data through a single shared-memory block."""

import errno
import os
import pickle
import secrets
import sys
from collections.abc import Callable
from contextlib import suppress
from multiprocessing.shared_memory import SharedMemory
from typing import Any, NamedTuple, cast

# Receivers never unlink. Tracking their attachments would leak tracker entries and
# warnings when a receiver does not share the sender's resource tracker.
_ATTACH_OPTIONS: dict[str, Any] = {"track": False} if sys.version_info >= (3, 13) else {}

# Linux allocates a block's pages on first write, and a full /dev/shm (64 MiB by default in
# Docker) then kills the writing process with SIGBUS. Reserving them when the block is
# created turns that into an OSError (ENOSPC) that put() can raise.
_RESERVE_PAGES = sys.platform.startswith("linux")
_RESERVATION_UNSUPPORTED = {errno.EOPNOTSUPP, errno.EINVAL, errno.ENOSYS}

# POSIX SharedMemory keeps a descriptor next to its mapping; Windows keeps none.
_CLOSE_SPARE_DESCRIPTOR = os.name != "nt"


class SharedMemoryMessage(NamedTuple):
    name: str
    buffer_sizes: tuple[int, ...]
    # Unique per sender. Blocks are reused, so acknowledgements name the message, not the block.
    sequence: int = 0
    # Random identity of a block the sender will reuse, or 0. Freed POSIX names can be taken
    # by new blocks, so receivers key their kept mappings by name and identity.
    block: int = 0

    @property
    def size(self) -> int:
        return sum(self.buffer_sizes)


def create_shared_memory(size: int) -> SharedMemory:
    """Create a block, reserving its pages and dropping its spare descriptor where possible."""
    allocation = SharedMemory(create=True, size=size)
    block: Any = allocation  # Its descriptor is private
    try:
        if _RESERVE_PAGES:
            _reserve_pages(block._fd, size)
    except BaseException:
        release_shared_memory(allocation)
        raise
    drop_spare_descriptor(allocation)
    block._block_token = 1 + secrets.randbits(63)
    return allocation


def drop_spare_descriptor(allocation: SharedMemory) -> None:
    """Close POSIX SharedMemory's own descriptor; its mapping holds a duplicate.

    Keeping both would cost every block kept mapped two of the process's descriptors.
    """
    if _CLOSE_SPARE_DESCRIPTOR:
        block: Any = allocation
        os.close(block._fd)
        block._fd = -1


def _reserve_pages(descriptor: int, size: int) -> None:
    try:
        cast(Any, os).posix_fallocate(descriptor, 0, size)  # Linux-only, unlike its stubs' os
    except OSError as error:
        if error.errno not in _RESERVATION_UNSUPPORTED:
            raise  # Above all ENOSPC: /dev/shm cannot hold the block


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
        token = getattr(allocation, "_block_token", 0)
        return allocation, SharedMemoryMessage(allocation.name, buffer_sizes, sequence, token)
    except BaseException:
        if allocation is not None:
            release_shared_memory(allocation)
        raise
    finally:
        for view in (*chunks, *buffers):
            view.release()


def deserialize(message: SharedMemoryMessage) -> Any:
    """Unpickle with owned local buffers and always close the shared mapping."""
    allocation = attach(message)
    try:
        return load(message, allocation)
    finally:
        allocation.close()


def attach(message: SharedMemoryMessage) -> SharedMemory:
    """Map the block that valid metadata names, or explain why it no longer exists."""
    validate_message(message)
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
    """Unpickle an attached block with owned local buffers; the caller keeps the mapping."""
    if message.size > allocation.size:
        raise ValueError("Shared-memory block is smaller than its metadata")
    shared = cast(memoryview, allocation.buf)
    pickle_size = message.buffer_sizes[0]
    buffers = _copy_buffers(shared, pickle_size, message.buffer_sizes[1:])
    with shared[:pickle_size] as pickle_data:
        return pickle.loads(pickle_data, buffers=buffers)


def validate_message(message: SharedMemoryMessage) -> None:
    """Reject metadata that is malformed before any of it is used."""
    if not isinstance(message, SharedMemoryMessage) or not isinstance(message.name, str):
        raise ValueError("Invalid shared-memory metadata")
    sizes = message.buffer_sizes
    if (
        not message.name
        or any(type(field) is not int or field < 0 for field in (message.sequence, message.block))
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
