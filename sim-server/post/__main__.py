"""CLI: python -m post {process,compare,viewer} ...   (run from sim-server/)"""
import argparse, json, sys
from . import process_run, compare_runs, build_viewer, metrics_to_rows


def main(argv=None):
    p = argparse.ArgumentParser(prog="post")
    sub = p.add_subparsers(dest="cmd", required=True)
    pr = sub.add_parser("process", help="one run -> out dir (vtk, frames.json, video, metrics.json)")
    pr.add_argument("run"); pr.add_argument("--out", required=True)
    pr.add_argument("--no-video", action="store_true"); pr.add_argument("--field")
    pr.add_argument("--no-gltf", action="store_true"); pr.add_argument("--gltf-max-frames", type=int)
    pr.add_argument("--sim-id", help="also print sim_metrics rows for this id")
    c = sub.add_parser("compare", help="several runs side by side (compare.py)")
    c.add_argument("runs", nargs="+"); c.add_argument("--out", required=True)
    c.add_argument("--no-video", action="store_true"); c.add_argument("--mode", default="auto", choices=["auto", "force", "velocity"])
    v = sub.add_parser("viewer", help="standalone three.js HTML (make_viewer.py)")
    v.add_argument("runs", nargs="+"); v.add_argument("--out", required=True)
    v.add_argument("--comparison"); v.add_argument("--max-mb", type=float, default=30.0)
    for sp in (c, v):
        sp.add_argument("--labels", help="comma-separated, one per run (default: folder names)")
    for sp in (pr, c, v):
        sp.add_argument("--ramp-duration", type=float)
    for sp in (pr, c):
        sp.add_argument("--cmap", default="jet")
        sp.add_argument("--kfrac", type=float, default=0.15); sp.add_argument("--track-tol", type=float, default=0.20)
    a = p.parse_args(argv)
    import os
    labs = lambda: a.labels.split(",") if a.labels else [os.path.basename(os.path.normpath(r)) for r in a.runs]
    if a.cmd == "process":
        m = process_run(a.run, a.out, ramp_duration=a.ramp_duration, cmap=a.cmap, field=a.field, video=not a.no_video,
                        gltf=not a.no_gltf, gltf_max_frames=a.gltf_max_frames, kfrac=a.kfrac, track_tol=a.track_tol)
        print(json.dumps(metrics_to_rows(m, a.sim_id) if a.sim_id else m, indent=2))
    elif a.cmd == "compare":
        r = compare_runs(a.runs, labs(), a.out, ramp_duration=a.ramp_duration, cmap=a.cmap, mode=a.mode,
                         video=not a.no_video, kfrac=a.kfrac, track_tol=a.track_tol)
        print(json.dumps([x["table_row"] for x in r["runs"]], indent=2))
    else:
        print(build_viewer(a.runs, labs(), a.out, ramp_duration=a.ramp_duration, comparison=a.comparison, max_mb=a.max_mb))


if __name__ == "__main__":
    sys.exit(main())
