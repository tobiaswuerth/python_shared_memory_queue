"""Summarize benchmark reports from several platforms as a speedup table and matrix."""

import argparse
import json
import math
from pathlib import Path
from typing import cast

SURFACE = "#fcfcfb"
INK = "#0b0b0b"
SECONDARY_INK = "#52514e"
# Diverging blue <-> red around a neutral gray at 1x, with lightness falling evenly towards
# both ends: blue cells are faster than multiprocessing.Queue, red cells slower.
DIVERGING = ["#7d1a1a", "#e34948", "#f2a3a2", "#f0efec", "#86b6ef", "#2a78d6", "#104281"]


def size_label(size):
    for divisor, unit in ((10**9, "GB"), (10**6, "MB"), (10**3, "kB")):
        if size >= divisor:
            return f"{size / divisor:g} {unit}"
    return f"{size} B"


def speedups(report):
    """Shared-memory throughput relative to multiprocessing.Queue per size; None if failed."""
    rows = {(row["transport"], row["size"]): row for row in report["results"]}
    result = {}
    for size in sorted({size for _, size in rows}):
        shared, standard = rows.get(("shared", size)), rows.get(("standard", size))
        if shared and standard and shared["status"] == standard["status"] == "ok":
            result[size] = shared["median_mib_per_second"] / standard["median_mib_per_second"]
        else:
            result[size] = None
    return result


def markdown(reports):
    table = [(report["label"], speedups(report)) for report in reports]
    sizes = sorted({size for _, values in table for size in values})
    lines = [
        "| Platform | " + " | ".join(size_label(size) for size in sizes) + " |",
        "| --- |" + " ---: |" * len(sizes),
    ]
    for label, values in table:
        cells = [_cell(values, size) for size in sizes]
        lines.append(f"| {label} | " + " | ".join(cells) + " |")
    return "\n".join(lines)


def _cell(values, size):
    if size not in values:
        return "—"
    if values[size] is None:
        return "failed"
    text = f"{values[size]:.2f}×"
    return f"**{text}**" if values[size] >= 1 else text


def _luminance(color):
    channels = [int(color[index : index + 2], 16) / 255 for index in (1, 3, 5)]
    linear = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def _contrast(first, second):
    lighter, darker = sorted((_luminance(first), _luminance(second)), reverse=True)
    return (lighter + 0.05) / (darker + 0.05)


def label_ink(background):
    """Primary ink or the surface color, whichever contrasts more with the cell."""
    return max((INK, SURFACE), key=lambda ink: _contrast(ink, background))


def plot(reports, output, subtitle=""):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np
    from matplotlib.colors import LinearSegmentedColormap, TwoSlopeNorm, to_hex

    table = [(report["label"], speedups(report)) for report in reports]
    sizes = sorted({size for _, values in table for size in values})
    ratios = np.array(
        [[values.get(size) or math.nan for size in sizes] for _, values in table], dtype=float
    )
    logs = np.log10(ratios)
    limit = max(math.log10(4), float(np.nanmax(np.abs(logs))))
    norm = TwoSlopeNorm(vmin=-limit, vcenter=0, vmax=limit)
    cmap = LinearSegmentedColormap.from_list("speedup", DIVERGING)

    plt.rcParams.update({"font.size": 11, "font.family": "DejaVu Sans"})
    fig, ax = plt.subplots(
        figsize=(1.25 * len(sizes) + 3.4, 0.62 * len(table) + 2.0),
        layout="constrained",
        facecolor=SURFACE,
    )
    ax.set_facecolor(SURFACE)
    mesh = ax.pcolormesh(
        np.ma.masked_invalid(logs), cmap=cmap, norm=norm, edgecolors=SURFACE, linewidth=2
    )
    for row, column in np.ndindex(ratios.shape):
        if math.isnan(ratios[row, column]):
            text, color = ("failed" if sizes[column] in table[row][1] else "—"), SECONDARY_INK
        else:
            value = ratios[row, column]
            text = f"{value:.1f}×" if value >= 10 else f"{value:.2f}×"
            rgba = cast(tuple[float, float, float, float], cmap(norm(logs[row, column])))
            color = label_ink(to_hex(rgba))
        ax.text(column + 0.5, row + 0.5, text, ha="center", va="center", color=color, fontsize=10)

    ax.set_xticks(np.arange(len(sizes)) + 0.5, [size_label(size) for size in sizes])
    ax.set_yticks(np.arange(len(table)) + 0.5, [label for label, _ in table])
    ax.invert_yaxis()
    ax.tick_params(length=0, colors=SECONDARY_INK)
    ax.set_xlabel("Payload per message", color=SECONDARY_INK)
    ax.spines[:].set_visible(False)
    ax.set_title(
        "py-sharedmemory speedup over multiprocessing.Queue",
        loc="left",
        fontweight="bold",
        color=INK,
        pad=24 if subtitle else 10,
    )
    if subtitle:
        ax.text(
            0, 1.02, subtitle, transform=ax.transAxes, color=SECONDARY_INK, fontsize=9, va="bottom"
        )

    ticks = [tick for tick in (0.1, 0.25, 0.5, 1, 2, 4, 10, 25) if abs(math.log10(tick)) <= limit]
    colorbar = fig.colorbar(mesh, ax=ax, fraction=0.035, pad=0.02, ticks=np.log10(ticks))
    colorbar.ax.set_yticklabels([f"{tick:g}×" for tick in ticks], color=SECONDARY_INK, fontsize=9)
    colorbar.ax.tick_params(length=0)
    colorbar.outline.set_visible(False)
    colorbar.set_label("slower  ←  1×  →  faster", color=SECONDARY_INK, fontsize=9)
    fig.savefig(output, dpi=200, facecolor=SURFACE)
    plt.close(fig)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("reports", nargs="+", type=Path, help="benchmark JSON, one per platform")
    parser.add_argument("--image", type=Path, help="optional PNG of the speedup matrix")
    parser.add_argument("--subtitle", default="", help="line under the matrix title")
    args = parser.parse_args(argv)
    reports = []
    for path in args.reports:
        report = json.loads(path.read_text(encoding="utf-8"))
        report["label"] = report.get("label") or path.stem
        reports.append(report)
    print(markdown(reports))
    if args.image:
        plot(reports, args.image, args.subtitle)


if __name__ == "__main__":
    main()
