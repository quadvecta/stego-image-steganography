"""Repeatable 4K heatmap benchmark; no GUI, network or extra dependencies.

Run: /tmp/bcode/venv/bin/python outputs/benchmark_engine.py --repeats 9
Peak is traced allocation above prebuilt inputs, not process RSS; native OpenCV
allocations may be omitted. Timing and allocation measurements are separate.
"""

import argparse
import gc
import importlib.util
from pathlib import Path
import statistics
import sys
import time
import tracemalloc

import cv2
import numpy as np

import stego_engine as current


def load_original(path):
    spec = importlib.util.spec_from_file_location("benchmark_original", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def measure(label, operation, repeats):
    for _ in range(2):
        operation()
    samples = []
    for _ in range(repeats):
        start = time.perf_counter()
        operation()
        samples.append((time.perf_counter() - start) * 1000)
    gc.collect()
    tracemalloc.start()
    operation()
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    print(f"{label:28s} {statistics.median(samples):10.3f} "
          f"{min(samples):10.3f} {peak / 2**20:12.3f}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--original", type=Path,
                        default=Path(__file__).resolve().parent / "reference/stego_engine.py")
    parser.add_argument("--repeats", type=int, default=9)
    args = parser.parse_args()
    if args.repeats < 1:
        parser.error("--repeats must be positive")
    original = load_original(args.original)
    cv2.setNumThreads(1)
    shape, bin_px, max_dim, seed = (2160, 3840, 3), 61, 560, 20261025
    rng = np.random.default_rng(seed)
    cover = rng.integers(0, 256, shape, dtype=np.uint8)
    print(f"Python {sys.version.split()[0]}, NumPy {np.__version__}, OpenCV {cv2.__version__}")
    print(f"shape={shape}, bin_px={bin_px}, max_dim={max_dim}, overlay=True, seed={seed}")
    print(f"OpenCV threads=1; warmups=2; repeats={args.repeats}; duplicates retained")
    for n in (100_000, 1_000_000):
        positions = rng.integers(0, shape[0] * shape[1], n, dtype=np.int64)
        expected = original.bin_density(original.position_counts(shape, positions), bin_px)
        np.testing.assert_array_equal(
            current.bin_density(current.position_counts(shape, positions), bin_px), expected)
        if hasattr(current, "position_density"):
            np.testing.assert_array_equal(current.position_density(shape, positions, bin_px), expected)
        print(f"\nRecorded positions={n:,}; grid={expected.shape}")
        print(f"{'Operation':28s} {'Median ms':>10s} {'Min ms':>10s} {'Peak MiB':>12s}")
        for name, module in (("original", original), ("current", current)):
            measure(name + " counts + bins", lambda m=module: m.bin_density(
                m.position_counts(shape, positions), bin_px), args.repeats)
            measure(name + " render only", lambda m=module: m.render_heatmap(
                cover, expected, bin_px, max_dim=max_dim), args.repeats)
            measure(name + " full preview", lambda m=module: m.render_heatmap(
                cover, m.bin_density(m.position_counts(shape, positions), bin_px),
                bin_px, max_dim=max_dim), args.repeats)
        if hasattr(current, "position_density"):
            measure("direct density", lambda: current.position_density(
                shape, positions, bin_px), args.repeats)
            measure("direct full preview", lambda: current.render_heatmap(
                cover, current.position_density(shape, positions, bin_px),
                bin_px, max_dim=max_dim), args.repeats)


if __name__ == "__main__":
    main()
