import multiprocessing as mp

import pytest

from memory import create_shared_memory_pair


@pytest.fixture
def make_pair():
    pairs = []

    def create(capacity=1, *, ctx=None):
        pair = create_shared_memory_pair(capacity, ctx=ctx)
        pairs.append(pair)
        return pair

    yield create
    for sender, receiver in reversed(pairs):
        sender.close()
        receiver.close()
        assert not sender._blocks
        assert sender._ack_thread is None or not sender._ack_thread.is_alive()


@pytest.fixture(params=mp.get_all_start_methods())
def ctx(request):
    return mp.get_context(request.param)
