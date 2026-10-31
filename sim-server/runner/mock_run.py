"""Solver stand-in used when binaries are absent or the job is a dry run.

Writes the same kinds of files a short OpenMP run would leave behind: starter
and engine listings, a time-history CSV (rigid-wall impulse still cumulative),
an ASCII VTK frame with von Mises in GPa, and start.ts/end.ts.
"""

from __future__ import annotations

import time
from pathlib import Path

from runner import OPENMP_ENGINE, LaunchResult


def write_mock_outputs(run_dir: str, name: str, threads: int, stop_displacement_mm: float) -> LaunchResult:
    rd = Path(run_dir)
    rd.mkdir(parents=True, exist_ok=True)
    started = time.time()
    (rd / "start.ts").write_text(f"{int(started)}\n")
    listing = _engine_listing(name)
    (rd / f"{name}_0001.out").write_text(listing)
    (rd / f"{name}_0000.out").write_text(
        "TERMINATION WITH WARNING\n              0 ERROR(S)\n              0 WARNING(S)\n"
    )
    (rd / "engine.log").write_text(
        f"mock OpenMP engine {OPENMP_ENGINE} OMP_NUM_THREADS={threads}\n" + listing
    )
    (rd / f"{name}T01.csv").write_text(_time_history_csv(stop_displacement_mm))
    (rd / f"{name}A001").write_text(_vtk_frame())
    ended = time.time()
    (rd / "end.ts").write_text(f"{int(ended)}\n")
    (rd / "watch.log").write_text("mock watch: displacement limit not reached\n")
    return LaunchResult(
        returncode=0,
        engine_bin=OPENMP_ENGINE,
        threads=threads,
        started_at=started,
        ended_at=ended,
        cancelled=False,
        mock=True,
        log_tail=listing[-500:],
        normal_termination=True,
    )


def _engine_listing(name: str) -> str:
    rows = []
    for i, cyc in enumerate(range(0, 201, 20)):
        t = cyc * 1.0e-2
        ie = 0.02 * i
        ke = max(0.0, 0.15 - 0.01 * i)
        err = 0.4 * i
        rows.append(
            f"{cyc:8d} {t:10.4e} {1.2e-4:10.4e} BRICK {1:8d} {err:6.1f}% {ie:10.4f} {ke:10.4f} {0.0:10.4f} {0.0:10.4f}"
        )
    table = "\n".join(rows)
    return (
        f" {name} mock engine listing\n"
        "   CYCLE    TIME      TIME-STEP  ELEMENT          ERROR  I-ENERGY    K-ENERGY T\n"
        f"{table}\n"
        "     NORMAL TERMINATION\n"
        "     TOTAL NUMBER OF CYCLES  :     200\n"
        " ELAPSED TIME     =          0.05 s\n"
    )


def _time_history_csv(stop_mm: float) -> str:
    # Impulse is cumulative (kN*ms). post/ differentiates it to newtons.
    lines = [
        "time,INTERNAL ENERGY,KINETIC ENERGY,EXTERNAL WORK,CONTACT ENERGY,HOURGLASS ENERGY,"
        "PLASTIC WORK,RWALL IMPULSE Z,NODE DZ"
    ]
    for i in range(11):
        t = i * 2.0  # ms
        # Z impulse is negative as the fork loads the wall in -Z.
        # post.wall_force_from_impulse negates d(impulse)/dt, so force comes out positive (N).
        imp = -0.0004 * t * t
        ie = 0.01 * t
        ke = max(0.0, 0.2 - 0.015 * t)
        dz = min(stop_mm * 0.25, 0.15 * t)
        lines.append(f"{t:.6g},{ie:.6g},{ke:.6g},{ie+ke:.6g},0.001,{0.0002*t:.6g},0.002,{imp:.6g},{-dz:.6g}")
    return "\n".join(lines) + "\n"


def _vtk_frame() -> str:
    # One hex (VTK type 12) with cell von Mises in GPa (kg/mm/ms stress).
    return """# vtk DataFile Version 3.0
mock
ASCII
DATASET UNSTRUCTURED_GRID
FIELD FieldData 1
TIME 1 1 double
20
POINTS 8 float
0 0 1
4 0 1
4 2 1
0 2 1
0 0 12
4 0 12
4 2 12
0 2 12
CELLS 1 9
8 0 1 2 3 4 5 6 7
CELL_TYPES 1
12
CELL_DATA 1
SCALARS 3DELEM_Von_Mises float 1
LOOKUP_TABLE default
0.00352
SCALARS 3DELEM_Plastic_Strain float 1
LOOKUP_TABLE default
0.004
SCALARS EROSION_STATUS int 1
LOOKUP_TABLE default
1
POINT_DATA 8
VECTORS Displacement float
0 0 0
0 0 0
0 0 0
0 0 0
0 0 -0.4
0 0 -0.4
0 0 -0.4
0 0 -0.4
"""
