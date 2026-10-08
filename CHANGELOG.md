# Changelog

## 3.0.0

- Python 3.11+, standard-library-only runtime.
- Fix acknowledgement races, capacity leaks, failed-put recovery, and process cleanup.
- Preserve NumPy mutability; remove redundant copies and retained pickle storage.
- Simplify core ownership and sender flow; use immutable metadata and recover from partial startup.
- Add explicit close, context managers, context selection, and timed acknowledgement waits.
- Replace legacy tests and benchmarks; add platform CI and recorded machine results.
