import pickle
import sys
import tracemalloc
from multiprocessing.shared_memory import SharedMemory
from unittest.mock import Mock

import numpy as np
import pytest

from py_sharedmemory import convert
from py_sharedmemory.convert import (
    SharedMemoryMessage,
    deserialize,
    release_shared_memory,
    serialize,
)

from .helpers import assert_unlinked


def restore_buffer(buffer):
    return buffer


class Buffered:
    def __init__(self, payload):
        self.payload = payload

    def __reduce_ex__(self, protocol):
        return restore_buffer, (pickle.PickleBuffer(self.payload),)


class Unpicklable:
    def __reduce__(self):
        raise ValueError("Cannot serialize")


class FailsAfterBuffer(Buffered):
    def __reduce_ex__(self, protocol):
        return tuple, ((pickle.PickleBuffer(self.payload), Unpicklable()),)


ARRAYS = [
    pytest.param(lambda: np.arange(24, dtype=np.float32).reshape(4, 6), id="c-contiguous"),
    pytest.param(lambda: np.asfortranarray(np.arange(24).reshape(4, 6)), id="fortran"),
    pytest.param(lambda: np.arange(24).reshape(4, 6).T, id="transpose"),
    pytest.param(lambda: np.arange(24).reshape(4, 6)[:, ::2], id="strided"),
    pytest.param(lambda: np.arange(24)[::-1], id="negative-stride"),
    pytest.param(lambda: np.empty((0, 3), dtype=np.float32), id="empty"),
    pytest.param(lambda: np.array(3.5), id="scalar"),
    pytest.param(lambda: np.array([1 + 2j, 3 + 4j]), id="complex"),
    pytest.param(lambda: np.arange(8, dtype=">i4"), id="big-endian"),
    pytest.param(lambda: np.array(["", "hello", "世界"]), id="unicode"),
    pytest.param(lambda: np.array([None, {"value": 1}], dtype=object), id="object"),
    pytest.param(
        lambda: np.array([(1, 2.5)], dtype=[("index", "i4"), ("value", "f8")]),
        id="structured",
    ),
]


@pytest.mark.parametrize("array_factory", ARRAYS)
def test_numpy_layouts_round_trip(array_factory):
    original = array_factory()
    handle, info = serialize(original)
    try:
        result = deserialize(info)
        np.testing.assert_array_equal(result, original)
        assert result.dtype == original.dtype
        assert result.shape == original.shape
        assert result.flags.writeable
    finally:
        release_shared_memory(handle)
    # Returned data must remain valid once the shared block is gone.
    np.testing.assert_array_equal(result, original)
    assert_unlinked(info.name)


@pytest.mark.parametrize("readonly", [False, True])
def test_numpy_mutability_is_preserved(readonly):
    original = np.arange(16, dtype=np.float32)
    original.flags.writeable = not readonly
    handle, info = serialize(original)
    try:
        result = deserialize(info)
    finally:
        release_shared_memory(handle)
    assert result.flags.writeable == original.flags.writeable
    if readonly:
        with pytest.raises(ValueError, match="read-only"):
            result[0] = -1
    else:
        result[0] = -1
        assert original[0] == 0


def test_multiple_buffers_and_repeated_references():
    first = np.arange(1_024, dtype=np.float32)
    second = np.arange(2_048, dtype=np.int64)
    original = {"arrays": (first, second, first), "state": [None, True, "ready"]}
    handle, info = serialize(original)
    try:
        assert len(info.buffer_sizes) == 3
        assert sum(info.buffer_sizes) == info.size
        result = deserialize(info)
    finally:
        release_shared_memory(handle)
    assert result["arrays"][0] is result["arrays"][2]
    assert result["state"] == original["state"]
    for actual, expected in zip(result["arrays"], original["arrays"], strict=True):
        np.testing.assert_array_equal(actual, expected)


def test_out_of_band_arrays_are_aligned_after_odd_sized_ones():
    original = [
        np.ones(33, dtype=np.uint8),
        np.ones(7, dtype=np.float64),
        np.ones(3, dtype=np.complex128),
        np.ones(5, dtype=np.int16),
        np.ones(9, dtype=np.float32),
        np.ones(3, dtype=np.longdouble),
    ]
    handle, info = serialize(original)
    try:
        result = deserialize(info)
    finally:
        release_shared_memory(handle)
    for actual, expected in zip(result, original, strict=True):
        np.testing.assert_array_equal(actual, expected)
        assert actual.flags.aligned, actual.dtype


def test_each_out_of_band_buffer_owns_its_memory():
    # Keeping a small buffer must not keep its large neighbour alive.
    small, large = bytearray(b"label"), bytearray(2**20)
    handle, info = serialize((Buffered(small), Buffered(large)))
    try:
        result = deserialize(info)
    finally:
        release_shared_memory(handle)
    assert len(result[0].obj) == len(small)
    assert len(result[1].obj) == len(large)


@pytest.mark.parametrize("payload", [b"readonly", bytearray(b"writable"), bytearray()])
def test_protocol_5_buffer_mutability_and_empty_buffers(payload):
    handle, info = serialize(Buffered(payload))
    try:
        result = deserialize(info)
    finally:
        release_shared_memory(handle)
    assert bytes(result) == payload
    assert result.readonly == isinstance(payload, bytes)
    if not result.readonly and result:
        result[0] = 0
        assert payload[0] != 0


MiB = 2**20
# Pickle hands bytes-like objects from 64 KiB up to its output file by reference.
LARGE = 2**17


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param(lambda: bytes(32 * MiB), id="bytes"),
        pytest.param(lambda: bytearray(32 * MiB), id="bytearray"),
        pytest.param(
            lambda: {"step": 7, "frames": bytearray(32 * MiB), "tags": ["train"] * 100},
            id="nested",
        ),
    ],
)
def test_large_bytes_reach_shared_memory_without_an_intermediate_copy(payload):
    original = payload()
    tracemalloc.start()
    try:
        handle, info = serialize(original)
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    try:
        # pickle.dumps would allocate the whole payload again, and more while growing.
        assert peak < MiB
        assert deserialize(info) == original
    finally:
        release_shared_memory(handle)


class ResizesEarlierPayload:
    def __init__(self, payload):
        self.payload = payload

    def __reduce__(self):
        self.payload.extend(b"more")
        return int, ()


def test_bytearray_cannot_be_resized_before_it_is_copied():
    # A resize after pickle wrote the length header would corrupt the stream.
    payload = bytearray(LARGE)
    with pytest.raises(BufferError):
        serialize([payload, ResizesEarlierPayload(payload)])
    payload.extend(b"more")
    assert len(payload) == LARGE + 4


@pytest.mark.parametrize(
    ("payload", "wrap"),
    [
        pytest.param(bytearray(b"data"), FailsAfterBuffer, id="out-of-band"),
        pytest.param(bytearray(LARGE), lambda payload: [payload, Unpicklable()], id="in-band"),
    ],
)
def test_pickle_failure_releases_exported_views(payload, wrap):
    with pytest.raises(ValueError, match="Cannot serialize"):
        serialize(wrap(payload))
    # Resizing would raise BufferError if a view of the payload were retained.
    payload.extend(b"more")


@pytest.mark.parametrize(
    ("payload", "wrap"),
    [
        pytest.param(bytearray(b"data"), Buffered, id="out-of-band"),
        pytest.param(bytearray(LARGE), list, id="in-band"),
    ],
)
def test_allocation_failure_releases_exported_views(monkeypatch, payload, wrap):
    def fail_allocation(**kwargs):
        raise MemoryError("No shared memory")

    monkeypatch.setattr(convert, "SharedMemory", fail_allocation)
    with pytest.raises(MemoryError):
        serialize(wrap(payload))
    payload.extend(b"more")


def test_success_releases_exported_views():
    payload = bytearray(LARGE)
    handle, info = serialize({"payload": payload})
    try:
        payload.extend(b"more")
        assert deserialize(info) == {"payload": bytearray(LARGE)}
    finally:
        release_shared_memory(handle)


def test_payload_spanning_many_pickle_frames_round_trips():
    original = {"numbers": list(range(200_000)), "blob": b"x" * LARGE, "text": "y" * LARGE}
    handle, info = serialize(original)
    try:
        assert deserialize(info) == original
    finally:
        release_shared_memory(handle)


def test_copy_failure_unlinks_new_allocation(monkeypatch):
    created = []

    class FailingBuffer:
        def __setitem__(self, key, value):
            raise MemoryError("Copy failed")

    class FailingMapping:
        def __init__(self, **kwargs):
            self.handle = SharedMemory(**kwargs)
            self.buf = FailingBuffer()
            self.name = self.handle.name
            created.append(self.name)

        def close(self):
            self.handle.close()

        def unlink(self):
            self.handle.unlink()

    monkeypatch.setattr(convert, "SharedMemory", FailingMapping)
    with pytest.raises(MemoryError, match="Copy failed"):
        serialize(np.arange(8))
    assert len(created) == 1
    assert_unlinked(created[0])


@pytest.mark.parametrize(
    "bad_info",
    [
        None,
        SharedMemoryMessage("", (1,)),
        SharedMemoryMessage(123, (1,)),
        SharedMemoryMessage("unused", (0,)),
        SharedMemoryMessage("unused", None),
        SharedMemoryMessage("unused", ()),
        SharedMemoryMessage("unused", 1),
        SharedMemoryMessage("unused", "1"),
        SharedMemoryMessage("unused", (0, 1)),
        SharedMemoryMessage("unused", (-1, 2)),
        SharedMemoryMessage("unused", (1, -1)),
        SharedMemoryMessage("unused", (True,)),
        SharedMemoryMessage("unused", (1.0,)),
        SharedMemoryMessage("unused", [1]),
        SharedMemoryMessage("unused", (1,), -1),
        SharedMemoryMessage("unused", (1,), True),
        SharedMemoryMessage("unused", (1,), 1.0),
        SharedMemoryMessage("unused", (1,), None),
    ],
)
def test_invalid_metadata_is_rejected_before_attaching(bad_info, monkeypatch):
    def unexpected_attachment(**kwargs):
        pytest.fail("Invalid metadata must not open shared memory")

    monkeypatch.setattr(convert, "SharedMemory", unexpected_attachment)
    with pytest.raises(ValueError, match="metadata"):
        deserialize(bad_info)


@pytest.mark.parametrize("failure", ["copy", "unpickle", "oversized"])
def test_receive_failures_always_close_attachment(failure, monkeypatch):
    handle, info = serialize(np.arange(8) if failure == "copy" else 42)
    attached = []

    def attach(**kwargs):
        mapping = SharedMemory(**kwargs)
        attached.append(mapping)
        return mapping

    monkeypatch.setattr(convert, "SharedMemory", attach)
    try:
        if failure == "copy":

            def fail_copy(buffer):
                raise MemoryError("Local copy failed")

            monkeypatch.setattr(convert, "bytearray", fail_copy, raising=False)
            error = MemoryError
        elif failure == "unpickle":
            handle.buf[0] = ord("!")
            error = pickle.UnpicklingError
        else:
            probe = SharedMemory(name=handle.name)
            size = probe.size + 1
            probe.close()
            info = SharedMemoryMessage(handle.name, (size,))
            error = ValueError
        with pytest.raises(error):
            deserialize(info)
        assert len(attached) == 1
        assert attached[0].buf is None
    finally:
        release_shared_memory(handle)


def test_cleanup_tolerates_already_unlinked_blocks():
    handle, info = serialize(42)
    handle.unlink()
    release_shared_memory(handle)
    assert_unlinked(info.name)


def test_freed_block_reports_a_clear_error():
    handle, info = serialize(42)
    release_shared_memory(handle)
    with pytest.raises(FileNotFoundError, match="freed before it was received") as caught:
        deserialize(info)
    assert caught.value.filename == info.name
    assert isinstance(caught.value.__cause__, FileNotFoundError)


def test_zero_filled_block_counts_as_freed(monkeypatch):
    # Windows recreates a name freed mid-attach as an empty block; a pickle never starts at 0.
    handle, info = serialize(42)
    attached = []

    def attach(**kwargs):
        mapping = SharedMemory(**kwargs)
        attached.append(mapping)
        return mapping

    monkeypatch.setattr(convert, "SharedMemory", attach)
    try:
        handle.buf[0] = 0
        with pytest.raises(FileNotFoundError, match="freed before it was received") as caught:
            deserialize(info)
        assert caught.value.filename == info.name
        assert attached[0].buf is None
    finally:
        release_shared_memory(handle)


def test_cleanup_ignores_names_that_are_already_gone():
    # Windows never raises here because unlink is a no-op, so simulate POSIX.
    handle = Mock()
    handle.unlink.side_effect = FileNotFoundError
    release_shared_memory(handle)
    handle.close.assert_called_once()
    handle.unlink.assert_called_once()


def test_unlink_is_attempted_even_if_mapping_close_fails():
    handle = Mock()
    handle.close.side_effect = BufferError("Mapping still exported")
    with pytest.raises(BufferError):
        release_shared_memory(handle)
    handle.unlink.assert_called_once()


def test_receiver_attachments_are_not_resource_tracked(monkeypatch):
    handle, info = serialize(42)
    attachments = []

    def attach(**kwargs):
        attachments.append(kwargs)
        return SharedMemory(**kwargs)

    monkeypatch.setattr(convert, "SharedMemory", attach)
    try:
        assert deserialize(info) == 42
    finally:
        release_shared_memory(handle)
    expected = {"name": info.name}
    if sys.version_info >= (3, 13):
        expected["track"] = False
    assert attachments == [expected]


def test_in_band_payload_needs_no_intermediate_local_copy(monkeypatch):
    payload = b"x" * 2**20
    handle, info = serialize(payload)

    def unexpected_copy(buffer):
        pytest.fail("In-band payloads should be decoded directly from shared memory")

    monkeypatch.setattr(convert, "bytearray", unexpected_copy, raising=False)
    try:
        result = deserialize(info)
    finally:
        release_shared_memory(handle)
    assert result == payload


def test_small_buffer_does_not_retain_a_large_serialized_prefix():
    prefix = b"x" * 2**20
    payload = bytearray(b"small buffer")
    handle, info = serialize((prefix, Buffered(payload)))
    try:
        result = deserialize(info)
    finally:
        release_shared_memory(handle)
    assert result[0] == prefix
    assert bytes(result[1]) == payload
    assert len(result[1].obj) == len(payload)
    result[1][0] = 0
    assert payload[0] != 0
