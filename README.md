# py-sharedmemory

[![CI](https://github.com/tobiaswuerth/python_shared_memory_queue/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/tobiaswuerth/python_shared_memory_queue/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/py-sharedmemory)](https://pypi.org/project/py-sharedmemory/)
[![Python](https://img.shields.io/badge/dynamic/toml?url=https%3A%2F%2Fraw.githubusercontent.com%2Ftobiaswuerth%2Fpython_shared_memory_queue%2Fmain%2Fpyproject.toml&query=%24.project%5B%27requires-python%27%5D&label=python&logo=python&logoColor=white)](https://pypi.org/project/py-sharedmemory/)
[![Downloads](https://img.shields.io/pypi/dm/py-sharedmemory)](https://pypistats.org/packages/py-sharedmemory)
[![Coverage](https://img.shields.io/badge/coverage-100%25-brightgreen)](https://github.com/tobiaswuerth/python_shared_memory_queue/actions/workflows/ci.yml)
[![License](https://img.shields.io/github/license/tobiaswuerth/python_shared_memory_queue)](https://github.com/tobiaswuerth/python_shared_memory_queue/blob/main/LICENSE)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
[![Checked with mypy](https://www.mypy-lang.org/static/mypy_badge.svg)](https://mypy-lang.org/)
[![pre-commit](https://img.shields.io/badge/pre--commit-enabled-brightgreen?logo=pre-commit)](https://github.com/pre-commit/pre-commit)

**A fast `multiprocessing.Queue` alternative for large Python objects and NumPy arrays.**
Payloads travel between processes through shared memory instead of pipes; the queues
underneath carry only a few bytes of metadata per message. Multi-megabyte batches,
images, and simulation states arrive 1.4× (Windows) to 6× (Linux) faster, on macOS even
small messages beat a bounded `multiprocessing.Queue`, and transfers too large for
`multiprocessing.Queue` still work.

- **Familiar API:** `put` / `get` with `block`, `timeout`, `put_nowait` / `get_nowait`,
  `queue.Full`, and `queue.Empty`.
- **Any picklable object:** NumPy arrays, other protocol-5 buffers, and large `bytes` and
  `bytearray` objects are copied straight into shared memory, without an intermediate
  pickle copy.
- **Bounded memory:** a capacity limit applies backpressure, and received blocks are
  reused for later messages, so shared memory stays within `capacity × message size`.
- **One producer, many consumers:** consumer processes and threads share one receiver.
- **Portable:** Linux, macOS, and Windows on x86-64 and ARM64; `spawn`, `fork`, and
  `forkserver`; Python 3.11–3.15, including free-threaded 3.14t and 3.15t.
- **Lightweight:** no dependencies, fully typed (PEP 561), and 100% test coverage.

Built for **machine-learning data pipelines, reinforcement-learning actors and replay
buffers, scientific computing, and image or video processing**, where large payloads
make pipe I/O the bottleneck.

## Installation

```sh
pip install py-sharedmemory
```

> **Upgrading from 3.x?** The import package is now `py_sharedmemory` (previously
> `memory`). Replace `from memory import ...` with `from py_sharedmemory import ...`;
> nothing else changed.

## Quick start

```python
import multiprocessing as mp

from py_sharedmemory import create_shared_memory_pair


def produce(sender):
    with sender:
        sender.put({"step": 42, "frames": bytearray(16_000_000)})
        sender.wait_for_all_ack()  # Keep the data alive until it is received


if __name__ == "__main__":
    ctx = mp.get_context("spawn")
    sender, receiver = create_shared_memory_pair(capacity=2, ctx=ctx)
    worker = ctx.Process(target=produce, args=(sender,))
    worker.start()
    with receiver:
        batch = receiver.get(timeout=30)
    worker.join()
    sender.close()
```

More complete, runnable programs:

- [`examples/basic.py`](https://github.com/tobiaswuerth/python_shared_memory_queue/blob/main/examples/basic.py):
  a producer process with timeouts and error handling.
- [`examples/numpy_batches.py`](https://github.com/tobiaswuerth/python_shared_memory_queue/blob/main/examples/numpy_batches.py):
  streaming NumPy batches with backpressure and an end-of-stream marker.
- [`examples/worker_pool.py`](https://github.com/tobiaswuerth/python_shared_memory_queue/blob/main/examples/worker_pool.py):
  fanning large tasks out to several consumer processes.

## When to use it

Use py-sharedmemory when individual messages are large: **from about 1 MB on Linux and
Windows x86-64, and from about 10 MB on Windows ARM64**. Typical payloads are NumPy
arrays, image or video frames, RL rollouts, large `bytes`, or big nested objects. On
**macOS** it was faster at every size measured, from 1 kB up; see
[Performance](#performance).

On Linux and Windows, prefer `multiprocessing.Queue` for small messages: up to about
100 kB it is up to about 3.5× faster, because mapping a shared-memory block for every
message costs more than the pipe.

On free-threaded Python (`python3.14t` and later), threads run Python code in parallel.
If everything your workers use is free-threading safe, threads and `queue.Queue` pass
objects by reference without any copy. Use processes, and this package, when you need
process isolation or rely on extensions that re-enable the GIL.

To let several processes read and write **the same** array in place, use
[`multiprocessing.shared_memory`](https://docs.python.org/3/library/multiprocessing.shared_memory.html)
directly. py-sharedmemory delivers an independent copy of each message.

## Performance

![Speedup of py-sharedmemory over multiprocessing.Queue for each platform and payload size from 1 kB to 100 MB](https://raw.githubusercontent.com/tobiaswuerth/python_shared_memory_queue/main/benchmarks/results/platforms.png)

Speedup over `multiprocessing.Queue`; above 1× means py-sharedmemory is faster.

| Platform | 1 kB | 10 kB | 100 kB | 1 MB | 10 MB | 100 MB |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Linux x64 | 0.29× | 0.29× | 0.44× | **3.00×** | **4.12×** | **2.95×** |
| Linux arm64 | 0.36× | 0.37× | 0.57× | **2.57×** | **5.75×** | **2.31×** |
| Windows x64 | 0.29× | 0.30× | 0.36× | **1.38×** | **1.42×** | **1.35×** |
| Windows arm64 | 0.28× | 0.30× | 0.55× | 0.65× | **1.52×** | **1.51×** |
| macOS arm64 | **11.35×** | **10.86×** | **11.18×** | **7.94×** | **3.49×** | **3.53×** |

- **Linux, x86-64 and ARM64:** 2.3–5.8× faster from 1 MB. Up to 100 kB,
  `multiprocessing.Queue` is up to 3.5× faster.
- **Windows x86-64:** about 1.4× faster from 1 MB, and about 3× slower up to 100 kB.
- **Windows ARM64:** the crossover comes later. `multiprocessing.Queue` still wins at 1 MB,
  and py-sharedmemory is 1.5× faster from 10 MB.
- **macOS (Apple Silicon):** 3.5–11× faster at every size. macOS has no timed semaphore
  wait, so a full bounded `multiprocessing.Queue` whose `put` has a timeout, as here,
  [polls with sleeps of up to 20 ms](https://github.com/python/cpython/blob/main/Modules/_multiprocessing/semaphore.c).
  py-sharedmemory enforces its capacity with a thread condition instead.

Measured by the [benchmark workflow](https://github.com/tobiaswuerth/python_shared_memory_queue/blob/main/.github/workflows/benchmark.yml)
on GitHub-hosted runners on 2026-10-18: py-sharedmemory 4.2.1, Python 3.14.8, NumPy 2.5.4,
contiguous float32 arrays, `spawn`, capacity 2 for both queues, and the median of 5 runs
that alternate between the two. The runners are shared virtual machines, so compare the
speedups, not absolute throughput; run-to-run spread is mostly within 20%, and up to
about 50% on macOS. See the [raw results](https://github.com/tobiaswuerth/python_shared_memory_queue/tree/main/benchmarks/results/platforms).
The workflow reruns on every release.

### Large messages on a desktop

Beyond 100 MB, which shared runners cannot hold, a desktop shows the same picture up to
10 GB, where `multiprocessing.Queue` fails:

![Transfer speed relative to multiprocessing.Queue, from 16 bytes to 10 GB](https://raw.githubusercontent.com/tobiaswuerth/python_shared_memory_queue/main/benchmarks/results/windows-numpy.png)

| Payload per message | `multiprocessing.Queue` | py-sharedmemory | Speedup |
| --- | ---: | ---: | ---: |
| 100 kB | 2,315 MiB/s | 797 MiB/s | 0.34× |
| 1 MB | 1,022 MiB/s | 2,056 MiB/s | **2.01×** |
| 10 MB | 1,177 MiB/s | 2,387 MiB/s | **2.03×** |
| 100 MB | 1,034 MiB/s | 1,820 MiB/s | **1.76×** |
| 1 GB | 855 MiB/s | 1,262 MiB/s | **1.47×** |
| 10 GB | failed (Windows error 87) | 8.0 s per message | — |

Core i9-14900K, 191.8 GiB RAM, Windows 11, Python 3.12.10, NumPy 2.5.3, measured on
2026-10-08. Contiguous float32 arrays, `spawn`, capacity 2, median of 5 runs excluding
startup and warmup; up to 1,000 messages or 512 MiB per run, at least one message.
1 GB = 10⁹ bytes. See the [raw results](https://github.com/tobiaswuerth/python_shared_memory_queue/blob/main/benchmarks/results/windows-numpy.json).
These numbers show transfer speed; I/O load and stability depend on the workload. They
predate version 4.2, which reuses received blocks of up to 256 MiB and so no longer faults
in fresh pages on every `put` at those sizes.

## How it works

1. `put()` pickles the object with protocol 5. Large buffers, such as NumPy array data and
   `bytes` or `bytearray` objects of 64 KiB or more, never enter the pickle; everything is copied once,
   from the original objects into one shared-memory block: a block from an earlier,
   received message when one fits, otherwise a new one.
2. Only the block's name, buffer sizes, and a sequence number travel through a
   `multiprocessing.Queue`.
3. `get()` attaches to the block, copies the data into local memory, unpickles it, and
   sends an acknowledgement back.
4. A background thread in the producer makes the block available for reuse when the
   acknowledgement arrives. `capacity` limits how many messages can be outstanding.

Received objects never point into shared memory, so they stay valid after the block is
reused or freed and can be modified freely.

## Usage guide

### Passing endpoints to processes

Create the pair in the parent process and pass the endpoints to workers as `Process`
arguments, the same way as a `multiprocessing.Queue`. Use the same context for the pair
and the processes. A sender can be passed to another process only **before** its first
`put`; after that it belongs to the process that used it.

As with a `multiprocessing.Queue`, keep an endpoint referenced in the creating process until
the processes using the pair have started. With `spawn` and `forkserver` on Linux and macOS,
a starting child reopens the pair's semaphores by name, and the names disappear once the
creating process lets go of them. After that, the creating process may drop endpoints it
does not use, and may create as many further pairs as it needs.

```python
ctx = mp.get_context("spawn")  # "fork" and "forkserver" also work
sender, receiver = create_shared_memory_pair(capacity=4, ctx=ctx)
producer = ctx.Process(target=produce, args=(sender,))
consumer = ctx.Process(target=consume, args=(receiver,))
```

### Capacity and backpressure

`capacity` counts messages that were sent but not yet received. When it is reached,
`put()` blocks, so a fast producer cannot exhaust memory. Shared-memory use is at most
about `capacity × message size`. `capacity=0` or `None` means unbounded.

Received blocks are reused rather than freed, because writing into memory that is
already mapped is much faster than faulting in fresh pages. A block is reused for a
message that fills at least half of it, or for any message that fits a block of up to
1 MiB. The sender keeps at most 8 such blocks and 256 MiB, never more blocks than the
capacity allows, and frees them on `close()`. Blocks over 256 MiB are freed once received.

### Timeouts and non-blocking calls

```python
from queue import Empty, Full

try:
    sender.put(batch, timeout=5)  # Wait up to 5 s for capacity
except Full:
    ...

try:
    item = receiver.get_nowait()  # Same as get(block=False)
except Empty:
    ...
```

### Multiple consumers and threads

Each message is delivered to exactly one consumer. Several consumer processes can share
one receiver, as in [`examples/worker_pool.py`](https://github.com/tobiaswuerth/python_shared_memory_queue/blob/main/examples/worker_pool.py),
and both endpoints are thread-safe. Only one process may produce per pair; for several
producer processes, create one pair per producer.

### Shutting down cleanly

The producer owns every message until it is acknowledged, and `close()` frees unread
messages. Before the producer closes or exits:

```python
with sender:
    for item in items:
        sender.put(item)
    if not sender.wait_for_all_ack(timeout=30):
        raise TimeoutError("Consumer did not receive everything")
```

Closing never blocks. If a `with sender:` block or the producing process ends normally
while messages are still unacknowledged, they are discarded and the sender emits a
`RuntimeWarning`, so forgetting to drain does not lose data silently. An explicit
`close()` or an exception leaving the `with` block discards without a warning.

When both endpoints live in one process, drain before closing either of them. A sentinel
such as `None` is a simple way to tell consumers that the stream has ended.

### Crashes and deadlocks

- **Pass timeouts when a peer can die.** `get`, `put`, and `wait_for_all_ack` without a
  timeout wait forever for a process that no longer exists, as with a
  `multiprocessing.Queue`.
- **A crashed consumer loses at most the message it was handling.** Messages it never
  received go to the next consumer, including one started to replace it. If the crash
  also lost that message's acknowledgement, its slot of capacity stays taken until the
  sender closes; closing still never blocks and frees every block.
- **Killing an idle consumer is safe.** Receivers wait for data without holding the
  queue's read lock, so the other consumers keep receiving.
- **Stop consumers cooperatively when you can,** for example with one sentinel each. A
  consumer killed during the brief moment it reads a message, or on Linux and macOS while
  its acknowledgement is being written, can still leave the pair's queue locked, just as
  it would a `multiprocessing.Queue`.
- **A crashed producer's unread messages** stay readable on Linux and macOS, where a block
  outlives its creator until it is unlinked. On Windows they are freed with the producer,
  and `get` raises `FileNotFoundError` for each of them.

### NumPy arrays

Arrays keep their dtype, shape, and writability. Read-only arrays stay read-only, and
writable ones arrive as writable local copies. C- and Fortran-contiguous arrays keep
their layout and are copied straight into shared memory without an intermediate pickle
copy; other views, such as strided slices, arrive as contiguous copies. Each received
array has its own aligned memory, so keeping one array does not keep the rest of the
message alive.

### Errors

| Exception | Raised when |
| --- | --- |
| `queue.Full` | `put` found no capacity within `timeout` or with `block=False`. |
| `queue.Empty` | `get` found no message within `timeout` or with `block=False`. |
| `BrokenPipeError` | The endpoint is closed, or the producer's acknowledgement channel failed. |
| `FileNotFoundError` | `get` received a message that the producer freed by closing before it was read. |
| `RuntimeError` | A second process tries to produce, or a used sender is passed to another process. |
| Pickling errors | The object cannot be pickled or unpickled. Both endpoints stay usable, and failed messages are still released. |
| `RuntimeWarning` (warning) | A `with sender:` block or the producing process ended normally and discarded unacknowledged messages. |

## API reference

### `create_shared_memory_pair(capacity=0, *, ctx=None)`

Returns a connected `(SharedMemorySender, SharedMemoryReceiver)`. `capacity` limits
outstanding messages (`0` or `None` is unbounded). `ctx` is the multiprocessing context
for the underlying queues; it defaults to `multiprocessing.get_context()`.

### `SharedMemorySender`

| Member | Description |
| --- | --- |
| `put(obj, block=True, timeout=None)` | Copy `obj` into a shared-memory block and send it. |
| `put_nowait(obj)` | `put(obj, block=False)`. |
| `wait_for_all_ack(timeout=None) -> bool` | Wait until every message is received and its block released; `False` on timeout. |
| `close()` | Free unread messages and reusable blocks silently and release resources. Runs automatically at normal process exit. |
| `closed` | Whether the sender is closed. |
| `with sender:` | Closes the sender on exit; warns if a clean exit discards unacknowledged messages. |

### `SharedMemoryReceiver`

| Member | Description |
| --- | --- |
| `get(block=True, timeout=None)` | Return the next object as an independent local copy. |
| `get_nowait()` | `get(block=False)`. |
| `close()` | Flush acknowledgements and close this process's handles. |
| `closed` | Whether the receiver is closed. |
| `with receiver:` | Closes the receiver on exit. |

## Platform notes and troubleshooting

**Docker and Kubernetes:** on Linux, shared memory lives in `/dev/shm`, which containers
limit to 64 MB by default. Raise it to at least `capacity × largest message`, for example
`docker run --shm-size=2g` or `shm_size: 2gb` in Docker Compose. In Kubernetes, mount an
`emptyDir` volume with `medium: Memory` at `/dev/shm`. Otherwise the producer can fail
with `OSError: [Errno 28] No space left on device` or crash with `SIGBUS`.

**Linux:** outside containers, `/dev/shm` usually defaults to half of physical RAM.

**macOS:** fully supported and tested on Apple Silicon. The default start method is
`spawn`, so guard the entry point with `if __name__ == "__main__":`. An unbounded pair
holds at most 32,767 unread messages, the limit of macOS semaphores; beyond that `put`
waits or raises `queue.Full` like a full bounded pair.

**Windows:** shared memory is backed by the page file, and each block disappears once
the last handle closes, so nothing can leak after a crash.

**"resource_tracker: There appear to be N leaked shared_memory objects":** fixed in
3.0.1. Upgrade if you see this warning with the `fork` start method.

**`FileNotFoundError: Shared-memory block was freed before it was received`:** the
producer closed or exited before this message was read. Call `wait_for_all_ack()` before
closing the sender; the sender also warns when this happens.

**`OSError: [Errno 24] Too many open files` with many unread messages:** fixed in 4.1.1.
On Linux and macOS the sender keeps at most 8 blocks mapped for reuse, two file
descriptors each; other unread messages hold none.

**`RuntimeError: A shared-memory pair supports one producing process`:** a second
process called `put`. Create a separate pair for each producer process.

**`Can't pickle local object` or similar:** messages must be picklable. Lambdas, local
functions, open files, and locks cannot be sent.

## Contributing

Bug reports, ideas, and pull requests are welcome. See
[CONTRIBUTING.md](https://github.com/tobiaswuerth/python_shared_memory_queue/blob/main/CONTRIBUTING.md)
for the development setup, tests, and release process, and the
[changelog](https://github.com/tobiaswuerth/python_shared_memory_queue/blob/main/CHANGELOG.md)
for version history. Report security issues privately as described in
[SECURITY.md](https://github.com/tobiaswuerth/python_shared_memory_queue/blob/main/SECURITY.md).

## License

[MIT](https://github.com/tobiaswuerth/python_shared_memory_queue/blob/main/LICENSE) © Tobias Würth
