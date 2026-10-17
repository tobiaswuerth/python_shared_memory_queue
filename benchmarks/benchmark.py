"""Compare real process-to-process throughput with multiprocessing.Queue."""

import argparse
import io
import json
import multiprocessing as mp
import platform
import statistics
import time
from contextlib import redirect_stderr
from datetime import UTC, datetime
from pathlib import Path

from py_sharedmemory import create_shared_memory_pair


def _receive(receiver, count, warmup, control, size):
    try:
        for _ in range(warmup):
            receiver.get(timeout=30)
        control.send("ready")
        if control.recv() != "start":
            raise RuntimeError("Invalid benchmark handshake")
        for _ in range(count):
            payload = receiver.get(timeout=30)
            actual_size = payload.nbytes if hasattr(payload, "nbytes") else len(payload)
            if actual_size != size:
                raise ValueError("Received the wrong payload size")
        control.send("done")
    except Exception as error:
        control.send({"error": f"{type(error).__name__}: {error}"})
    finally:
        receiver.close()
        control.close()


def _expect(control, expected):
    if not control.poll(30):
        raise TimeoutError(f"Benchmark consumer did not signal {expected}")
    message = control.recv()
    if isinstance(message, dict) and "error" in message:
        raise RuntimeError(f"Consumer failed: {message['error']}")
    if message != expected:
        raise RuntimeError("Invalid benchmark handshake")


def measure(transport, payload, count, capacity, ctx):
    """Measure after process startup and warmup, through the last received item."""
    size = payload.nbytes if hasattr(payload, "nbytes") else len(payload)
    if transport == "shared":
        sender, receiver = create_shared_memory_pair(capacity, ctx=ctx)
    else:
        sender = receiver = ctx.Queue(maxsize=capacity)
    control, child_control = ctx.Pipe()
    warmup = min(3, count)
    process = ctx.Process(target=_receive, args=(receiver, count, warmup, child_control, size))
    process.start()
    child_control.close()
    try:
        for _ in range(warmup):
            sender.put(payload, timeout=30)
        _expect(control, "ready")
        if transport == "shared" and not sender.wait_for_all_ack(timeout=30):
            raise TimeoutError("Warmup messages were not acknowledged")
        start = time.perf_counter()
        control.send("start")
        for _ in range(count):
            sender.put(payload, timeout=30)
        _expect(control, "done")
        if transport == "shared" and not sender.wait_for_all_ack(timeout=30):
            raise TimeoutError("Benchmark messages were not acknowledged")
        duration = time.perf_counter() - start
        process.join(10)
        if process.is_alive() or process.exitcode != 0:
            raise RuntimeError("Benchmark consumer failed")
        return duration
    finally:
        if process.is_alive():
            process.terminate()
            process.join(5)
        control.close()
        if transport == "shared":
            sender.close()
            receiver.close()
        else:
            sender.cancel_join_thread()
            sender.close()


def _positive_int(value):
    number = int(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("must be positive")
    return number


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sizes", nargs="+", type=_positive_int, default=[1_024, 2**20, 2**24])
    parser.add_argument(
        "--messages", type=_positive_int, help="default: up to 1,000 or 512 MiB/run"
    )
    parser.add_argument("--capacity", type=_positive_int, default=2)
    parser.add_argument("--repeats", type=_positive_int, default=3)
    parser.add_argument("--payload", choices=["numpy", "bytes"], default="numpy")
    parser.add_argument("--start-method", choices=mp.get_all_start_methods(), default="spawn")
    parser.add_argument("--json", type=Path, help="optional result file")
    args = parser.parse_args(argv)
    if args.payload == "numpy" and any(size % 4 for size in args.sizes):
        parser.error("NumPy sizes must be multiples of 4 for float32 payloads")
    if args.payload == "numpy":
        import numpy as np

    ctx = mp.get_context(args.start_method)
    results = []
    report = {
        "generated_at_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "python": platform.python_version(),
        "platform": platform.platform(),
        "processor": platform.processor(),
        "start_method": args.start_method,
        "capacity": args.capacity,
        "payload": args.payload,
        "numpy": np.__version__ if args.payload == "numpy" else None,
        "repeats": args.repeats,
        "results": results,
    }
    print(f"Payload: {args.payload}; start method: {args.start_method}; capacity: {args.capacity}")
    print(f"{'Bytes':>12}  {'Transport':>9}  {'MiB/s':>10}  {'ms/message':>12}")
    for size in args.sizes:
        count = args.messages or min(1_000, max(1, 2**29 // size))
        payload = np.ones(size // 4, dtype=np.float32) if args.payload == "numpy" else b"x" * size
        for transport in ("standard", "shared"):
            row = {"transport": transport, "size": size, "messages": count}
            durations = []
            diagnostics = io.StringIO()
            try:
                with redirect_stderr(diagnostics):
                    for _ in range(args.repeats):
                        durations.append(measure(transport, payload, count, args.capacity, ctx))
            except Exception as error:
                row.update(status="failed", error=f"{type(error).__name__}: {error}")
                if diagnostics.getvalue():
                    row["stderr"] = diagnostics.getvalue()
                print(f"{size:12,d}  {transport:>9}  FAILED: {row['error']}", flush=True)
            else:
                duration = statistics.median(durations)
                throughput = size * count / duration / 2**20
                latency = duration * 1_000 / count
                row.update(
                    status="ok",
                    median_mib_per_second=throughput,
                    median_ms_per_message=latency,
                )
                print(
                    f"{size:12,d}  {transport:>9}  {throughput:10.1f}  {latency:12.3f}", flush=True
                )
            row["durations_seconds"] = durations
            results.append(row)
            if args.json:
                args.json.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
