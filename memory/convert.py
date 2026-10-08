"""Serialize protocol-5 pickle data through a single shared-memory block."""

import pickle
from multiprocessing.shared_memory import SharedMemory
from typing import Any, NamedTuple


class SharedMemoryMessage(NamedTuple):
    name: str
    buffer_sizes: tuple[int, ...]

    @property
    def size(self) -> int:
        return sum(self.buffer_sizes)


def release_shared_memory(allocation: SharedMemory) -> None:
    """Release both the mapping and its name, including on error paths."""
    try:
        allocation.close()
    finally:
        try:
            allocation.unlink()
        except FileNotFoundError:
            pass


def serialize(data: Any) -> tuple[SharedMemory, SharedMemoryMessage]:
    """Copy a pickle and its out-of-band buffers into owned shared memory."""
    buffers: list[memoryview] = []
    allocation = None

    def append_buffer(buffer: pickle.PickleBuffer) -> None:
        buffers.append(buffer.raw())

    try:
        pickle_data = pickle.dumps(data, protocol=5, buffer_callback=append_buffer)
        buffer_sizes = (len(pickle_data), *(buffer.nbytes for buffer in buffers))
        allocation = SharedMemory(create=True, size=sum(buffer_sizes))
        allocation.buf[: len(pickle_data)] = pickle_data
        offset = len(pickle_data)
        for buffer in buffers:
            allocation.buf[offset : offset + buffer.nbytes] = buffer
            offset += buffer.nbytes
        return allocation, SharedMemoryMessage(allocation.name, buffer_sizes)
    except BaseException:
        if allocation is not None:
            release_shared_memory(allocation)
        raise
    finally:
        for buffer in buffers:
            buffer.release()


def deserialize(message: SharedMemoryMessage) -> Any:
    """Unpickle with owned local buffers and always close the shared mapping."""
    _validate_message(message)
    allocation = SharedMemory(name=message.name)
    try:
        if message.size > allocation.size:
            raise ValueError("Shared-memory block is smaller than its metadata")
        pickle_size = message.buffer_sizes[0]
        buffers = []
        if len(message.buffer_sizes) > 1:
            with allocation.buf[pickle_size : message.size] as shared_buffer:
                buffers = _copy_buffers(shared_buffer, message.buffer_sizes[1:])
        with allocation.buf[:pickle_size] as pickle_data:
            return pickle.loads(pickle_data, buffers=buffers)
    finally:
        allocation.close()


def _validate_message(message: SharedMemoryMessage) -> None:
    if not isinstance(message, SharedMemoryMessage) or not isinstance(message.name, str):
        raise ValueError("Invalid shared-memory metadata")
    sizes = message.buffer_sizes
    if (
        not message.name
        or not isinstance(sizes, tuple)
        or not sizes
        or any(type(size) is not int or size < 0 for size in sizes)
        or sizes[0] == 0
    ):
        raise ValueError("Invalid shared-memory metadata")


def _copy_buffers(shared_buffer: memoryview, sizes: tuple[int, ...]) -> list[memoryview]:
    # One writable copy; each returned view keeps its local storage alive.
    buffers = []
    with memoryview(bytearray(shared_buffer)) as local_buffer:
        offset = 0
        for size in sizes:
            buffers.append(local_buffer[offset : offset + size])
            offset += size
    return buffers
