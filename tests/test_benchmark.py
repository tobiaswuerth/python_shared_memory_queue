import json
import multiprocessing as mp
import subprocess
import sys
from pathlib import Path

import pytest

import py_sharedmemory
from benchmarks.benchmark import _expect, _receive, main, measure


@pytest.mark.parametrize("transport", ["standard", "shared"])
def test_benchmark_measures_actual_ipc(transport):
    duration = measure(transport, b"x" * 4_096, 3, 2, mp.get_context("spawn"))
    assert 0 < duration < 10


def test_benchmark_reports_consumer_failure():
    ctx = mp.get_context("spawn")
    queue = ctx.Queue()
    parent, child = ctx.Pipe()
    process = ctx.Process(target=_receive, args=(queue, 1, 0, child, 16))
    process.start()
    child.close()
    try:
        _expect(parent, "ready")
        parent.send("start")
        queue.put(b"wrong size")
        with pytest.raises(RuntimeError, match="ValueError: Received the wrong payload size"):
            _expect(parent, "done")
        process.join(5)
        assert not process.is_alive()
        assert process.exitcode == 0
    finally:
        if process.is_alive():
            process.terminate()
            process.join(5)
        parent.close()
        queue.cancel_join_thread()
        queue.close()


def test_benchmark_cli_writes_results(tmp_path):
    result = tmp_path / "results.json"
    process = subprocess.run(
        [
            sys.executable,
            "-m",
            "benchmarks.benchmark",
            "--sizes",
            "1024",
            "--messages",
            "3",
            "--repeats",
            "1",
            "--json",
            str(result),
        ],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert process.returncode == 0, process.stderr
    report = json.loads(result.read_text(encoding="utf-8"))
    assert report["payload"] == "numpy"
    assert {row["transport"] for row in report["results"]} == {"standard", "shared"}
    assert all(row["median_mib_per_second"] > 0 for row in report["results"])
    assert all(row["status"] == "ok" for row in report["results"])


def test_benchmark_records_failure_and_continues(monkeypatch, tmp_path):
    def sometimes_fail(transport, payload, count, capacity, ctx):
        assert count == 1_000
        if transport == "standard":
            print("Queue feeder failed", file=sys.stderr)
            raise OSError("payload too large")
        return 0.01

    monkeypatch.setattr("benchmarks.benchmark.measure", sometimes_fail)
    path = tmp_path / "results.json"
    main(["--sizes", "16", "--repeats", "2", "--json", str(path)])
    failed, success = json.loads(path.read_text(encoding="utf-8"))["results"]
    assert failed["status"] == "failed"
    assert failed["error"] == "OSError: payload too large"
    assert failed["stderr"] == "Queue feeder failed\n"
    assert failed["durations_seconds"] == []
    assert "median_mib_per_second" not in failed
    assert success["status"] == "ok"
    assert success["durations_seconds"] == [0.01, 0.01]


def test_repeats_alternate_the_transport_order(monkeypatch, tmp_path):
    # Shared runners drift; alternating spreads any slowdown over both transports.
    calls = []

    def record(transport, payload, count, capacity, ctx):
        calls.append(transport)
        return 0.01

    monkeypatch.setattr("benchmarks.benchmark.measure", record)
    main(["--sizes", "16", "--repeats", "3", "--json", str(tmp_path / "results.json")])
    assert calls == ["standard", "shared", "shared", "standard", "standard", "shared"]


def test_results_report_the_spread_and_the_platform_label(monkeypatch, tmp_path):
    durations = iter([0.02, 0.02, 0.01, 0.01, 0.04, 0.04])
    monkeypatch.setattr("benchmarks.benchmark.measure", lambda *args: next(durations), raising=True)
    path = tmp_path / "results.json"
    main(["--sizes", "16", "--repeats", "3", "--label", "Linux x64", "--json", str(path)])
    report = json.loads(path.read_text(encoding="utf-8"))
    assert report["label"] == "Linux x64"
    assert report["machine"]
    assert report["py_sharedmemory"] == py_sharedmemory.__version__
    for row in report["results"]:
        assert row["min_mib_per_second"] == pytest.approx(16 * 1_000 / 0.04 / 2**20)
        assert row["median_mib_per_second"] == pytest.approx(16 * 1_000 / 0.02 / 2**20)
        assert row["max_mib_per_second"] == pytest.approx(16 * 1_000 / 0.01 / 2**20)


@pytest.mark.parametrize(
    "args", [["--messages", "0"], ["--capacity", "-1"], ["--sizes", "3", "--payload", "numpy"]]
)
def test_benchmark_rejects_invalid_inputs(args):
    with pytest.raises(SystemExit) as caught:
        main(args)
    assert caught.value.code == 2


def _row(transport, size, status="ok", throughput=100.0):
    row = {"transport": transport, "size": size, "status": status}
    if status == "ok":
        row.update(median_mib_per_second=throughput, median_ms_per_message=8_000.0)
    return row


@pytest.mark.parametrize(
    ("size", "label"),
    [(16, "16 B"), (1_000, "1 kB"), (2_500_000, "2.5 MB"), (10**10, "10 GB")],
)
def test_size_labels(size, label):
    from benchmarks.compare import size_label

    assert size_label(size) == label


def _report(path, label, rows):
    path.write_text(json.dumps({"label": label, "results": rows}), encoding="utf-8")
    return path


def _platform_reports(tmp_path):
    linux = _report(
        tmp_path / "linux.json",
        "Linux x64",
        [
            _row("standard", 1_000, throughput=40),
            _row("shared", 1_000, throughput=10),
            _row("standard", 10**6, throughput=500),
            _row("shared", 10**6, throughput=1_000),
        ],
    )
    macos = _report(
        tmp_path / "macos.json",
        "macOS arm64",
        [
            _row("standard", 1_000, throughput=5),
            _row("shared", 1_000, status="failed"),
            _row("standard", 10**6, throughput=200),
            _row("shared", 10**6, throughput=3_000),
        ],
    )
    return linux, macos


def test_compare_prints_a_speedup_table_in_platform_order(tmp_path, capsys):
    from benchmarks import compare

    compare.main([str(path) for path in _platform_reports(tmp_path)])
    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == "| Platform | 1 kB | 1 MB |"
    assert lines[1] == "| --- | ---: | ---: |"
    assert lines[2] == "| Linux x64 | 0.25× | **2.00×** |"
    assert lines[3] == "| macOS arm64 | failed | **15.00×** |"


def test_compare_draws_the_platform_matrix(tmp_path, capsys):
    pytest.importorskip("matplotlib")
    from benchmarks import compare

    image = tmp_path / "platforms.png"
    reports = [str(path) for path in _platform_reports(tmp_path)]
    compare.main([*reports, "--image", str(image), "--subtitle", "Test runners"])
    assert image.read_bytes().startswith(b"\x89PNG")
    assert "| Platform |" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("background", "ink"),
    [("#fcfcfb", "#0b0b0b"), ("#f0efec", "#0b0b0b"), ("#104281", "#fcfcfb")],
)
def test_matrix_labels_use_the_more_legible_ink(background, ink):
    from benchmarks.compare import label_ink

    assert label_ink(background) == ink
