"""Multi-run wrappers around the vendored compare.py and make_viewer.py."""
import json
import os

from . import _env
from ._vendor import compare as cmp
from ._vendor import make_viewer as mv


def _labels(labels, n):
    labels = list(labels)
    if len(labels) != n:
        raise ValueError("labels count must match run_dirs")
    for l in labels:
        if "," in l:
            raise ValueError(f"label {l!r} contains a comma")
    return ",".join(l.strip() for l in labels)


def compare_runs(run_dirs, labels, out_dir, *, ramp_duration=None, cmap="jet", mode="auto", post_root=None,
                 video=True, grid=True, kfrac=0.15, track_tol=0.20, sustain=3, f_start=0.10, smooth=5,
                 stress_scale="per", strain_scale="shared", fps=8, extra_args=()):
    """Side-by-side comparison of runs (same model, different materials). Wraps compare.py.

    Per-run postprocess output goes to post_root/<run basename>/post (default out_dir/runs), so run folders stay
    read-only. Writes out_dir/comparison.{json,md,csv}, force_displacement.png, displacement_time.png,
    force_time.png and (video=True) stress/strain MP4/GIF panels. Returns the parsed comparison.json."""
    _env.ensure_tools()
    out = os.path.abspath(out_dir)
    os.makedirs(out, exist_ok=True)
    argv = [os.path.abspath(r) for r in run_dirs] + [
        "--out", out, "--labels", _labels(labels, len(run_dirs)), "--mode", mode, "--cmap", cmap,
        "--post-root", os.path.abspath(post_root or os.path.join(out, "runs")),
        "--kfrac", repr(kfrac), "--track-tol", repr(track_tol), "--sustain", str(sustain),
        "--f-start", repr(f_start), "--smooth", str(smooth), "--fps", str(fps),
        "--stress-scale", stress_scale, "--strain-scale", strain_scale]
    if ramp_duration:
        argv += ["--ramp-duration", repr(float(ramp_duration))]
    if not video:
        argv += ["--no-video"]
    if not grid:
        argv += ["--no-grid"]
    cmp.main(argv + list(extra_args))
    with open(os.path.join(out, "comparison.json")) as f:
        return json.load(f)


def build_viewer(run_dirs, labels, out_html, *, ramp_duration=None, post_root=None, comparison=None,
                 max_mb=30.0, gzip=True, fps=8.0, title="SimStudio frame viewer", extra_args=()):
    """One standalone offline HTML (three.js r147 inlined from post/assets/three). Wraps make_viewer.py.

    run_dirs may be raw run folders (converted with anim_to_vtk into post_root/<run>/post/vtk, default
    <out_html dir>/runs) or process_run() out_dirs (their vtk/frames.json, already in Pa, is used).
    comparison: a compare_runs comparison.json for buckling markers (default: next to out_html if present)."""
    _env.ensure_tools()
    out = os.path.abspath(out_html)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    argv = [os.path.abspath(r) for r in run_dirs] + [
        "--out", out, "--labels", _labels(labels, len(run_dirs)), "--max-mb", repr(float(max_mb)),
        "--fps", repr(float(fps)), "--title", title,
        "--post-root", os.path.abspath(post_root or os.path.join(os.path.dirname(out), "runs"))]
    if ramp_duration:
        argv += ["--ramp-duration", repr(float(ramp_duration))]
    if comparison:
        argv += ["--comparison", os.path.abspath(comparison)]
    if not gzip:
        argv += ["--no-gzip"]
    mv.main(argv + list(extra_args))
    return out
