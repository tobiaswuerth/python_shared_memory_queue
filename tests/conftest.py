import gc
import multiprocessing as mp

import pytest

from py_sharedmemory import create_shared_memory_pair


@pytest.fixture(autouse=True)
def collect_garbage():
    # Queues left in reference cycles finalize their semaphores whenever the collector
    # runs. On Python 3.12 that raises ReentrantCallError if it interrupts this thread
    # inside the resource tracker, so collect at a safe point after every test.
    yield
    gc.collect()


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
        assert not sender._pool
        assert sender._mapped_in_flight == 0
        assert sender._ack_thread is None or not sender._ack_thread.is_alive()


@pytest.fixture(params=mp.get_all_start_methods())
def ctx(request):
    return mp.get_context(request.param)
