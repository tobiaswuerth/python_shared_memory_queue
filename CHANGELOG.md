# Changelog

## 4.3.0

Faster receives:

- Receivers keep their mappings of blocks the sender reuses instead of mapping each
  message's block again, which faulted in every page anew. Each receiving thread keeps at
  most `capacity` (up to 8) blocks and 256 MiB mapped, and `close()` releases them.
  Messages carry a random identity for each reusable block, so a new block that takes a
  freed name is never read through an old mapping. On GitHub's runners, 1–100 MB messages
  move up to 2.4× faster than with 4.2.1 on Windows, up to 1.7× on macOS, and up to 1.45×
  on Linux.

Fixes:

- Fix producers of different pairs still rejecting each other when the creating process
  kept neither endpoint, for example an orchestrator that wires producer and consumer
  processes and keeps only the processes. 4.2.2 fixed this only while the receiver was
  kept. The pair's owner is now an operating-system lock instead of a slot in
  multiprocessing's shared heap, which was handed to the next pair once released, so no
  two pairs can share an owner however their endpoints are kept.
- Fix a deadlock: killing a consumer while it waited for a message, for example when a
  supervisor scales a worker pool down, starved every other consumer of the pair forever.
  Receivers now wait for data without holding the queue's cross-process read lock and take
  it only to read a message that has arrived.
- A thread blocked in `get()` without a timeout now wakes with `BrokenPipeError` when its
  receiver is closed, instead of waiting forever and keeping the process from exiting.
- A full `/dev/shm`, 64 MiB by default in Docker, no longer kills the producer with
  `SIGBUS`: the sender reserves a new block's pages up front on Linux, so `put` raises
  `OSError` (`ENOSPC`) and the sender stays usable.
- `timeout=math.inf` now waits without limit in `put`, `get`, and `wait_for_all_ack`
  instead of raising `OverflowError`; a NaN timeout raises `ValueError`.
- A block kept mapped costs one file descriptor instead of two on Linux and macOS, and a
  receiver that runs out of descriptors releases its kept mappings and retries.
- When an endpoint in the same process closed the pair, `put` and `get` raise
  `BrokenPipeError` instead of a `ValueError` from inside `multiprocessing`.

Tests and docs:

- Add end-to-end tests of real deployment patterns: orchestrated pipelines, one pair per
  producer, worker pools, two-stage pipelines, request/response loops, threaded producers,
  threads and processes sharing a receiver, backpressure, mixed real-world payloads,
  recycled and crashed consumers, crashed producers, idle and interrupted consumers, many
  pairs under a tight descriptor limit, a full `/dev/shm`, and `ProcessPoolExecutor`
  workers. They wait on events instead of sleeps, and the single-process tests alone
  cover every line.
- Document how long to keep endpoints referenced, how pairs behave when processes crash,
  timeouts, and file-descriptor use.

## 4.2.2

- Fix producers of different pairs rejecting each other with "A shared-memory pair
  supports one producing process". The pair's producer-owner slot lived only as long as
  the parent's sender, which `Process.start()` releases. Once that sender was freed, the
  next pair could reuse the slot, so two pairs shared one owner. The receiver now keeps
  the slot too, so it stays reserved while the creating process holds its receiver, as
  in the documented pattern.

## 4.2.1

- `put` copies large `bytes` and `bytearray` objects (64 KiB and up) straight into shared
  memory, wherever they appear in a message. It used to build the complete pickle in
  memory first: an extra copy of the payload that briefly needed up to 1.5 times its size
  (48 MiB for a 32 MiB `bytearray`). Strided NumPy arrays, which NumPy pickles as `bytes`,
  skip that copy too. The message format is unchanged.
- Resizing a `bytearray` while its message is being pickled, from a `__reduce__` method,
  now raises `BufferError` instead of risking a corrupted message.

## 4.2.0

- Reuse received shared-memory blocks instead of freeing them. Writing into fresh pages
  cost far more than the copy itself, so large `put` calls are now much faster. A block
  is reused for a message that fills at least half of it, or for any message that fits a
  block of up to 1 MiB. The sender keeps at most 8 blocks and 256 MiB for reuse, never
  more blocks than the capacity allows, and frees them on `close()`, so peak memory is
  unchanged. On Linux and macOS at most 8 unread blocks stay mapped (two file
  descriptors each).
- Acknowledgements carry a per-message sequence number, so an unknown or repeated
  acknowledgement can never release a reused block that holds a newer message.
- Fix receivers hanging forever in `close()` or at process exit after reading many
  messages that a closed sender had already freed: such messages are no longer
  acknowledged, so the acknowledgement pipe can no longer fill up.
- Fix a Windows race where a message freed while a receiver attached to it raised a
  confusing `UnpicklingError`; it now raises the usual "freed before it was received"
  `FileNotFoundError`.
- Received NumPy arrays are always aligned. Arrays that followed an odd-sized buffer in the
  same message used to arrive unaligned, which slows NumPy and breaks libraries that need
  aligned memory. Each array now owns its memory, so keeping one no longer keeps the
  whole message alive.

## 4.1.1

- Unread messages no longer hold file descriptors on Linux and macOS: the sender unmaps
  each block right after writing it. Unbounded pairs previously failed with
  `Too many open files` after about 120 unread messages under macOS's default limit.
- Cap outstanding messages at the metadata queue's limit (32,767 on macOS), so a blocking
  `put` on an unbounded pair waits instead of raising `queue.Full`.
- Explain a freed block in the receiver's `FileNotFoundError`, which happens when the
  sender closes before a message is read.

## 4.1.0

- Warn with a `RuntimeWarning` when leaving a `with sender:` block or exiting the
  producing process discards unacknowledged messages, so a forgotten
  `wait_for_all_ack()` no longer loses data silently. Explicit `close()` calls and
  exits caused by exceptions stay silent, and closing still never blocks.
- Collect garbage between tests to avoid Python 3.12 resource-tracker reentrancy errors,
  and add a stress test that checks every byte across consumer processes.

## 4.0.0

- **Breaking:** the import package is renamed from `memory` to `py_sharedmemory` to match the
  distribution name. `memory` clashed with the unrelated `memory` package on PyPI and with
  projects' own `memory` modules. Upgrade by replacing `from memory import ...` with
  `from py_sharedmemory import ...`; the API is unchanged and no alias is kept.
- Rewrite the README as a user guide with an API reference, performance table,
  Docker, macOS, open-file-limit, and troubleshooting notes, and absolute links so
  images render on PyPI.
- Document every public class and method; add NumPy streaming and worker-pool examples.
- Add contributing and security guides, issue forms, and a pull request template.

## 3.0.1

- Fix resource-tracker leaks on Linux and macOS: receivers no longer register blocks they
  attach to, so `fork` workers no longer accumulate tracker entries or print
  "leaked shared_memory" warnings at exit.
- Ship type hints (`py.typed`), checked with strict mypy; expose `memory.__version__`.
- Support and test Python 3.15; test Linux, macOS, and Windows for every Python version.
- Require 100% branch coverage; add pre-commit hooks, Dependabot, and a trusted-publishing
  release workflow.

## 3.0.0

- Python 3.11+, standard-library-only runtime.
- Fix acknowledgement races, capacity leaks, failed-put recovery, and process cleanup.
- Preserve NumPy mutability; remove redundant copies and retained pickle storage.
- Simplify core ownership and sender flow; use immutable metadata and recover from partial startup.
- Add explicit close, context managers, context selection, and timed acknowledgement waits.
- Replace legacy tests and benchmarks; add platform CI and recorded machine results.
