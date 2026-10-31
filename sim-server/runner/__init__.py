"""Deck generation and OpenRadioss execution.

Public interface (stable):

    generate_decks(spec: DeckSpec) -> DeckFiles
    solver_environment(threads: int) -> dict[str, str]
    launch_engine(run_dir, name, threads, stop_displacement_mm=40.0) -> LaunchResult
    engine_binary_name() -> str

The engine binary is always the OpenMP build `engine_linux64_gf`, launched with
`OMP_NUM_THREADS`. The MPI binary `engine_linux64_gf_ompi` is not used; it fails
in this environment.

`generate_decks` dispatches to:
- runner/gen_deck45.py for mode="load" (fork study, force-controlled, current)
- runner/gen_deck.py for mode="displacement" (original imposed-velocity deck)

`launch_engine` runs starter (`starter_linux64_gf -np 1`) then the OpenMP engine
in `run_dir`, writes start.ts / end.ts, and asks watch.py to stop the engine
once pushed-end travel exceeds the displacement limit. In mock mode it writes
solver-shaped listing, time-history, and animation stand-ins instead.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path

OPENMP_ENGINE = "engine_linux64_gf"
MPI_ENGINE = "engine_linux64_gf_ompi"
STARTER = "starter_linux64_gf"


def engine_binary_name() -> str:
    """OpenMP engine only. Do not switch this to the MPI build."""
    return OPENMP_ENGINE


@dataclass
class DeckSpec:
    name: str
    material: str
    mode: str
    out_dir: str
    mesh_path: str
    load_kN: float = 0.4413
    stop_displacement_mm: float = 40.0
    end_time_ms: float = 120.0
    ramp_ms: float = 100.0
    velocity_mm_ms: float = 0.25
    friction: float = 0.3
    threads: int = 1
    nanim: int = 8
    nth: int = 40
    zshift: float = 0.0
    mock: bool = False


@dataclass
class DeckFiles:
    starter_path: str
    engine_path: str
    run_dir: str
    params: dict = field(default_factory=dict)


@dataclass
class LaunchResult:
    returncode: int
    engine_bin: str
    threads: int
    started_at: float | None
    ended_at: float | None
    cancelled: bool
    mock: bool
    log_tail: str
    normal_termination: bool


def solver_environment(threads: int = 1) -> dict[str, str]:
    """Environment for starter and the OpenMP engine. See runner/env.sh."""
    env = os.environ.copy()
    root = env.get("OPENRADIOSS_PATH") or env.get("SOLVER_ROOT") or ""
    if not root:
        for cand in ("/opt/solver", "/opt/OpenCourant", "/opt/OpenRadioss"):
            if Path(cand, "exec", OPENMP_ENGINE).is_file():
                root = cand
                break
    if root:
        env["OPENRADIOSS_PATH"] = root
        env["RAD_CFG_PATH"] = env.get("RAD_CFG_PATH") or str(Path(root) / "hm_cfg_files")
        env["RAD_H3D_PATH"] = env.get("RAD_H3D_PATH") or str(Path(root) / "extlib" / "h3d" / "lib" / "linux64")
        lib = str(Path(root) / "extlib" / "hm_reader" / "linux64")
        h3d = str(Path(root) / "extlib" / "h3d" / "lib" / "linux64")
        env["LD_LIBRARY_PATH"] = lib + ":" + h3d + ":" + env.get("LD_LIBRARY_PATH", "")
        env["PATH"] = str(Path(root) / "exec") + ":" + env.get("PATH", "")
    env["OMP_NUM_THREADS"] = str(max(1, int(threads)))
    env["OMP_STACKSIZE"] = env.get("OMP_STACKSIZE") or "400m"
    # Never prefer the MPI engine if both names are on PATH.
    env["OPENRADIOSS_ENGINE"] = OPENMP_ENGINE
    return env


def resolve_binary(env: dict[str, str], name: str) -> str | None:
    if name == MPI_ENGINE:
        return None
    root = env.get("OPENRADIOSS_PATH")
    if root:
        path = Path(root) / "exec" / name
        if path.is_file() and os.access(path, os.X_OK):
            return str(path)
    found = shutil.which(name, path=env.get("PATH"))
    if found and MPI_ENGINE not in Path(found).name:
        return found
    return None


def generate_decks(spec: DeckSpec) -> DeckFiles:
    """Write starter (`*_0000.rad`) and engine (`*_0001.rad`) into spec.out_dir/name."""
    if spec.mode not in ("load", "displacement"):
        raise ValueError("mode must be 'load' or 'displacement'")
    if spec.material not in ("steel", "aluminum", "plastic"):
        raise ValueError("material must be steel, aluminum, or plastic")
    if not Path(spec.mesh_path).is_file():
        raise FileNotFoundError(spec.mesh_path)
    if spec.mode == "load":
        from runner.gen_deck45 import generate as gen
    else:
        from runner.gen_deck import generate as gen
    return gen(spec)


def launch_engine(
    run_dir: str,
    name: str,
    threads: int = 1,
    stop_displacement_mm: float = 40.0,
    *,
    mock: bool = False,
    cancel_flag=None,
    log=None,
) -> LaunchResult:
    """Run starter, then OpenMP engine, under a displacement watch.

    mock=True skips the solver and writes stand-in outputs so post/ can run.
    `log(stream, line)` receives starter and engine stdout when the real binary runs.
    """
    if mock or os.environ.get("SIM_MODE", "").lower() == "mock":
        from runner.mock_run import write_mock_outputs

        return write_mock_outputs(run_dir, name, threads, stop_displacement_mm)
    from runner.launch import run_openmp

    return run_openmp(
        run_dir,
        name,
        threads,
        stop_displacement_mm,
        cancel_flag=cancel_flag,
        log=log,
    )
