# Changelog

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
