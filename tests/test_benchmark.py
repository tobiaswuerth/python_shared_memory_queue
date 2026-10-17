import json
import multiprocessing as mp
import subprocess
import sys
from pathlib import Path

import pytest

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


@pytest.mark.parametrize("with_failure", [False, True])
def test_plot_writes_png(tmp_path, with_failure):
    pytest.importorskip("matplotlib")
    from benchmarks import plot

    results = [_row("standard", 16, throughput=50), _row("shared", 16, throughput=25)]
    results += [_row("standard", 10**6), _row("shared", 10**6, throughput=200)]
    if with_failure:
        results += [_row("standard", 10**10, status="failed"), _row("shared", 10**10)]
    report_path = tmp_path / "report.json"
    report_path.write_text(json.dumps({"results": results}), encoding="utf-8")
    image = tmp_path / "plot.png"
    plot.main([str(report_path), str(image)])
    assert image.read_bytes().startswith(b"\x89PNG")


@pytest.mark.parametrize(
    ("size", "label"),
    [(16, "16 B"), (1_000, "1 kB"), (2_500_000, "2.5 MB"), (10**10, "10 GB")],
)
def test_plot_size_labels(size, label):
    pytest.importorskip("matplotlib")
    from benchmarks.plot import _size_label

    assert _size_label(size) == label
