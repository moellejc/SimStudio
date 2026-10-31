"""Single-run post-processing: VTK frames (stress in Pa), frames.json, heat-map renders/MP4/GIF, metrics.json."""
import glob
import json
import os
import re
import tempfile
from types import SimpleNamespace

import numpy as np

from . import _env
from ._vendor import compare as cmp
from ._vendor import postprocess as pp

VM_RE, EPSP_RE = pp.FIELD_PRIORITY[0][1], pp.FIELD_PRIORITY[1][1]

BUCKLING_DEFAULTS = dict(kfrac=0.15, track_tol=0.20, sustain=3, f_start=0.10, smooth=5, plateau_frac=0.98)


def _jsonable(o):
    if hasattr(o, "tolist"):
        return o.tolist()
    if hasattr(o, "item"):
        return o.item()
    return str(o)


def _dump(obj, path):
    with open(path, "w") as f:
        json.dump(obj, f, indent=2, default=_jsonable)


def _f(x):
    return None if x is None or (isinstance(x, float) and not np.isfinite(x)) else float(x)


# ------------------------------------------------------------------ buckling
def detect_buckling(F_N, u, reaction_N=None, *, kfrac=0.15, track_tol=0.20, sustain=3, f_start=0.10, smooth=5,
                    plateau_frac=0.98):
    """Force-control instability (wraps compare.detect_instability).

    F_N applied load (N), u loaded-end displacement along the load, reaction_N floor reaction (N) or None, all
    sampled on the same time base. Buckling = first sample (after F > f_start*Fmax) where the tangent stiffness
    dF/du < kfrac * initial stiffness while F still rises, or |R-F|/F > track_tol, held for `sustain` samples.

    plateau_frac: the stiffness criterion is evaluated only up to the first sample where F reaches
    plateau_frac*Fmax. Once a ramp-and-hold load stops rising, dF/du -> 0 trivially (compare.py would flag the
    ramp end as buckling); the reaction-tracking criterion still covers the hold. None = plain compare.py.
    Returns the compare.py dict plus force_N / disp at the detected index (None when not detected)."""
    F = np.asarray(F_N, float)
    uu = None if u is None else np.asarray(u, float)
    R = None if reaction_N is None else np.asarray(reaction_N, float)
    a = SimpleNamespace(kfrac=kfrac, track_tol=track_tol, sustain=sustain, f_start=f_start, smooth=smooth)
    res = cmp.detect_instability(F, uu, R, a)
    if plateau_frac and uu is not None and len(F) and np.max(F) > 0 and res.get("criteria") is not None:
        n_p = int(np.argmax(F >= plateau_frac * np.max(F))) + 1
        if n_p < len(F):
            pre = cmp.detect_instability(F[:n_p], uu[:n_p], None, a) if n_p >= 10 else {"criteria": {}}
            crit = {k: v for k, v in res["criteria"].items() if k != "stiffness_loss"}
            if "stiffness_loss" in pre.get("criteria", {}):
                crit["stiffness_loss"] = pre["criteria"]["stiffness_loss"]
            res["criteria"] = crit
            res["stiffness_window_end_index"] = n_p - 1
            for k in ("detected", "index", "criterion"):
                res.pop(k, None)
            if crit:
                c, i = min(crit.items(), key=lambda kv: kv[1])
                res.update(detected=True, index=int(i), criterion=c)
            else:
                res["detected"] = False
    res["force_N"] = res["disp"] = None
    if res.get("detected"):
        i = res["index"]
        res["force_N"] = float(F[i])
        res["disp"] = float(uu[i]) if uu is not None else None
    return res


# ------------------------------------------------------------------ time history
def _fracture_from_th(df, t):
    """First rise of a part TH 'ERODED' count above its t=0 value (elements already inactive at t=0, e.g. rigid
    caps, are not fracture)."""
    best = None
    for ent, cols in cmp.th_entities(df).items():
        for var, c in cols:
            if not var.upper().startswith("ERODED"):
                continue
            y = df[c].values.astype(float)
            grow = y > y[0]
            if grow.any():
                i = int(np.argmax(grow))
                if best is None or t[i] < best["time_solver"]:
                    best = {"source": f"TH ERODED ({ent})", "time_solver": float(t[i]), "index": i,
                            "eroded_elements": float(y[-1] - y[0])}
    return best


def analyze_time_history(df, deck, *, mode="auto", **buckling_kw):
    """Force/displacement analysis of a th_to_csv DataFrame (columns stripped) with a compare.parse_deck dict.

    Floor reaction = |d(impulse)/dt| of /TH/RWALL FN (T01 stores impulse), converted to N with the /BEGIN units.
    Returns dict: t, mode, applied_N, reaction_N, disp (model length units), disp_source, instability, notes,
    ke_ie_ratio, added_mass_pct, fracture_th."""
    kw = {**BUCKLING_DEFAULTS, **buckling_kw}
    t = df[df.columns[0]].values.astype(float)
    if mode == "auto":
        mode = "force" if (deck["cload"] or deck["pload"]) else "velocity"
    out = {"t": t, "mode": mode, "applied_N": None, "reaction_N": None, "disp": None, "disp_source": None,
           "instability": {"detected": False}, "notes": [], "reaction_source": None}
    R = cmp.extract_reaction(df, deck, None, kw["smooth"])
    if R:
        out["reaction_N"] = R["force"]
        out["reaction_source"] = {k: R[k] for k in ("entity", "source", "impulse_cols")}
    if mode == "force":
        F, dirs, notes = cmp.applied_load(t, deck)
        out["notes"] += notes
        if F is None and R:
            F = R["force"]; out["notes"].append("applied load not computable (/PLOAD?) -> floor reaction used as load")
        u, how = cmp.loaded_node_disp(df, deck, dirs)
        out.update(applied_N=F, disp=u, disp_source=how)
        if F is not None and u is not None:
            out["instability"] = detect_buckling(F, u, R["force"] if R else None, **kw)
        else:
            out["instability"] = {"detected": False, "reason": "missing applied load or loaded-node displacement TH"}
    else:
        u, how = cmp.imposed_displacement(t, df, deck)
        out.update(disp=u, disp_source=how)
        out["instability"] = {"detected": False, "reason": "velocity-controlled run (no applied load): "
                                                            "force-control buckling criteria not applicable"}
    cols = {re.sub(r"\s+", " ", k).upper(): k for k in df.columns}
    out["added_mass_pct"] = float(np.nanmax(np.abs(df[cols["PERCENTAGE ADDED MASS"]].values))) \
        if "PERCENTAGE ADDED MASS" in cols else None
    out["ke_ie_ratio"] = None
    if "KINETIC ENERGY" in cols and "INTERNAL ENERGY" in cols:
        ke = df[cols["KINETIC ENERGY"]].values.astype(float); ie = df[cols["INTERNAL ENERGY"]].values.astype(float)
        ok = ie > 0.05 * max(ie.max(), 1e-30)
        out["ke_ie_ratio"] = float(np.max(ke[ok] / ie[ok])) if ok.any() else None
    out["fracture_th"] = _fracture_from_th(df, t)
    return out


# ------------------------------------------------------------------ frames
def _to_pa(src_vtk, dst_vtk, fac):
    """Copy a VTK frame with every stress array (postprocess.STRESS_RE) multiplied by fac -> Pa."""
    import pyvista as pv
    m = pv.read(src_vtk)
    if fac != 1.0:
        for data in (m.cell_data, m.point_data):
            for k in list(data.keys()):
                if pp.STRESS_RE.search(k):
                    data[k] = np.asarray(data[k], float) * fac
    m.save(dst_vtk, binary=True)
    return pp.frame_time(m)


def _ended_early(listings, t_end, t_reached):
    eng = [x for x in listings if not re.search(r"_0000\.out$", x["file"])]
    normal = all(x["normal_termination"] for x in eng) if eng else None
    reasons = []
    if t_end and t_reached is not None and t_reached < 0.99 * t_end:
        reasons.append(f"reached t={t_reached:g} of /RUN end {t_end:g}")
    if normal is False:
        reasons.append("engine listing has no NORMAL TERMINATION")
    return bool(reasons), normal, "; ".join(reasons) or None


# ------------------------------------------------------------------ main entry
def process_run(run_dir, out_dir, *, ramp_duration=None, cmap="jet", name=None, field=None, fps=8,
                size=(1280, 720), video=True, warp=1.0, mode="auto", gltf=True, gltf_max_frames=None,
                **buckling_kw):
    """Post-process one OpenRadioss run folder into out_dir (run_dir is only read). Returns the metrics dict.

    Writes out_dir/vtk/<name>_NNN.vtk (stress in Pa), vtk/frames.json, frames/frame_NNN.png, <name>.mp4/.gif,
    th/<name>T01.csv, plots/*.png, gltf/<name>.glb + gltf/manifest.json (animated morph-target GLB, see gltf.py),
    summary.json (full postprocess-style summary) and metrics.json."""
    _env.ensure_tools()
    run, out = os.path.abspath(run_dir), os.path.abspath(out_dir)
    if run == out:
        raise ValueError("out_dir must differ from run_dir")
    os.makedirs(out, exist_ok=True)
    name, frames, th, outs = pp.discover(run, name)
    if not name:
        raise FileNotFoundError(f"no OpenRadioss outputs (A00x / T01) found in {run}")
    units = pp.deck_units(run, name)
    deck = cmp.parse_deck(cmp.find_deck(run, name), run, name)
    t_end = pp.solver_end_time(run, name)
    ts, ramp_end = 1.0, None
    if ramp_duration:
        ramp_end = cmp.ramp_end_time(deck)
        if ramp_end:
            ts = ramp_duration / ramp_end
    t_to_s = units["time_to_s"] if units["known"] else 1.0
    to_mm = units["length_to_m"] * 1e3 if units["known"] else 1.0
    stress_unit = "Pa" if units["known"] else "model units"
    a2v, t2c = _env.tool("anim_to_vtk") or pp.find_tool("anim_to_vtk"), _env.tool("th_to_csv") or pp.find_tool("th_to_csv")
    summary = {"run": run, "name": name, "post_dir": out, "anim_files": len(frames), "th_file": th,
               "tools": {"anim_to_vtk": a2v, "th_to_csv": t2c}, "listings": pp.parse_listings(outs), "units": units,
               "solver_end_time": t_end, "time_scale": ts, "ramp_end_time_solver": ramp_end}
    notes = [] if units["known"] else ["/BEGIN units not recognised: values are in model units, not SI"]

    # ---- time history
    tha = None
    if th and t2c:
        csv = pp.convert_th(th, name, os.path.join(out, "th"), t2c)
        if csv:
            tlabel = "equivalent load time (s)" if ts != 1.0 else f"time ({units.get('time') or 'model units'})"
            summary["time_history"] = pp.th_plots(csv, os.path.join(out, "plots"), time_scale=ts, time_label=tlabel)
            import pandas as pd
            df = pd.read_csv(csv); df.columns = [c.strip() for c in df.columns]
            tha = analyze_time_history(df, deck, mode=mode, **buckling_kw)
    elif th:
        notes.append("th_to_csv not found (set OPENRADIOSS_EXEC)")

    def load_at(tt):
        if tha is None:
            return None
        y = tha["applied_N"] if tha["mode"] == "force" and tha["applied_N"] is not None else tha["reaction_N"]
        return None if y is None or tt is None else float(np.interp(tt, tha["t"], y))

    # ---- frames: anim_to_vtk -> stress to Pa -> frames.json -> render
    r = {}
    if frames and a2v:
        vdir = os.path.join(out, "vtk"); os.makedirs(vdir, exist_ok=True)
        for old in glob.glob(os.path.join(vdir, "*.vtk")):
            os.remove(old)
        recs = []
        with tempfile.TemporaryDirectory() as td:
            for src in pp.convert_anim(frames, name, td, a2v):
                dst = os.path.join(vdir, os.path.basename(src))
                t = _to_pa(src, dst, units["stress_to_Pa"] if units["known"] else 1.0)
                recs.append({"file": os.path.basename(dst), "time": t,
                             "load_time": (t * ts if ts != 1.0 else t * t_to_s) if t is not None else None,
                             "load_N": load_at(t)})
        fj = {"stress_unit": stress_unit, "time_unit": units.get("time") or "model units",
              "load_time_unit": "s", "load_time_kind": "equivalent load time (ramp_duration)" if ts != 1.0 else "solver time in s",
              "load_kind": (None if tha is None else "applied load" if tha["mode"] == "force" else "floor reaction"),
              "frames": recs}
        _dump(fj, os.path.join(vdir, "frames.json"))
        ext = pp.external_frames(out)
        if ext:
            W, H = size
            r = pp.render([e["file"] for e in ext], out, name, field, warp, fps, (W - W % 16, H - H % 16), video,
                          cmap=cmap, stress_fac=1.0, stress_unit=stress_unit, ext=ext, time_scale=1.0)
            summary.update(r); summary["vtk_dir"] = vdir; summary["frames_source"] = "anim_to_vtk (stress scaled to Pa)"
            if gltf:
                from .gltf import frames_to_glb, length_scale
                l2m, lknown = length_scale(units)
                man = frames_to_glb([e["file"] for e in ext], os.path.join(out, "gltf", f"{name}.glb"),
                                    times=[x["time"] for x in recs], load_times=[x["load_time"] for x in recs],
                                    loads=[x["load_N"] for x in recs], stress_unit=stress_unit, name=name,
                                    max_frames=gltf_max_frames, length_to_m=l2m, length_known=lknown)
                summary["gltf"] = {k: v for k, v in man.items() if k != "frames"}
    elif frames:
        notes.append("anim_to_vtk not found (set OPENRADIOSS_EXEC)")

    # ---- metrics
    fmax = r.get("field_max", {})
    vm = [v["max_abs"] for k, v in fmax.items() if VM_RE.search(k)]
    ep = [v["max_abs"] for k, v in fmax.items() if EPSP_RE.search(k) and not re.search("upper|lower|layer", k, re.I)]
    t_reached = None
    if tha is not None:
        t_reached = float(tha["t"].max())
    elif r.get("frame_times"):
        t_reached = max(x for x in r["frame_times"] if x is not None)
    early, normal, why = _ended_early(summary["listings"], t_end, t_reached)

    inst = (tha or {}).get("instability") or {"detected": False, "reason": "no time history"}
    bF = bd = bt = None
    if inst.get("detected"):
        i = inst["index"]; bF = inst["force_N"]; bd = inst["disp"] * to_mm; bt = float(tha["t"][i])
    # fracture: TH ERODED (fine), else animation EROSION_STATUS (frame resolution); both skip cells inactive at t=0
    fo = None
    if tha and tha["fracture_th"]:
        fo = dict(tha["fracture_th"]); fo.pop("index")
    elif (r.get("erosion_anim") or {}).get("first_time") is not None:
        ea = r["erosion_anim"]
        fo = {"source": f"animation frame {ea['first_frame']} (frame resolution)", "time_solver": ea["first_time"],
              "eroded_elements": ea["max_eroded_cells"]}
    if fo:
        fo["time_s"] = fo["time_solver"] * t_to_s
        fo["load_time_s"] = fo["time_solver"] * ts if ts != 1.0 else None
        fo["load_N"] = load_at(fo["time_solver"])
        fo["disp_mm"] = float(np.interp(fo["time_solver"], tha["t"], tha["disp"])) * to_mm \
            if tha and tha["disp"] is not None else None
    en = (summary.get("time_history") or {}).get("energy") or {}
    peak = None
    if tha:
        y = tha["applied_N"] if tha["mode"] == "force" and tha["applied_N"] is not None else tha["reaction_N"]
        peak = float(np.max(y)) if y is not None else None
    metrics = {
        "run": run, "name": name, "mode": tha["mode"] if tha else None,
        "units_in": [units["mass"], units["length"], units["time"]], "units_known": units["known"],
        "peak_force_N": peak,
        "peak_force_kind": None if not tha else ("applied load" if tha["mode"] == "force" else "floor reaction"),
        "peak_reaction_N": float(np.max(tha["reaction_N"])) if tha and tha["reaction_N"] is not None else None,
        "buckling_force_N": bF, "disp_at_buckling_mm": bd,
        "buckling_time_s": bt * t_to_s if bt is not None else None,
        "buckling_load_time_s": bt * ts if bt is not None and ts != 1.0 else None,
        "buckling_criterion": inst.get("criterion"), "buckling_reason": inst.get("reason"),
        "max_disp_mm": float(np.nanmax(np.abs(tha["disp"]))) * to_mm if tha and tha["disp"] is not None else None,
        "disp_source": (tha or {}).get("disp_source"),
        "max_von_mises_Pa": max(vm) if vm else None,
        "max_plastic_strain": max(ep) if ep else None,
        "fracture_onset": fo,
        "energy_balance_ok": en.get("balance_ok_5pct"),
        "hourglass_warning": en.get("hourglass_warning"),
        "ke_ie_ratio": (tha or {}).get("ke_ie_ratio"),
        "added_mass_pct": (tha or {}).get("added_mass_pct"),
        "ended_early": early, "ended_early_reason": why, "normal_termination": normal,
        "solver_end_time": t_end, "time_reached": t_reached,
        "frame_count": r.get("frame_count", 0), "time_scale": ts,
        "glb": f"gltf/{name}.glb" if summary.get("gltf") else None,
        "buckling_settings": {**BUCKLING_DEFAULTS, **buckling_kw},
        "notes": notes + ((tha or {}).get("notes") or []),
    }
    metrics = json.loads(json.dumps(metrics, default=_jsonable))
    summary["metrics"] = metrics
    _dump(summary, os.path.join(out, "summary.json"))
    _dump(metrics, os.path.join(out, "metrics.json"))
    return metrics
