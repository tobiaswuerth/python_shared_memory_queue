"""Plot the recorded benchmark without inventing throughput for failed transfers."""

import argparse
import json
from pathlib import Path

import matplotlib

from .compare import size_label as _size_label

matplotlib.use("Agg")
import matplotlib.pyplot as plt


def plot(report, output):
    plt.rcParams.update({"font.size": 11, "font.family": "DejaVu Sans"})
    shared = {r["size"]: r for r in report["results"] if r["transport"] == "shared"}
    standard = {r["size"]: r for r in report["results"] if r["transport"] == "standard"}
    sizes = sorted(s for s in shared if shared[s]["status"] == standard[s]["status"] == "ok")
    ratios = [
        shared[s]["median_mib_per_second"] / standard[s]["median_mib_per_second"] for s in sizes
    ]
    failures = [
        s for s in shared if shared[s]["status"] == "ok" and standard[s]["status"] == "failed"
    ]
    fig, axes = plt.subplots(
        1,
        2 if failures else 1,
        figsize=(11, 4.5),
        gridspec_kw={"width_ratios": [4, 1]} if failures else None,
        layout="constrained",
        squeeze=False,
    )
    ax = axes[0, 0]
    bars = ax.bar(
        range(len(sizes)),
        ratios,
        width=0.7,
        color=["#0d9488" if ratio >= 1 else "#94a3b8" for ratio in ratios],
    )
    ax.bar_label(bars, labels=[f"{ratio:.2f}×" for ratio in ratios], padding=5, fontsize=10)
    ax.axhline(1, color="#64748b", linestyle="--", linewidth=1.2)
    ax.text(0.01, 1.05, "Standard queue (1×)", transform=ax.get_yaxis_transform(), fontsize=9)
    ax.set_xticks(range(len(sizes)))
    ax.set_xticklabels([_size_label(size) for size in sizes])
    ax.set_ylim(0, max(2.5, max(ratios, default=1) * 1.2))
    ax.set_xlabel("Payload per message")
    ax.set_ylabel("Speedup · higher is better")
    ax.set_title("Transfer speed vs. multiprocessing.Queue", loc="left", fontweight="bold", pad=18)
    ax.grid(axis="y", color="#e2e8f0", linewidth=0.8)
    ax.set_axisbelow(True)
    ax.spines[["top", "right"]].set_visible(False)
    if failures:
        panel = axes[0, 1]
        panel.axis("off")
        size = max(failures)
        seconds = shared[size]["median_ms_per_message"] / 1_000
        panel.text(0.5, 0.86, f"{_size_label(size)} payload", ha="center", fontweight="bold")
        panel.text(0.5, 0.63, f"{seconds:.1f} s", ha="center", fontsize=30, color="#0d9488")
        panel.text(0.5, 0.5, "py-sharedmemory", ha="center")
        panel.text(0.5, 0.25, "mp.Queue failed", ha="center", color="#b91c1c")
    fig.savefig(output, dpi=200, facecolor="white")
    plt.close(fig)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("results", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args(argv)
    plot(json.loads(args.results.read_text(encoding="utf-8")), args.output)


if __name__ == "__main__":
    main()
