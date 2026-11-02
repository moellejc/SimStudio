"""Run starter, then the OpenMP engine, with a displacement watch.

The MPI engine (`engine_linux64_gf_ompi`) is refused even if it is the only
binary present.
"""

from __future__ import annotations

import signal
import subprocess
import threading
import time
from pathlib import Path

from runner import OPENMP_ENGINE, STARTER, LaunchResult, resolve_binary, solver_environment
from runner.watch import main as watch_main


def run_openmp(
    run_dir: str,
    name: str,
    threads: int,
    stop_displacement_mm: float,
    cancel_flag=None,
    log=None,
) -> LaunchResult:
    env = solver_environment(threads)
    starter = resolve_binary(env, STARTER)
    engine = resolve_binary(env, OPENMP_ENGINE)
    if engine is None or "ompi" in Path(engine).name:
        raise RuntimeError(
            "OpenMP engine engine_linux64_gf was not found. "
            "The MPI build engine_linux64_gf_ompi is not used."
        )
    if starter is None:
        raise RuntimeError("starter_linux64_gf was not found on OPENRADIOSS_PATH")
    rd = Path(run_dir)
    started = time.time()
    (rd / "start.ts").write_text(f"{int(started)}\n")

    def emit(stream: str, line: str) -> None:
        if log:
            log(stream, line)

    emit("starter", f"starting {starter} -np 1")
    starter_rc = _stream([starter, "-i", f"{name}_0000.rad", "-np", "1"], rd, env, "starter", emit, cancel_flag)
    (rd / "starter.exit").write_text(str(starter_rc))
    if _cancelled(cancel_flag):
        return _finish(rd, started, threads, True, starter_rc, False, "cancelled during starter")
    if starter_rc != 0:
        tail = _tail(rd / "starter.log") or _tail(rd / f"{name}_0000.out")
        return _finish(rd, started, threads, False, starter_rc, False, tail or f"starter exited {starter_rc}")

    emit("engine", f"starting {engine} OMP_NUM_THREADS={env['OMP_NUM_THREADS']}")
    proc = subprocess.Popen(
        [engine, "-i", f"{name}_0001.rad"],
        cwd=rd,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    watch = threading.Thread(
        target=watch_main,
        args=([ "watch.py", str(rd), name, str(proc.pid), str(stop_displacement_mm)],),
        daemon=True,
    )
    watch.start()
    assert proc.stdout is not None
    lines: list[str] = []
    for line in proc.stdout:
        line = line.rstrip()
        if not line:
            continue
        lines.append(line)
        emit("engine", line)
        if _cancelled(cancel_flag):
            proc.send_signal(signal.SIGINT)
            break
    rc = proc.wait()
    watch.join(timeout=2)
    listing = _tail(rd / f"{name}_0001.out")
    normal = "NORMAL TERMINATION" in (listing or "") or "NORMAL TERMINATION" in "\n".join(lines)
    tail = "\n".join(lines[-30:])
    if listing:
        tail = (tail + "\n" + listing[-800:]).strip()
    return _finish(rd, started, threads, _cancelled(cancel_flag), rc, normal, tail)


def _stream(cmd, cwd, env, stream, emit, cancel_flag) -> int:
    proc = subprocess.Popen(cmd, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    log_path = Path(cwd) / f"{stream}.log"
    assert proc.stdout is not None
    with log_path.open("w") as fh:
        for line in proc.stdout:
            fh.write(line)
            text = line.rstrip()
            if text:
                emit(stream, text)
            if _cancelled(cancel_flag):
                proc.kill()
                break
    return proc.wait()


def _cancelled(flag) -> bool:
    if flag is None:
        return False
    if isinstance(flag, str):
        return False
    return bool(flag())


def _tail(path: Path) -> str:
    if not path.is_file():
        return ""
    return path.read_text(errors="ignore")[-4000:]


def _finish(rd: Path, started: float, threads: int, cancelled: bool, rc: int, normal: bool, tail: str) -> LaunchResult:
    ended = time.time()
    (rd / "end.ts").write_text(f"{int(ended)}\n")
    return LaunchResult(
        returncode=rc,
        engine_bin=OPENMP_ENGINE,
        threads=threads,
        started_at=started,
        ended_at=ended,
        cancelled=cancelled,
        mock=False,
        log_tail=tail[-1500:],
        normal_termination=normal and not cancelled and rc == 0,
    )
