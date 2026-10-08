from typing import NamedTuple

import numpy as np
import pytest


class SampleTuple(NamedTuple):
    count: int
    score: float
    label: str


@pytest.mark.parametrize(
    "data",
    [
        None,
        (None, None),
        [None, None],
        {None: None},
        {None},
        0,
        37,
        -37,
        2**128,
        -(2**128),
        0.0,
        21_474.8364,
        -21_474.8364,
        True,
        False,
        b"",
        b"Hello world!",
        bytearray(b"mutable"),
        "",
        "Hello world!",
        pytest.param("Unicode: 世界 🚀\n" * 1_000, id="long-unicode-string"),
        [],
        [1, 2, 3],
        [[1, 2], [3, 4]],
        [1, "a", 3.4, None, b"bytes", (2, 3)],
        [[], (), []],
        (),
        (1, 2, 3),
        ((1, 2), [3, 4]),
        (1, "a", 3.4, None, b"bytes"),
        ((), ()),
        SampleTuple(1, 2.3, "Hello world!"),
        {},
        {1: "a", "b": 2, "c": None},
        {1: {2: 3}, "a": ["b", "c"]},
        {1: {}, "a": {}},
        set(),
        {1, 2, "a", None},
        frozenset({1, 2, 3}),
        np.dtype(np.float32),
        np.dtype(np.bool_),
    ],
)
def test_picklable_objects_round_trip(make_pair, data):
    sender, receiver = make_pair()
    sender.put(data)
    assert receiver.get(timeout=3) == data
    assert sender.wait_for_all_ack(timeout=3)


def test_recursive_container_identity(make_pair):
    sender, receiver = make_pair()
    original = []
    original.append(original)
    sender.put(original)
    result = receiver.get(timeout=3)
    assert result[0] is result
    assert sender.wait_for_all_ack(timeout=3)


def test_large_ml_batch(make_pair):
    sender, receiver = make_pair()
    rng = np.random.default_rng(42)
    batch = {
        "observations": rng.random((32, 4, 84, 84), dtype=np.float32),
        "actions": rng.integers(0, 6, size=32),
        "rewards": rng.random(32, dtype=np.float32),
        "done": np.zeros(32, dtype=bool),
    }
    sender.put(batch)
    result = receiver.get(timeout=3)
    assert sender.wait_for_all_ack(timeout=3)
    for key, expected in batch.items():
        np.testing.assert_array_equal(result[key], expected)
        assert result[key].dtype == expected.dtype
        assert result[key].flags.writeable
