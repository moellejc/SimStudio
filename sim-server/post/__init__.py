"""Post-processing: animation, time history, and the summary stored in Postgres.

Public interface (stable):

    postprocess_run(run_dir, name=None, *, render=False) -> dict
    compare_runs(run_dirs, out_dir, labels=None) -> dict
    wall_force_from_impulse(time_ms, impulse_kN_ms) -> list[float]
    stress_gpa_to_pa(value) -> float

`postprocess_run` writes `<run>/post/summary.json`. That document is what the
sim server stores on `simulations.summary`. These keys are always present, in SI
units, even when the full VTK render path is unavailable:

    peak_force_n
    max_von_mises_pa
    max_plastic_strain
    energy_balance_error_pct

Unit rules for the fork decks (kg, mm, ms): stress × 1e9 → Pa, force × 1000 → N.
Rigid-wall /TH force is a cumulative impulse and is differentiated before it is
reported (`wall_force_from_impulse`).

The heavy pipeline (anim_to_vtk, frames, mp4/gif) lives in post/postprocess.py
and is used when its imports succeed. Otherwise the lightweight reader below
still produces summary.json from the CSV and VTK the runner left behind.
"""

from __future__ import annotations

import csv
import json
import os
import re
from pathlib import Path

# kg/mm/ms -> SI. stress = kg / (mm * ms^2) = 1e9 Pa. force = kg*mm/ms^2 = 1e3 N.
STRESS_GPA_TO_PA = 1.0e9
FORCE_KN_TO_N = 1000.0

SUMMARY_SCALAR_KEYS = (
    "peak_force_n",
    "max_von_mises_pa",
    "max_plastic_strain",
    "energy_balance_error_pct",
)


def stress_gpa_to_pa(value_gpa: float) -> float:
    """Model von Mises (GPa in kg/mm/ms) to pascals."""
    return float(value_gpa) * STRESS_GPA_TO_PA


def wall_force_from_impulse(time_ms: list[float] | tuple[float, ...], impulse_kN_ms: list[float] | tuple[float, ...]) -> list[float]:
    """Differentiate cumulative rigid-wall impulse into newtons.

    Impulse is kN·ms and time is ms, so d(impulse)/d(time) is kN.
    Multiply by 1000 for newtons. The sign follows post45: compression on the
    wall is reported positive when the stored impulse grows in the -Z sense
    and the column itself is the signed Z impulse (negated here).
    """
    t = [float(x) for x in time_ms]
    imp = [float(x) for x in impulse_kN_ms]
    n = len(t)
    if n == 0:
        return []
    if n == 1:
        return [0.0]
    deriv = [0.0] * n
    for i in range(n):
        if i == 0:
            dt = t[1] - t[0]
            di = imp[1] - imp[0]
        elif i == n - 1:
            dt = t[-1] - t[-2]
            di = imp[-1] - imp[-2]
        else:
            dt = t[i + 1] - t[i - 1]
            di = imp[i + 1] - imp[i - 1]
        slope_kN = di / dt if dt else 0.0
        deriv[i] = -slope_kN * FORCE_KN_TO_N
    return deriv


def postprocess_run(run_dir: str, name: str | None = None, *, render: bool = False) -> dict:
    """Convert a run folder to summary.json (and VTK/CSV/video when tools exist)."""
    run = Path(run_dir)
    name = name or _discover_name(run)
    if name is None:
        raise FileNotFoundError(f"no run outputs in {run_dir}")
    rich = None
    if render or os.environ.get("SIM_POST", "") == "full":
        rich = _try_full_post(run, name)
    summary = rich if rich is not None else _lightweight_summary(run, name)
    summary = normalize_summary(summary)
    post = run / "post"
    post.mkdir(parents=True, exist_ok=True)
    (post / "summary.json").write_text(json.dumps(summary, indent=2))
    return summary


def compare_runs(run_dirs: list[str], out_dir: str, labels: list[str] | None = None) -> dict:
    """Multi-run comparison. Writes comparison.json under out_dir.

    Uses post/compare.py when pyvista is installed. Otherwise builds a compact
    comparison from each run's summary.json (peak force and whether the
    stiffness drop was flagged).
    """
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    try:
        if os.environ.get("SIM_POST") == "full":
            from post.compare import compare_to_dir

            return compare_to_dir(run_dirs, str(out), labels)
    except Exception:
        pass
    rows = []
    for i, rd in enumerate(run_dirs):
        summary = postprocess_run(rd)
        label = labels[i] if labels and i < len(labels) else summary.get("name") or Path(rd).name
        rows.append(
            {
                "label": label,
                "run": rd,
                "peak_force_n": summary.get("peak_force_n"),
                "max_von_mises_pa": summary.get("max_von_mises_pa"),
                "max_plastic_strain": summary.get("max_plastic_strain"),
                "buckling": summary.get("buckling"),
            }
        )
    doc = {
        "runs": rows,
        "note": "Lightweight comparison. Force is wall reaction in newtons after impulse differentiation. Stress is pascals.",
    }
    (out / "comparison.json").write_text(json.dumps(doc, indent=2))
    return doc


def normalize_summary(summary: dict) -> dict:
    """Guarantee the four Postgres scalars exist and are JSON-friendly."""
    out = dict(summary)
    for key in SUMMARY_SCALAR_KEYS:
        if out.get(key) is None:
            out[key] = _fallback_scalar(out, key)
        if out.get(key) is not None:
            out[key] = float(out[key])
    return out


def _fallback_scalar(summary: dict, key: str) -> float | None:
    if key == "max_von_mises_pa":
        raw = summary.get("field_used_max")
        fac = 1.0
        units = summary.get("units") or {}
        if isinstance(units, dict) and units.get("stress_to_Pa"):
            fac = float(units["stress_to_Pa"]) if summary.get("field_in_model_units") else 1.0
        if isinstance(raw, (int, float)):
            return float(raw) * fac
    if key == "energy_balance_error_pct":
        energy = (summary.get("time_history") or {}).get("energy") or {}
        bal = energy.get("balance_incl_contact_hourglass") or energy.get("balance_IE_KE_RKE") or {}
        rel = bal.get("final_rel_error")
        if isinstance(rel, (int, float)):
            return abs(float(rel)) * 100.0
    if key == "max_plastic_strain":
        stats = summary.get("field_max") or {}
        if isinstance(stats, dict):
            for name, val in stats.items():
                if re.search(r"plastic|epsp", str(name), re.I) and isinstance(val, (int, float)):
                    return float(val)
    return None


def _discover_name(run: Path) -> str | None:
    for p in sorted(run.glob("*_0000.rad")):
        return p.name[: -len("_0000.rad")]
    for p in sorted(run.iterdir()) if run.is_dir() else []:
        m = re.match(r"(.+)A\d{3,}$", p.name)
        if m:
            return m.group(1)
        m = re.match(r"(.+)T01(\.csv)?$", p.name)
        if m:
            return m.group(1)
    return None


def _lightweight_summary(run: Path, name: str) -> dict:
    csv_path = _find_csv(run, name)
    series = _read_csv(csv_path) if csv_path else {}
    force = []
    if "time" in series and "rwall_impulse" in series:
        force = wall_force_from_impulse(series["time"], series["rwall_impulse"])
    elif "time" in series and "wall_force_n" in series:
        force = series["wall_force_n"]
    peak_force = max(force) if force else None
    vm_gpa, eps = _vtk_extrema(run, name)
    ie = series.get("internal", [])
    ke = series.get("kinetic", [])
    hg = series.get("hourglass", [])
    we = series.get("external", [])
    balance = None
    if ie and we:
        et = ie[-1] + (ke[-1] if ke else 0) + (hg[-1] if hg else 0)
        ref = max(abs(et), abs(we[-1]), 1e-30)
        balance = abs(et - we[-1]) / ref * 100.0
    buckling = None
    if peak_force is not None and peak_force < 20:
        buckling = {"text": f"no buckling up to {peak_force:.0f} N", "frame": None}
    return {
        "run": str(run),
        "name": name,
        "mock_post": True,
        "units": {
            "mass": "kg",
            "length": "mm",
            "time": "ms",
            "known": True,
            "stress_to_Pa": STRESS_GPA_TO_PA,
            "force_to_N": FORCE_KN_TO_N,
        },
        "peak_force_n": peak_force,
        "max_von_mises_pa": stress_gpa_to_pa(vm_gpa) if vm_gpa is not None else None,
        "max_plastic_strain": eps,
        "energy_balance_error_pct": balance,
        "buckling": buckling,
        "time_history_csv": str(csv_path) if csv_path else None,
        "wall_force_n": force,
        "times_ms": series.get("time"),
        "displacement_mm": series.get("dz"),
    }


def _find_csv(run: Path, name: str) -> Path | None:
    for cand in (run / "post" / "th" / f"{name}T01.csv", run / f"{name}T01.csv", run / f"{name}_force_disp.csv"):
        if cand.is_file():
            return cand
    found = sorted(run.glob("*T01.csv"))
    return found[0] if found else None


def _read_csv(path: Path) -> dict[str, list[float]]:
    with path.open(newline="") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        return {}

    def col(*names: str) -> list[float] | None:
        for n in names:
            for key in rows[0]:
                if key.strip().upper() == n.upper():
                    return [float(r[key]) for r in rows]
        return None

    out: dict[str, list[float]] = {}
    t = col("time", "time_sim_ms")
    if t:
        out["time"] = t
    mapping = {
        "internal": ("INTERNAL ENERGY", "internal_energy_J"),
        "kinetic": ("KINETIC ENERGY", "kinetic_energy_J"),
        "external": ("EXTERNAL WORK", "external_work_J"),
        "hourglass": ("HOURGLASS ENERGY", "hourglass_energy_J"),
        "rwall_impulse": ("RWALL IMPULSE Z",),
        "wall_force_n": ("wall_force_N",),
        "dz": ("NODE DZ", "displacement_mm"),
    }
    for dest, names in mapping.items():
        vals = col(*names)
        if vals is not None:
            out[dest] = vals
    return out


def _vtk_extrema(run: Path, name: str) -> tuple[float | None, float | None]:
    texts = []
    for pat in (f"{name}A001", f"{name}_001.vtk", "*.vtk"):
        texts.extend(run.glob(pat))
        texts.extend((run / "vtk").glob(pat) if (run / "vtk").is_dir() else [])
        texts.extend((run / "post" / "vtk").glob(pat) if (run / "post" / "vtk").is_dir() else [])
    vm = None
    eps = None
    for path in texts:
        try:
            text = path.read_text(errors="ignore")
        except OSError:
            continue
        if "Von_Mises" not in text and "von_mises" not in text.lower() and "3DELEM_Von_Mises" not in text:
            continue
        nums = _scalar_block(text, "3DELEM_Von_Mises") or _scalar_block(text, "von_mises_Pa")
        if nums:
            # von_mises_Pa is already Pa; 3DELEM_Von_Mises from the solver is GPa.
            if "von_mises_Pa" in text and "3DELEM_Von_Mises" not in text:
                vm = max(nums) / STRESS_GPA_TO_PA
            else:
                vm = max(nums)
        en = _scalar_block(text, "3DELEM_Plastic_Strain") or _scalar_block(text, "plastic_strain")
        if en:
            eps = max(en)
        if vm is not None:
            break
    return vm, eps


def _scalar_block(text: str, name: str) -> list[float]:
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if line.startswith("SCALARS") and name in line:
            vals = []
            for nxt in lines[i + 2 :]:
                if not nxt.strip():
                    continue
                if nxt.startswith(("SCALARS", "VECTORS", "CELL_DATA", "POINT_DATA", "FIELD")):
                    break
                for tok in nxt.split():
                    try:
                        vals.append(float(tok))
                    except ValueError:
                        return vals
            return vals
    return []


def _try_full_post(run: Path, name: str) -> dict | None:
    try:
        from post.postprocess import process_run
    except Exception:
        return None
    try:
        return process_run(str(run), name)
    except Exception:
        return None
