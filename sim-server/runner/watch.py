"""Stop the OpenMP engine once pushed-end travel exceeds a displacement limit.

Usage: watch.py <rundir> <name> <engine_pid> <stop_mm>

Progress is read from <name>T01 after th_to_csv. A missing time-history file is
not fatal; the watcher keeps polling until the engine exits.
"""

from __future__ import annotations

import os
import signal
import sys
import time


def _displacement(run_dir: str, name: str) -> tuple[float, float] | None:
    """Return (time_ms, dz_mm) from a CSV if one can be produced or already exists."""
    csv_path = os.path.join(run_dir, name + "T01.csv")
    t01 = os.path.join(run_dir, name + "T01")
    if not os.path.exists(csv_path) and os.path.exists(t01):
        try:
            from post.th import load

            df = load(t01)
            csv_path = os.path.join(run_dir, name + "T01.csv")
            df.to_csv(csv_path, index=False)
        except Exception:
            return None
    if not os.path.exists(csv_path):
        return None
    try:
        import csv as csvlib

        with open(csv_path, newline="") as fh:
            rows = list(csvlib.DictReader(fh))
        if not rows:
            return None
        last = rows[-1]
        t = float(last.get("time") or last.get("time_sim_ms"))
        # DEF node block is not stable by name; NODE DZ is written by the mock,
        # and post45 used column index 41 on a full th_to_csv header.
        if "NODE DZ" in last:
            return t, float(last["NODE DZ"])
        keys = list(last.keys())
        if len(keys) > 41:
            return t, float(last[keys[41]])
    except Exception:
        return None
    return None


def main(argv: list[str]) -> int:
    run_dir, name, pid_s, stop_s = argv[1], argv[2], int(argv[3]), float(argv[4])
    log_path = os.path.join(run_dir, "watch.log")
    interval = float(os.environ.get("WATCH_INTERVAL_S", "5"))
    with open(log_path, "a") as log:
        while True:
            time.sleep(interval)
            try:
                os.kill(pid_s, 0)
            except OSError:
                log.write("engine exited\n")
                log.flush()
                return 0
            sample = _displacement(run_dir, name)
            if sample is None:
                log.write("no time history yet\n")
                log.flush()
                continue
            t, dz = sample
            log.write(f"{time.strftime('%H:%M:%S')} t={t:.3f} dz={dz:.3f}\n")
            log.flush()
            if abs(dz) > stop_s:
                time.sleep(1)
                os.kill(pid_s, signal.SIGINT)
                log.write(f"SIGINT sent (travel>{stop_s:g} mm)\n")
                log.flush()
                return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
