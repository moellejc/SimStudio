#!/usr/bin/env python3
"""Compare several OpenRadioss/OpenCourant runs side by side (same model, different materials).

Usage:
    source /workspace/openradioss/tools/env.sh
    python /workspace/openradioss/post/compare.py RUN1 RUN2 RUN3 --out OUTDIR
        [--labels "304 stainless,6061-T6 Al,Polystyrene"]
        [--mode auto|force|velocity]          auto: /CLOAD or /PLOAD in deck -> force control
        [--ramp-duration 5 | --display-duration 5 | --time-scale K]   'equivalent load time (s)' axis
        [--cmap jet] [--stress-scale per|shared|both] [--strain-scale per|shared|both] [--no-grid] [--no-video]
        [--own-conversion]                    ignore <run>/vtk/frames.json exports
        [--post-root DIR]                     per-run post output in DIR/<run>/post (run folders untouched)
        force control:    [--kfrac 0.15] [--track-tol 0.2] [--sustain 3] [--f-start 0.10] [--smooth 5]
        velocity control: [--speed V] [--drop 0.10] [--min-frac 0.05] [--force-entity REGEX]

Outputs (OUTDIR):
    stress_per_material.mp4/.gif, strain_shared.mp4/.gif, grid_stress_strain.mp4/.gif (+ *_last.png)
    force_displacement.png (primary), displacement_time.png, force_time.png
    comparison.json, comparison.md, comparison.csv

Frames: if <run>/vtk/frames.json exists (generator export, stress already in Pa) those VTK frames and their
time/load labels are used; otherwise frames come from anim_to_vtk via postprocess.py (<run>/post/vtk).
Force control: applied load F(t) = Fscale * f(t/Ascale) * N_nodes from /CLOAD + /FUNCT (converted to N);
loaded-end displacement u(t) from /TH/NODE (DEF = DX DY DZ VX VY VZ) on a loaded node, along the load
direction. Buckling = first point (after F > f-start*Fmax) where the tangent stiffness dF/du drops below
kfrac * initial stiffness, OR the floor reaction (d(impulse)/dt of /TH/RWALL) stops tracking the applied
load (|R-F|/F > track-tol), sustained for --sustain samples; else "no buckling up to <load reached> N".
Velocity control (old path): buckling = peak reaction before the first >drop load drop.
Stresses in Pa (from /BEGIN units: kg/mm/ms -> GPa -> x1e9). Forces in N.
"""
import argparse, csv, glob, json, os, re, subprocess, sys
os.environ.setdefault("VTK_DEFAULT_OPENGL_WINDOW", "vtkEGLRenderWindow")
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import postprocess as pp  # noqa: E402

VM_RE = pp.FIELD_PRIORITY[0][1]
EPSP_RE = pp.FIELD_PRIORITY[1][1]


def log(*a):
    print("[compare]", *a, flush=True)


# ------------------------------------------------------------------ per-run preparation
def post_dir(run, a):
    return os.path.join(a.post_root, os.path.basename(os.path.normpath(run)), "post") if a.post_root else os.path.join(run, "post")


def ensure_post(run, a):
    post = post_dir(run, a)
    sj = os.path.join(post, "summary.json")
    name, frames, th, outs = pp.discover(run)
    srcs = [f for _, f in frames] + ([th] if th else []) + glob.glob(os.path.join(run, "vtk", "frames.json"))
    newest = max([os.path.getmtime(f) for f in srcs] + [0])
    stale = not os.path.exists(sj) or os.path.getmtime(sj) < newest
    if not stale:
        try:
            stale = "units" not in json.load(open(sj))  # produced by an older postprocess.py
        except Exception:
            stale = True
    if a.reprocess or stale:
        log(f"postprocessing {run}")
        cmd = [sys.executable, os.path.join(HERE, "postprocess.py"), run, "--fps", str(a.fps), "--cmap", a.cmap]
        if a.time_scale_eff != 1.0:
            cmd += ["--time-scale", repr(a.time_scale_eff)]
        if a.own_conversion:
            cmd += ["--own-conversion"]
        if a.post_root:
            cmd += ["--out", post]
        if subprocess.run(cmd).returncode != 0:
            raise SystemExit(f"postprocess.py failed for {run}")
    else:
        log(f"reusing {post}")
    return json.load(open(sj)), name


def find_deck(run, name):
    for pat in (f"{name}_0000.rad", "*_0000.rad", "*.k", "*.key"):
        g = sorted(glob.glob(os.path.join(run, pat)))
        if g:
            return g[0]
    return None


# ------------------------------------------------------------------ deck parsing (best effort)
def rad_blocks(path):
    key, lines = None, []
    for raw in open(path, errors="ignore"):
        l = raw.rstrip("\n")
        if l.startswith("#") or l.startswith("$"):
            continue
        if l.startswith("/"):
            if key:
                yield key, lines
            key, lines = l.strip(), []
        elif key:
            lines.append(l)
    if key:
        yield key, lines


def _num(s, default=0.0):
    try:
        return float(s.strip().replace("D", "E")) if s and s.strip() else default
    except ValueError:
        return default


def rad_floats(line):
    out = []
    for t in line.split():
        try:
            out.append(float(t.replace("D", "E")))
        except ValueError:
            pass
    return out


def _isint(t):
    return bool(re.match(r"^-?\d+$", t))


def parse_deck(deck, run=None, name=None, units_override=None):
    info = {"deck": deck, "units": None, "th": {}, "impvel": [], "cload": [], "pload": [], "functs": {},
            "grnod": {}, "sensors": [], "has_fail": False}
    u = pp.deck_units(run or os.path.dirname(deck or "."), name, deck, override=units_override)
    info["units"] = [u["mass"], u["length"], u["time"]] if u["mass"] else None
    info["force_to_N"] = u["force_to_N"] if u["known"] else None
    info["stress_to_Pa"] = u["stress_to_Pa"] if u["known"] else None
    info["length_unit"] = u["length"] or "model units"
    info["time_unit"] = u["time"] or "model units"
    if not deck:
        return info
    if deck.endswith((".k", ".key")):
        return parse_kfile(deck, info)
    for key, lines in rad_blocks(deck):
        m = re.match(r"/TH/(\w+)/(\d+)", key)
        if m and len(lines) >= 2:
            kind = m.group(1).upper()
            var_tokens, objs = [], []
            for l in lines[1:]:
                toks = l.split()
                if not toks:
                    continue
                if _isint(toks[0]) or objs:
                    objs += [int(toks[0])] if kind == "NODE" else [int(t) for t in toks if _isint(t)]
                else:
                    var_tokens += toks
            info["th"].setdefault(kind, []).append({"id": int(m.group(2)), "title": lines[0].strip(),
                                                    "vars": var_tokens, "objs": objs})
        m = re.match(r"/FUNCT/(\d+)", key)
        if m:
            pts = [p[:2] for p in (rad_floats(l) for l in lines[1:]) if len(p) >= 2]
            if pts:
                info["functs"][int(m.group(1))] = np.array(pts, float)
        m = re.match(r"/GRNOD/NODE/(\d+)", key)
        if m and lines:
            info["grnod"][int(m.group(1))] = [int(t) for l in lines[1:] for t in l.split() if _isint(t)]
        if key.startswith("/FAIL/"):
            info["has_fail"] = True
        if key.startswith("/SENSOR/"):
            info["sensors"].append({"key": key, "title": lines[0].strip() if lines else ""})
        if key.startswith("/IMPVEL") and len(lines) >= 3:
            c1 = lines[1].split(); c2 = rad_floats(lines[2])
            try:
                info["impvel"].append({"fct": int(c1[0]), "dir": c1[1].upper(), "grnd": int(c1[4]) if len(c1) > 4 else 0,
                                       "ascale": (c2[0] or 1.0) if c2 else 1.0, "fscale": (c2[1] or 1.0) if len(c2) > 1 else 1.0,
                                       "tstart": c2[2] if len(c2) > 2 else 0.0, "tstop": c2[3] if len(c2) > 3 else 0.0})
            except (IndexError, ValueError):
                pass
        if key.startswith("/CLOAD") and len(lines) >= 2:
            l = lines[1]
            try:
                if len(l) >= 60:  # fixed fields: fct Dir skew sens grnd Itypfun (10 each) | Ascale(20) | Fscale(20)
                    fld = lambda i, w=10: l[i:i + w]
                    rec = {"fct": int(_num(fld(0))), "dir": fld(10).strip().upper(), "grnd": int(_num(fld(40))),
                           "itypfun": int(_num(fld(50), 1)) or 1, "ascale": _num(fld(60, 20), 1.0) or 1.0,
                           "fscale": _num(fld(80, 20), 1.0) or 1.0}
                else:
                    t = l.split()
                    rec = {"fct": int(t[0]), "dir": t[1].upper(), "grnd": int(t[4]), "itypfun": 1,
                           "ascale": float(t[-2]) if len(t) >= 7 else 1.0, "fscale": float(t[-1]) if len(t) >= 7 else 1.0}
                info["cload"].append(rec)
            except (ValueError, IndexError):
                pass
        if key.startswith("/PLOAD") and len(lines) >= 2:
            info["pload"].append({"raw": lines[1]})
    return info


def _kfields(line):
    if "," in line:
        return [x.strip() for x in line.split(",")]
    return [line[i:i + 10].strip() for i in range(0, max(len(line), 10), 10)]


def parse_kfile(deck, info):
    """Minimal LS-DYNA reader: *DEFINE_CURVE, *BOUNDARY_PRESCRIBED_MOTION (VAD=0), *LOAD_NODE."""
    kw, cards, blocks = None, [], []
    for raw in open(deck, errors="ignore"):
        l = raw.rstrip("\n")
        if l.startswith("$"):
            continue
        if l.startswith("*"):
            if kw:
                blocks.append((kw, cards))
            kw, cards = l.strip().upper(), []
        elif kw:
            cards.append(l)
    if kw:
        blocks.append((kw, cards))
    f = lambda s, d=0.0: float(s) if s not in ("", None) else d
    for kw, cards in blocks:
        try:
            if kw.startswith("*DEFINE_CURVE") and cards:
                c = _kfields(cards[0])
                lcid = int(f(c[0])); sfa = f(c[2] if len(c) > 2 else "", 1.0) or 1.0; sfo = f(c[3] if len(c) > 3 else "", 1.0) or 1.0
                pts = []
                for l in cards[1:]:
                    v = [x for x in (l.split(",") if "," in l else [l[0:20], l[20:40]]) if x.strip()]
                    if len(v) >= 2:
                        pts.append([float(v[0]) * sfa, float(v[1]) * sfo])
                if pts:
                    info["functs"][lcid] = np.array(pts)
            if kw.startswith("*BOUNDARY_PRESCRIBED_MOTION") and cards:
                c = _kfields(cards[0])
                dof, vad, lcid = int(f(c[1])), int(f(c[2])), int(f(c[3]))
                sf = f(c[4] if len(c) > 4 else "", 1.0) or 1.0
                if vad == 0 and abs(dof) in (1, 2, 3):
                    info["impvel"].append({"fct": lcid, "dir": "XYZ"[abs(dof) - 1], "grnd": 0, "ascale": 1.0,
                                           "fscale": sf * (1 if dof > 0 else -1), "tstart": 0.0, "tstop": 0.0})
            if kw.startswith("*LOAD_NODE") and cards:
                for l in cards:
                    c = _kfields(l)
                    dof, lcid, sf = int(f(c[1])), int(f(c[2])), f(c[3] if len(c) > 3 else "", 1.0) or 1.0
                    if dof in (1, 2, 3):
                        info["cload"].append({"fct": lcid, "dir": "XYZ"[dof - 1], "grnd": 0, "itypfun": 1,
                                              "ascale": 1.0, "fscale": sf})
        except (ValueError, IndexError):
            continue
    return info


# ------------------------------------------------------------------ TH helpers
TH_DEF_VARS = {"RWALL": {"DEF": ["FNX", "FNY", "FNZ", "FTX", "FTY", "FTZ"], "FN": ["FNX", "FNY", "FNZ"],
                         "FT": ["FTX", "FTY", "FTZ"]},
               "INTER": {"DEF": ["FNX", "FNY", "FNZ", "FTX", "FTY", "FTZ"]},
               # verified on this OpenCourant build with the demo run (DX,DY = v*t; VX,VY = v)
               "NODE": {"DEF": ["DX", "DY", "DZ", "VX", "VY", "VZ"]}}


def expand_vars(kind, toks):
    out = []
    for t in toks:
        out += TH_DEF_VARS.get(kind, {}).get(t.upper(), [t.upper()])
    return out


def th_entities(df):
    groups = {}
    for c in df.columns[1:]:
        toks = [x for x in re.split(r"\s{2,}", c.strip()) if x]
        if len(toks) >= 2:
            groups.setdefault(" | ".join(toks[:-1]), []).append((toks[-1], c))
    return groups


def smooth(y, w):
    y = np.asarray(y, float)
    if w <= 1 or len(y) < w:
        return y.copy()
    ypad = np.pad(y, (w // 2, w - 1 - w // 2), mode="edge")
    return np.convolve(ypad, np.ones(w) / w, mode="valid")


def eval_funct(F, x):
    return np.interp(x, F[:, 0], F[:, 1], left=F[0, 1], right=F[-1, 1])


# ------------------------------------------------------------------ floor reaction
def extract_reaction(df, deck, force_entity=None, smooth_w=5):
    """Floor/rigid-wall normal force |FN| in N = |d(impulse)/dt| (Radioss T01 stores impulse)."""
    t = df[df.columns[0]].values.astype(float)
    groups = th_entities(df)
    cand = []
    for kind in ("RWALL", "INTER"):
        for blk in deck["th"].get(kind, []):
            vars_ = expand_vars(kind, blk["vars"])
            for ent, cols in groups.items():
                if ent.split(" | ")[0].strip() == blk["title"].strip() or ent.startswith(blk["title"].strip()):
                    names = vars_ if len(vars_) == len(cols) else [v for v, _ in cols]
                    cand.append((ent, cols, names, f"/TH/{kind} '{blk['title']}' vars={vars_}"))
    if not cand:
        for ent, cols in groups.items():
            if re.search(r"rigid ?wall|rwall|floor|wall", ent, re.I):
                cand.append((ent, cols, ["FNX", "FNY", "FNZ", "FTX", "FTY", "FTZ"][:len(cols)],
                             "guessed: first 3 columns of rigid-wall group = FNX,FNY,FNZ"))
    if force_entity:
        cand = [c for c in cand if re.search(force_entity, c[0], re.I)] or \
               [(e, cols, [v for v, _ in cols], "--force-entity, first 3 columns as FNX,FNY,FNZ")
                for e, cols in groups.items() if re.search(force_entity, e, re.I)]
    best, fac = None, deck["force_to_N"] or 1.0
    for ent, cols, names, src in cand:
        idx = [i for i, n in enumerate(names) if n in ("FNX", "FNY", "FNZ")] or list(range(min(3, len(cols))))
        imp = np.column_stack([df[cols[i][1]].values.astype(float) for i in idx])
        if not np.any(imp):
            continue
        comps = np.column_stack([np.gradient(imp[:, j], t) for j in range(imp.shape[1])])
        fmag = np.linalg.norm(comps, axis=1) * fac
        if best is None or fmag.max() > best["force_raw"].max():
            best = {"entity": ent, "source": src, "time": t, "force_raw": fmag, "force": smooth(fmag, smooth_w),
                    "impulse_cols": [cols[i][1].strip() for i in idx]}
    return best


# ------------------------------------------------------------------ force control
def applied_load(t, deck):
    """Applied load magnitude (N) = |vector sum| of /CLOAD x /FUNCT; returns (F, [(dir, unit_component)], notes).
    unit_component is the component of the full-load unit vector, so sum(D_dir * comp) = displacement along load."""
    comps, notes = {}, []
    for c in deck["cload"]:
        F = deck["functs"].get(c["fct"])
        if F is None:
            notes.append(f"/CLOAD fct {c['fct']} not found"); continue
        if c.get("itypfun", 1) != 1:
            notes.append(f"/CLOAD Itypfun={c['itypfun']} (not time-based) skipped"); continue
        if c["dir"] not in ("X", "Y", "Z"):
            notes.append(f"/CLOAD dir {c['dir']} (moment) skipped"); continue
        n = len(deck["grnod"].get(c["grnd"], [])) if c["grnd"] > 0 else 1
        if n == 0:
            notes.append(f"grnod {c['grnd']} is not a /GRNOD/NODE list; assuming 1 node"); n = 1
        comps[c["dir"]] = comps.get(c["dir"], 0) + c["fscale"] * eval_funct(F, t / c["ascale"]) * n
    if not comps:
        return None, None, notes
    M = np.column_stack(list(comps.values()))
    mag = np.linalg.norm(M, axis=1)
    i = int(np.argmax(mag))
    unit = M[i] / mag[i] if mag[i] > 0 else np.ones(len(comps)) / np.sqrt(len(comps))
    return mag * (deck["force_to_N"] or 1.0), [(d, float(u)) for d, u in zip(comps, unit)], notes


def curve_full_load(deck):
    """Peak applied load (N) over the load functions' abscissae (vector sum of all /CLOAD)."""
    ts = [F[:, 0] * c["ascale"] for c in deck["cload"] for F in [deck["functs"].get(c["fct"])] if F is not None]
    if not ts:
        return None
    tt = np.unique(np.concatenate(ts + [np.linspace(0, max(x.max() for x in ts if np.isfinite(x).all()) if all(np.isfinite(x).all() for x in ts) else 1.0, 200)]))
    F, _, _ = applied_load(tt[np.isfinite(tt)], deck)
    return float(F.max()) if F is not None else None


def ramp_end_time(deck):
    """Solver time at which the load (or imposed-velocity) function first reaches its max."""
    for src in (deck["cload"], deck["impvel"]):
        for c in src:
            F = deck["functs"].get(c["fct"])
            if F is not None:
                y = np.abs(F[:, 1]); i = int(np.argmax(y >= 0.999 * y.max()))
                return float(F[i, 0] * c["ascale"])
    return None


def loaded_node_disp(df, deck, load_dirs):
    """Displacement of a /TH/NODE node (prefer one in the loaded group), along the load direction."""
    groups = th_entities(df)
    loaded = set()
    for c in deck["cload"] + deck["impvel"]:
        loaded |= set(deck["grnod"].get(c.get("grnd", 0), []))
    best = None
    for blk in deck["th"].get("NODE", []):
        names = expand_vars("NODE", blk["vars"])
        for nid in blk["objs"]:
            for ent, cols in groups.items():
                if not (ent.startswith(blk["title"].strip()) and str(nid) in re.split(r"[\s|]+", ent)):
                    continue
                nm = names if len(cols) == len(names) else [v for v, _ in cols]
                comp = {n: df[c].values.astype(float) for n, (_, c) in zip(nm, cols) if n in ("DX", "DY", "DZ")}
                if not comp:
                    continue
                score = 2 if nid in loaded else 1
                if best is None or score > best[0]:
                    if load_dirs:
                        d = sum(comp.get("D" + dr, 0) * sg for dr, sg in load_dirs)
                        how = f"/TH/NODE {nid} " + " + ".join(f"{sg:+.3g}*D{dr}" for dr, sg in load_dirs) + " (along load)"
                    else:
                        d = np.linalg.norm(np.column_stack(list(comp.values())), axis=1)
                        how = f"/TH/NODE {nid} |D|"
                    best = (score, np.asarray(d, float), how + ("" if nid in loaded else " [node not in loaded group]"))
    return (best[1], best[2]) if best else (None, "no /TH/NODE with DX/DY/DZ found")


def detect_instability(F, u, R, a):
    """F applied load (N), u loaded-end displacement along load, R floor reaction (N) or None."""
    n = len(F)
    Fmax = float(np.max(F)) if n else 0.0
    if n < 10 or Fmax <= 0 or u is None:
        return {"detected": False, "reason": "insufficient data"}
    Fs, us = smooth(F, a.smooth), smooth(u, a.smooth)
    w = max(2, n // 200)
    dF = np.concatenate([Fs[w:] - Fs[:-w], np.full(w, Fs[-1] - Fs[-1 - w])])
    du = np.concatenate([us[w:] - us[:-w], np.full(w, us[-1] - us[-1 - w])])
    with np.errstate(divide="ignore", invalid="ignore"):
        k = np.where(np.abs(du) > 1e-12 * max(np.abs(us).max(), 1e-30), dF / du, np.inf)
    act = Fs > a.f_start * Fmax
    good = act & np.isfinite(k) & (k > 0)
    win = good & (Fs <= 0.30 * Fmax)
    k0 = float(np.median(k[win])) if win.sum() >= 3 else (float(np.median(k[good])) if good.any() else None)
    res = {"k0_N_per_length_unit": k0, "Fmax_applied_N": Fmax, "criteria": {}, "sample_window": int(w)}

    def first_sustained(mask):
        run = 0
        for i, m in enumerate(mask):
            run = run + 1 if m else 0
            if run >= a.sustain:
                return i - a.sustain + 1
        return None

    if k0:
        # while load still increases, tangent stiffness collapses (incl. negative slope = snap-through)
        i = first_sustained(act & (dF > 0) & (k < a.kfrac * k0))
        if i is not None:
            res["criteria"]["stiffness_loss"] = int(i)
    if R is not None and len(R) == n:
        Rs = smooth(R, a.smooth)
        dev = np.abs(Rs - Fs) / np.maximum(Fs, 1e-30)
        early = act & (Fs <= 0.5 * Fmax)
        res["reaction_tracking_rel_err_early"] = float(np.median(dev[early])) if early.any() else None
        i = first_sustained(act & (dev > a.track_tol))
        if i is not None:
            res["criteria"]["reaction_stops_tracking"] = int(i)
    if res["criteria"]:
        crit, i = min(res["criteria"].items(), key=lambda kv: kv[1])
        res.update(detected=True, index=int(i), criterion=crit)
    else:
        res.update(detected=False)
    return res


# ------------------------------------------------------------------ velocity control (old path)
def detect_buckling_drop(F, drop=0.10, min_frac=0.05):
    if F is None or not len(F) or F.max() <= 0:
        return None
    floor = min_frac * F.max()
    run_max, i_max = -np.inf, 0
    for i, f in enumerate(F):
        if f > run_max:
            run_max, i_max = f, i
        elif run_max > floor and f < (1 - drop) * run_max:
            return {"index": int(i_max), "drop_index": int(i), "detected": True, "criterion": f">{drop:.0%} load drop"}
    return {"index": int(np.argmax(F)), "drop_index": None, "detected": False, "criterion": "peak (no drop)"}


def imposed_displacement(t, df, deck, speed=None):
    if speed is not None:
        return np.abs(speed) * t, f"--speed {speed} * t"
    if deck["impvel"]:
        v = {}
        for iv in deck["impvel"]:
            F = deck["functs"].get(iv["fct"])
            if F is None or iv["dir"] not in "XYZ" or iv["dir"] in v:
                continue
            vel = iv["fscale"] * eval_funct(F, t / (iv["ascale"] or 1.0))
            if iv["tstop"] and iv["tstop"] > 0:
                vel = np.where(t <= iv["tstop"], vel, 0.0)
            v[iv["dir"]] = np.where(t >= iv["tstart"], vel, 0.0)
        if v:
            disp = np.column_stack([np.concatenate([[0.0], np.cumsum(0.5 * (vv[1:] + vv[:-1]) * np.diff(t))])
                                    for vv in v.values()])
            return np.linalg.norm(disp, axis=1), f"integrated imposed velocity in {sorted(v)} from deck"
    d, how = loaded_node_disp(df, deck, None)
    if d is not None:
        return np.abs(d), how
    return None, "unavailable (pass --speed)"


# ------------------------------------------------------------------ rendering
def field_arrays(meshes, rx, fac=1.0):
    out = []
    for m in meshes:
        a, loc, names = pp.merged_field(m, rx)
        out.append((a * fac, loc) if names else (None, None))
    return out


def prep_runs(runs, own_conversion=False):
    import pyvista as pv
    for r in runs:
        ext = None if own_conversion else pp.external_frames(r["run"])
        r["ext"] = ext
        if ext:
            files = [e["file"] for e in ext]; sfac = 1.0  # generator export: stress already in Pa
        else:
            files = sorted(glob.glob(os.path.join(r["post"], "vtk", "*.vtk"))); sfac = r["deck"]["stress_to_Pa"] or 1.0
        r["frame_source"] = "run/vtk/frames.json" if ext else "anim_to_vtk (post/vtk)"
        r["meshes"] = [pv.read(f) for f in files]
        r["vm"] = field_arrays(r["meshes"], VM_RE, sfac)
        r["ep"] = field_arrays(r["meshes"], EPSP_RE)
        r["times"] = [(float(e["time"]) if e.get("time") is not None else pp.frame_time(m))
                      for e, m in zip(ext, r["meshes"])] if ext else [pp.frame_time(m) for m in r["meshes"]]
        for key in ("vm", "ep"):
            vals = [a for a, _ in r[key] if a is not None and a.size]
            r[key + "_max"] = float(max(np.nanmax(a) for a in vals)) if vals else None
            r[key + "_frame_max"] = [float(np.nanmax(a)) if a is not None and a.size else 0.0 for a, _ in r[key]]
        live, allp = set(), set()
        for m, (a, loc) in zip(r["meshes"], r["vm"]):
            if "PART_ID" in m.cell_data.keys():
                pid = np.asarray(m.cell_data["PART_ID"]); allp |= set(np.unique(pid).tolist())
                if a is not None and loc == "cell":
                    live |= set(np.unique(pid[a > 0]).tolist())
        r["ctx_parts"] = sorted(allp - live) if live else []
        # single-part exports (PART_ID all equal): cells with zero stress in every frame are context
        r["ctx_mask"] = None
        ncs = {m.n_cells for m in r["meshes"]}
        if len(ncs) == 1 and not r["ctx_parts"] and all(loc == "cell" for a, loc in r["vm"] if a is not None):
            live_c = np.zeros(ncs.pop(), bool)
            for a, _ in r["vm"]:
                if a is not None:
                    live_c |= np.nan_to_num(a) > 0
            if live_c.any() and (~live_c).any():
                r["ctx_mask"] = ~live_c
        m0 = r["meshes"][0] if r["meshes"] else None
        r["es0"] = np.asarray(m0.cell_data["EROSION_STATUS"]) if m0 is not None and "EROSION_STATUS" in m0.cell_data.keys() else None


def split_mesh(m, arr, loc, ctx_parts, es0=None, ctx_mask=None):
    """Returns (structure, context). es0: EROSION_STATUS of frame 1 (cells inactive from the start, e.g. rigid
    bodies, are not treated as eroded). ctx_mask: cells whose field is zero in every frame (floor/rigid wall)."""
    g = m.copy()
    if arr is not None:
        (g.cell_data if loc == "cell" else g.point_data)["_f"] = arr
    else:
        g.point_data["_f"] = np.zeros(g.n_points)
    n = g.n_cells
    er = np.zeros(n, bool)
    if "EROSION_STATUS" in g.cell_data.keys():  # hide eroded (fractured) elements
        es = np.asarray(g.cell_data["EROSION_STATUS"])
        er = (es == 0) & (es0 != 0) if es0 is not None and es0.shape == es.shape else (es == 0) & es.any()
    ctx = np.zeros(n, bool)
    if ctx_mask is not None and len(ctx_mask) == n:
        ctx = ctx_mask.copy()
    elif ctx_parts and "PART_ID" in g.cell_data.keys():
        ctx = np.isin(np.asarray(g.cell_data["PART_ID"]), ctx_parts)
    main_idx = np.where(~er & ~ctx)[0]
    ctx_idx = np.where(ctx & ~er)[0]
    gm = g.extract_cells(main_idx) if len(main_idx) < n else g
    gc = g.extract_cells(ctx_idx) if len(ctx_idx) else None
    return gm, gc


def common_camera_bounds(runs):
    bs = []
    for r in runs:
        for m, (a, loc) in zip(r["meshes"], r["vm"]):
            g, _ = split_mesh(m, a, loc, r["ctx_parts"], r["es0"], r["ctx_mask"])
            if g.n_points:
                bs.append(g.bounds)
    b = np.array(bs)
    b = [b[:, 0].min(), b[:, 1].max(), b[:, 2].min(), b[:, 3].max(), b[:, 4].min(), b[:, 5].max()]
    pad = 0.15 * max(b[1] - b[0], b[3] - b[2], b[5] - b[4], 1e-12)
    return [b[0] - pad, b[1] + pad, b[2] - pad, b[3] + pad, b[4] - pad, b[5] + pad]


def frame_label(r, i, tscale):
    t = r["times"][i]
    e = r["ext"][i] if r.get("ext") else {}
    if e.get("display_time") is not None:
        s = f"load time = {float(e['display_time']):.3g} s"
    elif t is not None:
        s = f"equivalent load time = {t * tscale:.3g} s" if tscale != 1.0 else f"t = {t:.4g}"
    else:
        s = ""
    if e.get("load_N") is not None:
        s += f"   load = {float(e['load_N']):.0f} N"
    elif r.get("load_at") is not None and t is not None:
        s += f"   load = {r['load_at'](t):.0f} N"
    return s


def render_panels(runs, rows, out_base, nframes, fps, bounds, cmap, tscale, panel_px=(640, 600), view=(30, 20)):
    """rows: list of (key, title, scale, unit); key vm|ep, scale per|shared."""
    import pyvista as pv
    import imageio.v2 as iio
    from PIL import Image
    pv.OFF_SCREEN = True
    ncol, nrow = len(runs), len(rows)
    W, H = panel_px[0] * ncol, panel_px[1] * nrow
    W, H = W - W % 16, H - H % 16
    pl = pv.Plotter(shape=(nrow, ncol), off_screen=True, window_size=(W, H), border=True, border_color="gray")
    pl.set_background("white")
    clims = {}
    for key, _, scale, _u in rows:
        allmax = max((r[key + "_max"] or 0) for r in runs) or 1.0
        for j, r in enumerate(runs):
            clims[(key, scale, j)] = [0.0, allmax if scale == "shared" else (r[key + "_max"] or 1.0)]
    cam, pngs = None, []
    tmpdir = out_base + "_frames"
    os.makedirs(tmpdir, exist_ok=True)
    for i in range(nframes):
        pl.clear()  # clears all subplots and scalar bars
        for ri, (key, title, scale, unit) in enumerate(rows):
            sci = key == "vm"
            fv = (lambda v: f"{v:.3e}") if sci else (lambda v: f"{v:.3g}")
            ttl = f"{title} ({unit})" if unit else title
            for j, r in enumerate(runs):
                pl.subplot(ri, j)
                a_, loc = r[key][i]
                g, ctx = split_mesh(r["meshes"][i], a_, loc, r["ctx_parts"], r["es0"], r["ctx_mask"])
                if ctx is not None:
                    pl.add_mesh(ctx, color="lightgray", opacity=0.25)
                sb_title = f"{ttl} - {r['label']}" if scale == "per" else ttl + " " * (ri + 1)  # unique per bar
                if g.n_cells:
                    pl.add_mesh(g, scalars="_f", cmap=cmap, clim=clims[(key, scale, j)], show_edges=g.n_cells < 4000,
                                edge_color="#444444", line_width=0.3, show_scalar_bar=(scale == "per" or j == ncol - 1),
                                scalar_bar_args=dict(title=sb_title, color="black", vertical=False, position_x=0.08,
                                                     position_y=0.04, width=0.84, height=0.08,
                                                     fmt="%.2e" if sci else "%.3g", title_font_size=12,
                                                     label_font_size=10, n_labels=4))
                pl.add_text(f"{r['label']}\n{title}: max {fv(r[key + '_frame_max'][i])} (run max {fv(r[key + '_max'] or 0)})"
                            f"\n{frame_label(r, i, tscale)}", position="upper_left", font_size=9, color="black")
                if cam is None:
                    pl.reset_camera(bounds=bounds)
                    pl.camera.azimuth = view[0]; pl.camera.elevation = view[1]
                    pl.reset_camera(bounds=bounds)
                    cam = pl.camera.copy()
                else:
                    pl.camera = cam.copy()
        p = os.path.join(tmpdir, f"f_{i + 1:03d}.png")
        pl.screenshot(p)
        pngs.append(p)
    pl.close()
    mp4, gif, last = out_base + ".mp4", out_base + ".gif", out_base + "_last.png"
    with iio.get_writer(mp4, fps=fps, codec="libx264", quality=8, pixelformat="yuv420p", macro_block_size=16) as w:
        for p in pngs:
            w.append_data(iio.imread(p))
    ims = [Image.open(p).convert("RGB") for p in pngs]
    sc = min(1.0, 1200 / ims[0].width)
    ims = [im.resize((int(im.width * sc), int(im.height * sc))).quantize(colors=128, method=Image.Quantize.MEDIANCUT)
           for im in ims]
    ims[0].save(gif, save_all=True, append_images=ims[1:], duration=int(1000 / fps), loop=0, optimize=True)
    os.replace(pngs[-1], last)
    for p in pngs[:-1]:
        os.remove(p)
    os.rmdir(tmpdir)
    return {"mp4": mp4, "gif": gif, "last_frame_png": last,
            "color_limits": {f"{k}/{s}/{runs[j]['label']}": v for (k, s, j), v in clims.items()}}


# ------------------------------------------------------------------ main
def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("runs", nargs="+")
    ap.add_argument("--out", required=True)
    ap.add_argument("--labels", help="comma-separated labels (default: folder name minus fork_/fork45_)")
    ap.add_argument("--mode", choices=["auto", "force", "velocity"], default="auto")
    ap.add_argument("--cmap", default="jet", help="colormap (default jet: blue=low > cyan > green > yellow > red=high)")
    ap.add_argument("--ramp-duration", type=float, help="map the end of the load ramp (from /FUNCT) to this many s")
    ap.add_argument("--display-duration", type=float, help="map solver end time (/RUN) to this many s")
    ap.add_argument("--time-scale", type=float, help="display time = solver time * K")
    ap.add_argument("--stress-scale", choices=["per", "shared", "both"], default="per")
    ap.add_argument("--strain-scale", choices=["per", "shared", "both"], default="shared")
    ap.add_argument("--no-grid", action="store_true")
    ap.add_argument("--no-video", action="store_true")
    ap.add_argument("--own-conversion", action="store_true", help="ignore <run>/vtk/frames.json exports")
    ap.add_argument("--post-root", help="write per-run postprocess output to DIR/<run>/post instead of <run>/post "
                                         "(keeps run folders read-only)")
    ap.add_argument("--fps", type=int, default=8)
    ap.add_argument("--smooth", type=int, default=5, help="moving-average window (TH samples)")
    ap.add_argument("--kfrac", type=float, default=0.15, help="force ctrl: buckling when dF/du < kfrac * initial")
    ap.add_argument("--track-tol", type=float, default=0.20, help="force ctrl: buckling when |R-F|/F > tol")
    ap.add_argument("--sustain", type=int, default=3, help="consecutive TH samples a criterion must hold")
    ap.add_argument("--f-start", type=float, default=0.10, help="ignore loads below f-start * max load")
    ap.add_argument("--speed", type=float, help="velocity ctrl: displacement = speed * t")
    ap.add_argument("--drop", type=float, default=0.10)
    ap.add_argument("--min-frac", type=float, default=0.05)
    ap.add_argument("--force-entity", help="regex selecting the TH entity holding the floor force")
    ap.add_argument("--view", default="30,20", help="camera azimuth,elevation (deg)")
    ap.add_argument("--reprocess", action="store_true")
    a = ap.parse_args(argv)
    out = os.path.abspath(a.out)
    os.makedirs(out, exist_ok=True)
    labels = a.labels.split(",") if a.labels else \
        [re.sub(r"^(fork45_|fork_|t45_)", "", os.path.basename(os.path.normpath(r))) for r in a.runs]
    if len(labels) != len(a.runs):
        raise SystemExit("--labels count must match runs")
    if len(set(labels)) != len(labels):
        labels = [f"{l} #{i + 1}" for i, l in enumerate(labels)]

    # ---- time scaling (resolved from the first run's decks)
    import pandas as pd
    first = os.path.abspath(a.runs[0]); nm0 = pp.discover(first)[0]
    d0 = parse_deck(find_deck(first, nm0), first, nm0)
    ts = 1.0
    if a.time_scale:
        ts = a.time_scale
    elif a.ramp_duration:
        te = ramp_end_time(d0)
        if te:
            ts = a.ramp_duration / te; log(f"load ramp ends at solver t={te:g} {d0['time_unit']} -> shown as {a.ramp_duration:g} s")
        else:
            log("WARNING: --ramp-duration: no load function found; time not scaled")
    elif a.display_duration:
        te = pp.solver_end_time(first, nm0)
        if te:
            ts = a.display_duration / te; log(f"solver end t={te:g} {d0['time_unit']} -> shown as {a.display_duration:g} s")
        else:
            log("WARNING: /RUN end time not found; time not scaled")
    a.time_scale_eff = ts
    scaled = ts != 1.0
    tunit = d0["time_unit"]
    tlab = "equivalent load time (s)" if scaled else f"time ({tunit})"

    runs = []
    for rd, lab in zip(a.runs, labels):
        rd = os.path.abspath(rd)
        summ, name = ensure_post(rd, a)
        deck = parse_deck(find_deck(rd, name), rd, name)
        mode = a.mode if a.mode != "auto" else ("force" if (deck["cload"] or deck["pload"]) else "velocity")
        r = {"quasi_static": {}, "run": rd, "label": lab, "name": name, "post": post_dir(rd, a), "summary": summ, "deck": deck,
             "mode": mode, "notes": [], "t": None, "applied": None, "disp": None, "reaction": None, "inst": {}}
        th = summ.get("time_history")
        if th and th.get("csv") and os.path.exists(th["csv"]):
            df = pd.read_csv(th["csv"]); df.columns = [c.strip() for c in df.columns]
            t = df[df.columns[0]].values.astype(float)
            r["t"] = t
            R = extract_reaction(df, deck, a.force_entity, a.smooth)
            r["reaction"] = R
            cols = {re.sub(r"\s+", " ", k).upper(): k for k in df.columns}
            qs = {}
            if "PERCENTAGE ADDED MASS" in cols:
                qs["added_mass_pct_max"] = float(np.nanmax(np.abs(df[cols["PERCENTAGE ADDED MASS"]].values)))
            if "KINETIC ENERGY" in cols and "INTERNAL ENERGY" in cols:
                ke, ie = df[cols["KINETIC ENERGY"]].values.astype(float), df[cols["INTERNAL ENERGY"]].values.astype(float)
                ok = ie > 0.05 * max(ie.max(), 1e-30)
                qs["ke_over_ie_max"] = float(np.max(ke[ok] / ie[ok])) if ok.any() else None
            r["quasi_static"] = qs
            if mode == "force":
                F, dirs, notes = applied_load(t, deck)
                r["notes"] += notes
                if F is None and R:
                    F = R["force"]; r["notes"].append("applied load not computable (/PLOAD?) -> floor reaction used as load")
                u, how = loaded_node_disp(df, deck, dirs)
                r["applied"], r["disp"], r["disp_source"] = F, u, how
                r["full_load_N"] = curve_full_load(deck)
                if F is not None:
                    r["load_at"] = lambda x, tt=t, FF=F: float(np.interp(x, tt, FF))
                r["inst"] = detect_instability(F, u, R["force"] if R else None, a) if (F is not None and u is not None) \
                    else {"detected": False, "reason": "missing applied load or loaded-node displacement TH"}
            else:
                u, how = imposed_displacement(t, df, deck, a.speed)
                r["disp"], r["disp_source"] = u, how
                r["inst"] = detect_buckling_drop(R["force"], a.drop, a.min_frac) if R else {}
        else:
            r["notes"].append("no time history (T01) -> no force/displacement analysis")
        runs.append(r)

    # ---- animations
    videos, frame_check = {}, {}
    if not a.no_video:
        prep_runs(runs, a.own_conversion)
        nfr = [len(r["meshes"]) for r in runs]
        nframes = min(nfr) if nfr else 0
        frame_check = {"frame_counts": dict(zip(labels, nfr)), "frames_used": nframes,
                       "frame_sources": {r["label"]: r["frame_source"] for r in runs}}
        if len(set(nfr)) > 1:
            log(f"NOTE: frame counts differ {nfr} (e.g. a run stopped early by its sensor); animating first {nframes}")
        if nframes:
            tmat = np.array([[t if t is not None else np.nan for t in r["times"][:nframes]] for r in runs])
            dev = float(np.nanmax(np.abs(tmat - tmat[0]))) if tmat.size else 0.0
            dt_fr = float(np.nanmedian(np.diff(tmat[0]))) if nframes > 1 else 0.0
            frame_check.update(max_frame_time_mismatch=dev, aligned=bool(dev <= 0.05 * dt_fr) if dt_fr else True)
            if not frame_check["aligned"]:
                log(f"WARNING: frame times differ across runs (max mismatch {dev:.3g})")
            bounds = common_camera_bounds(runs)
            view = tuple(float(x) for x in a.view.split(","))
            su = "Pa" if all(r["ext"] or r["deck"]["stress_to_Pa"] for r in runs) else "model units"
            jobs = []
            for key, title, sc, unit in (("vm", "von Mises stress", a.stress_scale, su),
                                         ("ep", "plastic strain", a.strain_scale, "")):
                if all(r[key + "_max"] is None for r in runs):
                    log(f"no {title} field in any run; skipping"); continue
                for s in (["per", "shared"] if sc == "both" else [sc]):
                    jobs.append((f"{'stress' if key == 'vm' else 'strain'}_{'per_material' if s == 'per' else 'shared'}",
                                 [(key, title, s, unit)]))
            if not a.no_grid and all(any(r[k + "_max"] is not None for r in runs) for k in ("vm", "ep")):
                ss = "per" if a.stress_scale in ("per", "both") else "shared"
                es = "shared" if a.strain_scale in ("shared", "both") else "per"
                jobs.append(("grid_stress_strain", [("vm", "von Mises stress", ss, su), ("ep", "plastic strain", es, "")]))
            for nm, rows in jobs:
                log("rendering", nm)
                videos[nm] = render_panels(runs, rows, os.path.join(out, nm), nframes, a.fps, bounds, a.cmap, ts,
                                           panel_px=(640, 560 if len(rows) > 1 else 600), view=view)

    # ---- fracture onset (erosion): TH ERODED (fine) else animation EROSION_STATUS (frame resolution)
    for r in runs:
        er = (r["summary"].get("time_history") or {}).get("erosion_th") or []
        ea = r["summary"].get("erosion_anim") or {}
        fo = None
        if er:
            fo = {"source": f"TH ERODED ({er[0]['entity']})", "time_solver": er[0]["first_time_solver"]}
        elif ea.get("first_time") is not None:
            fo = {"source": f"animation frame {ea['first_frame']} (frame resolution)", "time_solver": ea["first_time"]}
        if fo:
            fo["time_display"] = fo["time_solver"] * ts
            if r["applied"] is not None:
                fo["load_N"] = float(np.interp(fo["time_solver"], r["t"], r["applied"]))
            if r["disp"] is not None:
                fo["disp"] = float(np.interp(fo["time_solver"], r["t"], r["disp"]))
            fo["max_eroded_cells"] = ea.get("max_eroded_cells")
        r["fracture"] = fo

    # ---- plots
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    lu = runs[0]["deck"]["length_unit"]
    plots = {}

    def ycurve(r):
        if r["mode"] == "force" and r["applied"] is not None:
            return r["applied"]
        return r["reaction"]["force"] if r["reaction"] else None

    def marks(ax, r, x, y, c):
        inst = r["inst"] or {}
        if inst.get("index") is not None and (inst.get("detected") or r["mode"] == "velocity"):
            i = inst["index"]
            ax.plot(x[i], y[i], "o", ms=11, mfc="none", mec=c, mew=2.2, zorder=5)
            ax.annotate((f"buckling {y[i]:.4g} N" if inst.get("detected") else f"peak {y[i]:.4g} N")
                        if y is ycurve(r) else "buckling", (x[i], y[i]), textcoords="offset points", xytext=(8, -14),
                        color=c, fontsize=9)
        fo = r.get("fracture")
        if fo:
            xi = np.interp(fo["time_solver"], r["t"], x); yi = np.interp(fo["time_solver"], r["t"], y)
            ax.plot(xi, yi, "X", ms=10, color=c, zorder=6)
            ax.annotate("fracture onset", (xi, yi), textcoords="offset points", xytext=(8, 6), color=c, fontsize=8)

    # primary: force vs displacement
    fig, ax = plt.subplots(figsize=(10, 6.5)); drew = False
    for k, r in enumerate(runs):
        c, y = f"C{k}", ycurve(r)
        if r["disp"] is None or y is None:
            continue
        if r["mode"] == "force":
            ax.plot(r["disp"], y, color=c, lw=2, label=f"{r['label']} - applied load")
            if r["reaction"]:
                ax.plot(r["disp"], r["reaction"]["force"], color=c, lw=0.9, ls="--", alpha=0.7, label=f"{r['label']} - floor reaction")
        else:
            ax.plot(r["disp"], r["reaction"]["force_raw"], color=c, alpha=0.25, lw=0.8)
            ax.plot(r["disp"], y, color=c, lw=1.8, label=f"{r['label']} - floor reaction")
        marks(ax, r, r["disp"], y, c); drew = True
    if drew:
        full = [r.get("full_load_N") for r in runs if r.get("full_load_N")]
        if full:
            ax.axhline(max(full), color="gray", ls=":", lw=1)
            ax.annotate(f"full load {max(full):.0f} N", (0, max(full)), xycoords=("axes fraction", "data"),
                        textcoords="offset points", xytext=(4, 3), color="gray", fontsize=8)
        ax.set_xlabel(f"loaded-end displacement along load ({lu})"); ax.set_ylabel("force (N)")
        ax.set_title("Force vs. displacement   (o = buckling / instability, X = fracture onset)", fontsize=11)
        ax.grid(alpha=.3); ax.legend(fontsize=8)
        p = os.path.join(out, "force_displacement.png"); fig.tight_layout(); fig.savefig(p, dpi=130); plots["force_displacement"] = p
    plt.close(fig)
    # displacement vs time
    fig, ax = plt.subplots(figsize=(10, 5.5)); drew = False
    for k, r in enumerate(runs):
        if r["disp"] is None:
            continue
        c = f"C{k}"; x = r["t"] * ts
        ax.plot(x, r["disp"], color=c, lw=1.8, label=r["label"]); marks(ax, r, x, r["disp"], c); drew = True
    if drew:
        ax.set_xlabel(tlab); ax.set_ylabel(f"loaded-end displacement ({lu})"); ax.grid(alpha=.3); ax.legend()
        ax.set_title("Displacement vs. time   (o = buckling, X = fracture onset)", fontsize=11)
        p = os.path.join(out, "displacement_time.png"); fig.tight_layout(); fig.savefig(p, dpi=120); plots["displacement_time"] = p
    plt.close(fig)
    # force vs time
    fig, ax = plt.subplots(figsize=(10, 5.5)); drew = False
    for k, r in enumerate(runs):
        if r["t"] is None:
            continue
        c = f"C{k}"; x = r["t"] * ts
        if r["applied"] is not None:
            ax.plot(x, r["applied"], color=c, lw=2, label=f"{r['label']} - applied"); drew = True
        if r["reaction"]:
            ax.plot(x, r["reaction"]["force"], color=c, lw=1, ls="--", label=f"{r['label']} - floor reaction"); drew = True
        y = ycurve(r)
        if y is not None:
            marks(ax, r, x, y, c)
    if drew:
        ax.set_xlabel(tlab); ax.set_ylabel("force (N)"); ax.grid(alpha=.3); ax.legend(fontsize=8)
        ax.set_title("Applied load and floor reaction vs. time", fontsize=11)
        p = os.path.join(out, "force_time.png"); fig.tight_layout(); fig.savefig(p, dpi=120); plots["force_time"] = p
    plt.close(fig)

    # ---- table + json
    tkey = "time_at_buckling_s_equiv" if scaled else f"time_at_buckling_{tunit}"
    rows, js_runs = [], []
    for r in runs:
        en = (r["summary"].get("time_history") or {}).get("energy") or {}
        lst = [x for x in r["summary"].get("listings", []) if "listing_energy_error_pct_final" in x]
        bal = en.get("balance_ok_5pct")
        status = ("OK" if bal else "CHECK") if bal is not None else "n/a"
        if en.get("hourglass_warning"):
            status += " (hourglass>10%)"
        if lst:
            status += f" (listing err {lst[-1]['listing_energy_error_pct_final']}%)"
        vmax = r.get("vm_max")
        if vmax is None:  # no-video path: postprocess summary is already in Pa when units are known
            vals = [v["max_abs"] for k, v in r["summary"].get("field_max", {}).items() if VM_RE.search(k)]
            vmax = max(vals) if vals else None
        emax = r.get("ep_max")
        if emax is None:
            vals = [v["max_abs"] for k, v in r["summary"].get("field_max", {}).items()
                    if EPSP_RE.search(k) and not re.search("upper|lower|layer", k, re.I)]
            emax = max(vals) if vals else None
        inst, y = r["inst"] or {}, ycurve(r)
        bF = bt = bd = None
        if inst.get("index") is not None and y is not None:
            i = inst["index"]; bF = float(y[i]); bt = float(r["t"][i] * ts)
            bd = float(r["disp"][i]) if r["disp"] is not None else None
        maxd = float(np.nanmax(r["disp"])) if r["disp"] is not None else None
        Fend = float(np.max(r["applied"])) if r["applied"] is not None else None
        full = r.get("full_load_N")
        if r["mode"] == "force":
            if inst.get("detected"):
                btxt = f"{bF:.4g}"
            elif Fend is not None:
                btxt = f"no buckling up to {Fend:.0f} N" + (f" (data ends before full {full:.0f} N)" if full and Fend < 0.99 * full else "")
            else:
                btxt = "n/a (" + inst.get("reason", "?") + ")"
        else:
            btxt = (f"{bF:.4g}" + ("" if inst.get("detected") else " (peak; no drop)")) if bF is not None else "n/a"
        fo = r["fracture"]
        ftxt = "none"
        if fo:
            ftxt = f"{fo['time_display']:.3g} s" if scaled else f"t={fo['time_solver']:.4g} {tunit}"
            if fo.get("load_N") is not None:
                ftxt += f" @ {fo['load_N']:.0f} N"
        rows.append({"material": r["label"], "mode": r["mode"], "buckling_load_N": btxt,
                     "buckling_criterion": inst.get("criterion"), f"disp_at_buckling_{lu}": bd, tkey: bt,
                     f"max_disp_{lu}": maxd, "max_load_reached_N": Fend, "max_von_mises_Pa": vmax,
                     "max_plastic_strain": emax, "fracture_onset": ftxt, "energy_balance": status,
                     "ke_over_ie_max": (r.get("quasi_static") or {}).get("ke_over_ie_max"),
                     "added_mass_pct_max": (r.get("quasi_static") or {}).get("added_mass_pct_max")})
        js_runs.append({"label": r["label"], "run": r["run"], "name": r["name"], "mode": r["mode"],
                        "deck": r["deck"]["deck"], "units": r["deck"]["units"], "force_to_N": r["deck"]["force_to_N"],
                        "stress_to_Pa": r["deck"]["stress_to_Pa"], "cload": r["deck"]["cload"], "full_load_N": full,
                        "has_fail_model": r["deck"]["has_fail"], "sensors": r["deck"]["sensors"],
                        "frame_source": r.get("frame_source"), "summary_json": os.path.join(r["post"], "summary.json"),
                        "disp_source": r.get("disp_source"),
                        "reaction_source": {k: r["reaction"][k] for k in ("entity", "source", "impulse_cols")} if r["reaction"] else None,
                        "instability": inst, "buckling": {"load_N": bF, "time": bt, "disp": bd},
                        "fracture": fo, "notes": r["notes"], "energy": en, "table_row": rows[-1]})
    cj = os.path.join(out, "comparison.json")
    json.dump({"runs": js_runs, "frames": frame_check, "videos": videos, "plots": plots, "time_scale": ts, "time_axis": tlab,
               "settings": {k: getattr(a, k) for k in ("mode", "cmap", "kfrac", "track_tol", "sustain", "f_start", "smooth",
                                                         "drop", "min_frac", "speed", "ramp_duration", "display_duration")},
               "notes": ["Floor reaction = d(impulse)/dt of /TH/RWALL FN (Radioss T01 stores impulse).",
                         "Stress in Pa (from /BEGIN units, or as exported in run/vtk); forces in N."]},
              open(cj, "w"), indent=2,
              default=lambda o: o.item() if hasattr(o, "item") else (o.tolist() if hasattr(o, "tolist") else str(o)))
    with open(os.path.join(out, "comparison.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys())); w.writeheader(); w.writerows(rows)
    fm = lambda v, sci=False: "n/a" if v is None or (isinstance(v, float) and np.isnan(v)) else \
        (f"{v:.3e}" if sci else f"{v:.4g}") if isinstance(v, (float, int)) else str(v)
    hdr = ["Material", "Buckling load (N)", f"Disp. at buckling ({lu})",
           "Time at buckling (s, equiv.)" if scaled else f"Time at buckling ({tunit})", f"Max disp. ({lu})",
           "Max von Mises (Pa)", "Max plastic strain", "Fracture onset", "Energy balance", "KE/IE max", "Added mass %"]
    md = ["| " + " | ".join(hdr) + " |", "|" + "---|" * len(hdr)]
    for rw in rows:
        md.append("| " + " | ".join([rw["material"], rw["buckling_load_N"], fm(rw[f"disp_at_buckling_{lu}"]), fm(rw[tkey]),
                                      fm(rw[f"max_disp_{lu}"]), fm(rw["max_von_mises_Pa"], True), fm(rw["max_plastic_strain"]),
                                      rw["fracture_onset"], rw["energy_balance"], fm(rw["ke_over_ie_max"]),
                                      fm(rw["added_mass_pct_max"])]) + " |")
    open(os.path.join(out, "comparison.md"), "w").write("\n".join(md) + "\n")
    print("\n".join(md))
    log("wrote", cj)


if __name__ == "__main__":
    main()
