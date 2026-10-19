"""Whole programs shaped like real applications, each run as its own process.

Every program must exit with code 0 and print nothing on stderr. That rules out what a
user would see in their terminal when something is wrong: a worker's traceback, a
RuntimeWarning about discarded messages, the resource tracker reporting leaked shared
memory or semaphores, or Windows warning that I/O was still in flight at exit.
"""

import subprocess
import sys
from pathlib import Path

import pytest

PROGRAMS = Path(__file__).parent / "programs"


def run(program, *args):
    result = subprocess.run(
        [sys.executable, str(PROGRAMS / program), *args],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    return result.stdout.splitlines()


@pytest.mark.parametrize("mode", ["complete", "stop-early"])
def test_environment_pool_steps_in_lockstep_and_shuts_down(mode):
    # Lockstep RL environments with control pipes; "stop-early" fails the training loop
    # while workers are blocked in put(), so the shutdown must drain them.
    lines = run("environment_pool.py", mode)
    assert lines[-1] == (
        "ok complete: 90 steps" if mode == "complete" else "ok stop-early: 45 steps"
    )


@pytest.mark.parametrize("mode", ["keep", "close-copies"])
def test_dataset_workers_fill_a_dataset_through_short_polls(mode):
    # Free-running producers with status pipes, collected with 10 ms polls; "close-copies"
    # closes the parent's sender copies right after starting each worker.
    assert run("dataset_workers.py", mode)[-1] == f"ok {mode}: 30 batches"


def test_evaluation_rounds_leave_nothing_behind():
    # Fresh workers and pairs every round of a long training run.
    *rounds, last = run("evaluation_rounds.py")
    assert last == "ok"
    counts = [tuple(map(int, line.split()[2:])) for line in rounds]
    assert len(counts) == 8
    descriptors = [count for count, _ in counts]
    # The first rounds start the resource tracker and import machinery; after that, a
    # round that leaked even one descriptor or handle would grow the count every time.
    assert max(descriptors[2:]) - min(descriptors[2:]) <= 4, descriptors
    assert descriptors[-1] <= descriptors[2] + 2, descriptors
    assert [leftover for _, leftover in counts] == [0] * 8


@pytest.mark.parametrize("mode", ["daemon", "close-wakes"])
def test_background_consumer_thread_ends_with_the_program(mode):
    assert run("background_consumer.py", mode)[-1] == f"ok {mode}: 30 messages"
