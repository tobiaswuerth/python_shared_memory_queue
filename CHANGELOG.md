# Changelog

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
