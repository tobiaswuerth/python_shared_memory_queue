# Contributing

Thanks for helping improve py-sharedmemory. Bug reports, documentation fixes, and pull
requests are all welcome.

## Development setup

Python 3.11 or newer is required.

```sh
git clone https://github.com/tobiaswuerth/python_shared_memory_queue.git
cd python_shared_memory_queue
python -m venv .venv
. .venv/bin/activate        # Windows: .venv\Scripts\activate
python -m pip install -e ".[dev]"
pre-commit install
```

## Checks

```sh
python -m pytest --cov        # Tests; 100% branch coverage is required
pre-commit run --all-files    # Ruff lint and format, strict mypy, file and schema checks
basedpyright                  # Strict pyright, with the settings editors read from pyproject
```

The pre-commit hook runs the same checks on every commit. CI runs the tests on Linux,
macOS, and Windows with Python 3.11–3.15, on Linux and Windows ARM64 with 3.11 and 3.15,
and on free-threaded 3.15t, and coverage must stay at 100% on each.

Tests for the `fork` start method only run on Linux and macOS. On Windows, run them
through WSL before changing process handling.

## Pull requests

- Keep changes focused and add a test for every bug fix or new behavior.
- Add a line under a new version heading in [CHANGELOG.md](CHANGELOG.md) for
  user-visible changes.
- Update the README when the API or behavior changes.

## Benchmarks

```sh
python -m pip install -e ".[benchmark]"
python -m benchmarks.benchmark --sizes 1000000 10000000 --repeats 3 --json results.json
python -m benchmarks.plot results.json chart.png
```

`python -m benchmarks.benchmark --help` lists all options. The published chart used
`--sizes 16 100 1000 10000 100000 1000000 10000000 100000000 1000000000 10000000000
--repeats 5`, which needs enough RAM for several copies of a 10 GB payload.

The [benchmark workflow](.github/workflows/benchmark.yml) runs on every release, on demand,
and on pull requests that change the benchmarks. It measures Linux, Windows, and macOS
runners, x86-64 and ARM64, from 1 kB to 100 MB. The run summary shows the speedups, and the
`benchmark-summary` artifact holds the reports and the platform matrix. To redraw it from
downloaded reports:

```sh
python -m benchmarks.compare linux-x64.json linux-arm64.json windows-x64.json \
    windows-arm64.json macos-arm64.json --image platforms.png
```

## Releasing

1. Update `__version__` in `py_sharedmemory/__init__.py` and add the changelog entry.
2. Merge to `main` and wait for CI to pass.
3. Publish a GitHub release tagged `vX.Y.Z` that matches the version.

The [release workflow](.github/workflows/release.yml) builds the package, checks the tag,
publishes to PyPI through trusted publishing, and attaches the files to the release.
