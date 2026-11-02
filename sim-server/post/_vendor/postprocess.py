#!/usr/bin/env python3
"""OpenRadioss / OpenCourant post-processing pipeline.

Usage:
    source /workspace/openradioss/tools/env.sh
    python /workspace/openradioss/post/postprocess.py /workspace/openradioss/results/<run>
        [--name ROOTNAME] [--field NAME] [--warp 1.0] [--fps 8] [--no-video] [--size 1280x720]

Given a run folder containing <name>A001[.gz], A002..., <name>T01 and *.out listings, writes into <run>/post/:
    vtk/<name>_NNN.vtk     animation frames converted with anim_to_vtk
    th/<name>T01.csv       time history converted with th_to_csv
    frames/frame_NNN.png   offscreen renders of the deformed mesh, colored by the chosen field
    <name>.mp4, <name>.gif animation
    plots/*.png            energy and per-entity time-history plots
    summary.json           frame count, time range, fields, maxima, energy balance check
"""
import argparse, glob, gzip, json, os, re, shutil, subprocess, sys, tempfile

# Headless rendering: prefer EGL so we never need/touch an X display.
os.environ.setdefault("VTK_DEFAULT_OPENGL_WINDOW", "vtkEGLRenderWindow")

import numpy as np

TOOL_SEARCH = [
    os.environ.get("OPENRADIOSS_PATH", ""),
    "/workspace/openradioss/tools/OpenCourant",
    "/workspace/openradioss/tools/OpenRadioss",
    "/opt/OpenRadioss", "/opt/OpenCourant",
]


def find_tool(stem):
    p = shutil.which(stem + "_linux64_gf") or shutil.which(stem)
    if p:
        return p
    for root in TOOL_SEARCH:
        if not root:
            continue
        for c in glob.glob(os.path.join(root, "exec", stem + "*")) + glob.glob(os.path.join(root, stem + "*")):
            if os.access(c, os.X_OK) and not c.endswith(".exe"):
                return c
    return None


def log(*a):
    print("[post]", *a, flush=True)


# ---------------------------------------------------------------- units (from the starter /BEGIN card)
MASS_SI = {"kg": 1.0, "g": 1e-3, "mg": 1e-6, "Mg": 1e3, "t": 1e3, "ton": 1e3, "lb": 0.45359237, "slug": 14.593903}
LEN_SI = {"m": 1.0, "dm": 0.1, "cm": 1e-2, "mm": 1e-3, "um": 1e-6, "mum": 1e-6, "km": 1e3, "in": 0.0254, "ft": 0.3048}
TIME_SI = {"s": 1.0, "ms": 1e-3, "us": 1e-6, "mus": 1e-6, "ns": 1e-9, "min": 60.0, "h": 3600.0}


def find_starter_deck(run, name=None):
    pats = ([f"{name}_0000.rad"] if name else []) + ["*_0000.rad"]
    for pat in pats:
        g = sorted(glob.glob(os.path.join(run, pat)))
        if g:
            return g[0]
    return None


def deck_units(run, name=None, deck=None):
    """Return unit info from /BEGIN (input units line). Factors convert model units -> SI."""
    deck = deck or find_starter_deck(run, name)
    u = {"deck": deck, "mass": None, "length": None, "time": None, "known": False,
         "force_to_N": 1.0, "stress_to_Pa": 1.0, "length_to_m": 1.0, "time_to_s": 1.0, "energy_to_J": 1.0}
    if not deck or not os.path.exists(deck):
        return u
    lines = []
    with open(deck, errors="ignore") as f:
        for l in f:
            if l.startswith("#"):
                continue
            lines.append(l.rstrip("\n"))
            if len(lines) > 400:
                break
    for i, l in enumerate(lines):
        if l.startswith("/BEGIN") and i + 3 < len(lines):
            toks = lines[i + 3].split()
            if len(toks) >= 3:
                M, L, T = toks[:3]
                u.update(mass=M, length=L, time=T)
                if M in MASS_SI and L in LEN_SI and T in TIME_SI:
                    m, l_, t = MASS_SI[M], LEN_SI[L], TIME_SI[T]
                    u.update(known=True, force_to_N=m * l_ / t ** 2, stress_to_Pa=m / (l_ * t ** 2),
                             length_to_m=l_, time_to_s=t, energy_to_J=m * l_ ** 2 / t ** 2)
            break
    return u


def solver_end_time(run, name):
    """End time from the engine deck /RUN card (model time units)."""
    for f in sorted(glob.glob(os.path.join(run, f"{name}_0001.rad"))):
        ls = [l for l in open(f, errors="ignore") if not l.startswith("#")]
        for i, l in enumerate(ls):
            if l.startswith("/RUN") and i + 1 < len(ls):
                try:
                    return float(ls[i + 1].split()[0])
                except (ValueError, IndexError):
                    pass
    return None


STRESS_RE = re.compile(r"von_mises|vonm|stress", re.I)


def external_frames(run):
    """Frames pre-exported by the run generator: <run>/vtk/*.vtk + <run>/vtk/frames.json (stress already in Pa).
    Returns list of dicts {file, time, display_time, load_N} or None. Accepts a list or {"frames": [...]},
    with flexible key names (file/vtk/path/name, time/t/solver_time, load_time/time_s/equiv_time, load/load_N/force)."""
    vdir = os.path.join(run, "vtk")
    fj = os.path.join(vdir, "frames.json")
    if not os.path.exists(fj):
        return None
    try:
        meta = json.load(open(fj))
    except Exception as e:
        log(f"frames.json unreadable ({e}); ignoring")
        return None
    items = meta.get("frames", meta) if isinstance(meta, dict) else meta
    files = sorted(glob.glob(os.path.join(vdir, "*.vtk")) + glob.glob(os.path.join(vdir, "*.vtu")))
    pick = lambda d, ks: next((d[k] for k in ks if k in d and d[k] is not None), None)
    out = []
    for i, it in enumerate(items if isinstance(items, list) else []):
        if not isinstance(it, dict):
            continue
        f = pick(it, ("file", "vtk", "path", "name", "filename"))
        f = (f if os.path.isabs(f) else os.path.join(vdir, f)) if f else (files[i] if i < len(files) else None)
        if not f or not os.path.exists(f):
            continue
        out.append({"file": f, "time": pick(it, ("time", "t", "solver_time", "time_ms")),
                    "display_time": pick(it, ("load_time", "time_s", "equiv_time", "display_time", "load_time_s")),
                    "load_N": pick(it, ("load_N", "load", "force_N", "force", "applied_load_N"))})
    if not out:
        log("frames.json present but no usable entries; falling back to own conversion")
        return None
    units = meta.get("stress_unit", meta.get("units", "Pa")) if isinstance(meta, dict) else "Pa"
    for o in out:
        o["stress_unit"] = units if isinstance(units, str) else "Pa"
    return out


# ---------------------------------------------------------------- discovery
ANIM_RE = re.compile(r"^(?P<name>.+)A(?P<num>\d{3,})(?P<gz>\.gz)?$")


def discover(run, name=None):
    anims = {}
    for f in os.listdir(run):
        m = ANIM_RE.match(f)
        if m and os.path.isfile(os.path.join(run, f)):
            if name and m["name"] != name:
                continue
            anims.setdefault(m["name"], []).append((int(m["num"]), os.path.join(run, f)))
    if not name:
        if anims:
            name = max(anims, key=lambda k: len(anims[k]))
        else:
            ths = [f[:-3] for f in os.listdir(run) if re.match(r".+T01(\.gz)?$", f)]
            ths = [re.sub(r"T01(\.gz)?$", "", f) for f in os.listdir(run) if re.match(r".+T01(\.gz)?$", f)]
            name = ths[0] if ths else None
    frames = sorted(anims.get(name, []))
    th = None
    for cand in (f"{name}T01", f"{name}T01.gz"):
        if name and os.path.exists(os.path.join(run, cand)):
            th = os.path.join(run, cand)
            break
    outs = sorted(glob.glob(os.path.join(run, f"{name}_*.out"))) if name else sorted(glob.glob(os.path.join(run, "*.out")))
    return name, frames, th, outs


def _plain_copy(src, dst):
    if src.endswith(".gz"):
        with gzip.open(src, "rb") as fi, open(dst, "wb") as fo:
            shutil.copyfileobj(fi, fo)
    else:
        shutil.copy(src, dst)


# ---------------------------------------------------------------- conversion
def convert_anim(frames, name, outdir, exe):
    os.makedirs(outdir, exist_ok=True)
    vtks = []
    with tempfile.TemporaryDirectory() as td:
        for num, src in frames:
            dst = os.path.join(outdir, f"{name}_{num:03d}.vtk")
            if os.path.exists(dst) and os.path.getmtime(dst) >= os.path.getmtime(src) and os.path.getsize(dst) > 0:
                vtks.append(dst)
                continue
            plain = os.path.join(td, f"{name}A{num:03d}")
            _plain_copy(src, plain)
            with open(dst, "wb") as fo:
                r = subprocess.run([exe, os.path.basename(plain)], cwd=td, stdout=fo, stderr=subprocess.PIPE)
            if r.returncode != 0 or os.path.getsize(dst) == 0:
                log(f"anim_to_vtk failed on {src}: {r.stderr.decode(errors='ignore')[:300]}")
                os.remove(dst)
                continue
            os.remove(plain)
            vtks.append(dst)
    return vtks


def convert_th(th, name, outdir, exe):
    os.makedirs(outdir, exist_ok=True)
    with tempfile.TemporaryDirectory() as td:
        plain = os.path.join(td, f"{name}T01")
        _plain_copy(th, plain)
        r = subprocess.run([exe, os.path.basename(plain)], cwd=td, capture_output=True)
        csvs = glob.glob(os.path.join(td, "*.csv"))
        if r.returncode != 0 or not csvs:
            log("th_to_csv failed:", r.stdout.decode(errors="ignore")[-300:], r.stderr.decode(errors="ignore")[-300:])
            return None
        out = os.path.join(outdir, os.path.basename(csvs[0]))
        shutil.move(csvs[0], out)
        return out


# ---------------------------------------------------------------- fields
FIELD_PRIORITY = [
    ("von_mises", re.compile(r"von_mises|vonm", re.I)),
    ("plastic_strain", re.compile(r"plastic_strain$|plastic_strain(?!_(upper|lower))|epsp", re.I)),
]


def merged_field(mesh, key_re):
    """Combine 1DELEM_/2DELEM_/3DELEM_ variants of a cell quantity (elementwise max of |value|)."""
    names = [n for n in mesh.cell_data.keys() if key_re.search(n) and not re.search(r"upper|lower|layer", n, re.I)]
    if not names:
        names = [n for n in mesh.cell_data.keys() if key_re.search(n)]
    if not names:
        names = [n for n in mesh.point_data.keys() if key_re.search(n)]
        if names:
            return np.abs(np.asarray(mesh.point_data[names[0]], float)), "point", names
        return None, None, []
    arr = np.max(np.abs(np.vstack([np.asarray(mesh.cell_data[n], float).reshape(-1) for n in names])), axis=0)
    return arr, "cell", names


def pick_field(meshes, override=None):
    m0 = meshes[-1]
    if override:
        rx = re.compile(re.escape(override), re.I)
        a, loc, names = merged_field(m0, rx)
        if names:
            return override, rx, names
        log(f"--field {override} not found, falling back to auto")
    for label, rx in FIELD_PRIORITY:
        a, loc, names = merged_field(m0, rx)
        if names and a is not None and np.nanmax(a) > 0:
            return label, rx, names
    return "displacement_magnitude", None, []


def displacement(mesh, ref):
    for k in mesh.point_data.keys():
        if k.lower().startswith("displacement"):
            return np.asarray(mesh.point_data[k], float)
    if ref is not None and ref.n_points == mesh.n_points:
        return np.asarray(mesh.points - ref.points, float)
    return np.zeros((mesh.n_points, 3))


def _cell_keys(g):
    """Per-cell key (element id, cell type) that survives cell extraction."""
    return np.asarray(g.cell_data["ELEMENT_ID"]).astype(np.int64) * 64 + np.asarray(g.celltypes).astype(np.int64)


def frame_time(mesh):
    try:
        return float(np.asarray(mesh.field_data["TIME"]).ravel()[0])
    except Exception:
        return None


# ---------------------------------------------------------------- rendering
def render(vtks, run_post, name, field_override, warp, fps, size, make_video, cmap="jet", stress_fac=1.0,
           stress_unit="model units", time_scale=1.0, time_label="t", ext=None):
    import pyvista as pv
    pv.OFF_SCREEN = True
    meshes = [pv.read(v) for v in vtks]
    ref = meshes[0]
    label, rx, srcnames = pick_field(meshes, field_override)
    log(f"coloring by: {label} {srcnames}")

    fields_found = {"point": sorted(set(ref.point_data.keys()) | set(meshes[-1].point_data.keys())),
                    "cell": sorted(set(ref.cell_data.keys()) | set(meshes[-1].cell_data.keys()))}
    times, scal, shown, eroded = [], [], [], []
    stats = {}
    for m in meshes:
        t = frame_time(m)
        if ext is not None and ext[len(times)].get("time") is not None:
            t = float(ext[len(times)]["time"])
        times.append(t)
        if not times[:-1]:
            es0, inactive0 = None, 0
        u = displacement(m, ref)
        umag = np.linalg.norm(u, axis=1)
        g = m.copy()
        if warp != 1.0 and ref.n_points == m.n_points:
            g.points = ref.points + warp * (m.points - ref.points)
        if label == "displacement_magnitude":
            g.point_data["_field"] = umag
        else:
            a, loc, _ = merged_field(m, rx)
            if STRESS_RE.search(label) or any(STRESS_RE.search(n) for n in srcnames):
                a = a * stress_fac
            (g.cell_data if loc == "cell" else g.point_data)["_field"] = a
        if "EROSION_STATUS" in g.cell_data.keys():
            es = np.asarray(g.cell_data["EROSION_STATUS"])
            # cells already inactive in frame 1 (e.g. rigid-body elements) are not fracture
            if es0 is None:
                es0 = es.copy(); inactive0 = int((es0 == 0).sum())
            er = (es == 0) & (es0 != 0) if es0.shape == es.shape else (es == 0)
            n_er = int(er.sum())
            eroded.append(n_er)
            if n_er:
                g = g.extract_cells(np.where(~er)[0])  # hide eroded (failed) elements
        else:
            eroded.append(0)
        shown.append(g)
        f = np.asarray(g.get_array("_field"), float) if g.n_cells else np.array([0.0])
        scal.append(f[np.isfinite(f)] if f.size else np.array([0.0]))
        # per-field maxima (cell + point scalars, vector magnitudes)
        for loc, data in (("cell", m.cell_data), ("point", m.point_data)):
            for k in data.keys():
                if k in ("NODE_ID", "ELEMENT_ID", "PART_ID", "EROSION_STATUS"):
                    continue
                v = np.asarray(data[k], float)
                if v.ndim > 1 and v.shape[1] in (3, 6, 9):
                    v = np.linalg.norm(v, axis=1) if v.shape[1] == 3 else v.max(axis=1)
                if not v.size:
                    continue
                mx = float(np.nanmax(np.abs(v)))
                if STRESS_RE.search(k):
                    mx *= stress_fac
                s = stats.setdefault(k, {"location": loc, "max_abs": -1.0,
                                         "unit": stress_unit if STRESS_RE.search(k) else "model units"})
                if mx > s["max_abs"]:
                    s.update(max_abs=mx, time=t, frame=len(times))
        md = float(umag.max()) if umag.size else 0.0
        s = stats.setdefault("displacement_magnitude", {"location": "point", "max_abs": -1.0})
        if md > s["max_abs"]:
            s.update(max_abs=md, time=t, frame=len(times))

    # parts whose field is identically zero in every frame (rigid walls, rigid bodies, unloaded parts)
    # are drawn as translucent gray context so they don't hide the loaded structure
    ctx_parts = set()
    if label != "displacement_magnitude" and "PART_ID" in shown[-1].cell_data.keys():
        live = set()
        for g in shown:
            if "PART_ID" in g.cell_data.keys() and "_field" in g.cell_data.keys():
                pid = np.asarray(g.cell_data["PART_ID"]); f = np.asarray(g.cell_data["_field"])
                live |= set(np.unique(pid[f > 0]).tolist())
        allp = set(np.unique(shown[-1].cell_data["PART_ID"]).tolist())
        if live:
            ctx_parts = allp - live
    # single-part exports (one PART_ID): fall back to elements whose field is zero in every frame
    ctx_keys = None
    if label != "displacement_magnitude" and not ctx_parts and "ELEMENT_ID" in shown[-1].cell_data.keys():
        live_k, all_k = set(), set()
        for g in shown:
            if "_field" in g.cell_data.keys() and g.n_cells:
                k = _cell_keys(g); f = np.nan_to_num(np.asarray(g.cell_data["_field"]))
                live_k |= set(k[f > 0].tolist()); all_k |= set(k.tolist())
        if live_k and all_k - live_k:
            ctx_keys = np.array(sorted(all_k - live_k))
    pool = np.concatenate(scal)
    fmax = float(pool.max()) if pool.size else 0.0
    nz = pool[pool > 0]
    # robust upper limit: 99th percentile of non-zero values over all frames (single hot elements don't wash out the map)
    top = float(np.percentile(nz, 99)) if nz.size else 1.0
    clim = [0.0, top if top > 0 else (fmax or 1.0)]
    # global bounds for a fixed camera
    def _ctxmask(g):
        if not g.n_cells:
            return None
        if ctx_parts and "PART_ID" in g.cell_data.keys():
            return np.isin(np.asarray(g.cell_data["PART_ID"]), list(ctx_parts))
        if ctx_keys is not None:
            return np.isin(_cell_keys(g), ctx_keys)
        return None

    def _live(g):
        m = _ctxmask(g)
        if m is not None and (~m).any():
            return g.extract_cells(np.where(~m)[0])
        return g
    has_ctx = bool(ctx_parts) or ctx_keys is not None
    b = np.array([_live(g).bounds for g in shown if g.n_points])
    b = b[np.isfinite(b).all(axis=1)]
    # pad the frame around the loaded parts by 30%
    pad = 0.3 * max(b[:, 1].max() - b[:, 0].min(), b[:, 3].max() - b[:, 2].min(), b[:, 5].max() - b[:, 4].min(), 1e-12)
    b = b + np.array([-pad, pad, -pad, pad, -pad, pad]) if has_ctx else b
    gb = [b[:, 0].min(), b[:, 1].max(), b[:, 2].min(), b[:, 3].max(), b[:, 4].min(), b[:, 5].max()]
    W, H = size
    fdir = os.path.join(run_post, "frames")
    os.makedirs(fdir, exist_ok=True)
    for f in glob.glob(os.path.join(fdir, "frame_*.png")):
        os.remove(f)
    pl = pv.Plotter(off_screen=True, window_size=(W, H))
    pl.set_background("white")
    pngs = []
    is_stress = label == "von_mises" or bool(STRESS_RE.search(label))
    bar_title = (f"von Mises stress ({stress_unit})" if label == "von_mises" else
                 f"{label.replace('_', ' ')} ({stress_unit})" if is_stress else label.replace("_", " "))
    bar = dict(title=bar_title, color="black", vertical=True, position_x=0.84, position_y=0.15,
               height=0.7, width=0.06, fmt="%.2e" if is_stress else "%.3g")
    for i, g in enumerate(shown):
        pl.clear()
        if not has_ctx:
            pl.add_mesh(ref.outline(), color="lightgray")
        ctx = _ctxmask(g) if has_ctx else None
        if ctx is not None:
            if ctx.any():
                pl.add_mesh(g.extract_cells(np.where(ctx)[0]), color="lightgray", opacity=0.25, show_edges=False)
            g = g.extract_cells(np.where(~ctx)[0])
        if g.n_cells:
            pl.add_mesh(g, scalars="_field", cmap=cmap, clim=clim, show_edges=g.n_cells < 4000, edge_color="#333333",
                        line_width=0.5, scalar_bar_args=bar, smooth_shading=False, lighting=True)
        t = times[i]
        tl = (f"{time_label} = {t * time_scale:.4g} s   (solver t = {t:.4g})" if time_scale != 1.0 else f"t = {t:.5g}") \
            if t is not None else ""
        if ext is not None:
            e = ext[i]
            if e.get("display_time") is not None:
                tl = f"load time = {float(e['display_time']):.3g} s"
            if e.get("load_N") is not None:
                tl += f"   load = {float(e['load_N']):.0f} N"
        pl.add_text(f"{name}   frame {i + 1}/{len(shown)}   {tl}",
                    position="upper_left", font_size=12, color="black")
        if i == 0:
            pl.reset_camera(bounds=gb)
            pl.camera.azimuth = 30
            pl.camera.elevation = 20
            pl.reset_camera(bounds=gb)
            cam = pl.camera.copy()
        else:
            pl.camera = cam.copy()
        p = os.path.join(fdir, f"frame_{i + 1:03d}.png")
        pl.screenshot(p)
        pngs.append(p)
    pl.close()

    videos = {}
    if make_video and pngs:
        import imageio.v2 as iio
        mp4 = os.path.join(run_post, f"{name}.mp4")
        with iio.get_writer(mp4, fps=fps, codec="libx264", quality=8, pixelformat="yuv420p", macro_block_size=16) as w:
            for p in pngs:
                w.append_data(iio.imread(p))
        videos["mp4"] = mp4
        from PIL import Image
        gif = os.path.join(run_post, f"{name}.gif")
        ims = [Image.open(p).convert("RGB").resize((W // 2, H // 2)) for p in pngs]
        ims = [im.quantize(colors=128, method=Image.Quantize.MEDIANCUT) for im in ims]
        ims[0].save(gif, save_all=True, append_images=ims[1:], duration=int(1000 / fps), loop=0, optimize=True)
        videos["gif"] = gif

    erosion = {"eroded_cells_per_frame": eroded, "max_eroded_cells": max(eroded) if eroded else 0,
               "inactive_in_first_frame": inactive0,
               "definition": "cells with EROSION_STATUS=0 that were active in frame 1"}
    first = next((i for i, n in enumerate(eroded) if n > 0), None)
    if first is not None:
        erosion.update(first_frame=first + 1, first_time=times[first],
                       first_time_display=(times[first] * time_scale if times[first] is not None else None))
    return dict(frame_count=len(meshes), frame_times=times, erosion_anim=erosion, colormap=cmap,
                stress_unit=stress_unit, stress_to_Pa=stress_fac, n_points=int(ref.n_points), n_cells=int(ref.n_cells),
                field_used=label, field_sources=srcnames, color_range=clim, field_used_max=fmax, context_parts_translucent=sorted(int(x) for x in ctx_parts),
                context_cells_translucent=int(len(ctx_keys)) if ctx_keys is not None else 0, fields_found=fields_found,
                field_max=stats, frames_dir=fdir, videos=videos)


# ---------------------------------------------------------------- time history
GLOBAL_ENERGY = {"internal": "INTERNAL ENERGY", "kinetic": "KINETIC ENERGY", "rotational": "ROTATION ENERGY",
                 "external_work": "EXTERNAL WORK", "contact": "CONTACT ENERGY", "hourglass": "HOURGLASS ENERGY",
                 "spring": "SPRING ENERGY", "plastic_work": "PLASTIC WORK"}


def th_plots(csv, outdir, max_entity_plots=60, time_scale=1.0, time_label="time"):
    import pandas as pd
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    os.makedirs(outdir, exist_ok=True)
    df = pd.read_csv(csv)
    df.columns = [c.strip() for c in df.columns]
    tcol = df.columns[0]
    t = df[tcol].values * time_scale
    res = {"time_scale": time_scale, "time_axis": time_label, "csv": csv, "th_time_range": [float(t.min()), float(t.max())], "n_samples": int(len(t)),
           "n_columns": int(df.shape[1]), "plots": []}
    col = lambda k: df[GLOBAL_ENERGY[k]].values if GLOBAL_ENERGY[k] in df.columns else None
    E = {k: col(k) for k in GLOBAL_ENERGY}
    if E["internal"] is not None and E["kinetic"] is not None:
        tot = E["internal"] + E["kinetic"] + (E["rotational"] if E["rotational"] is not None else 0)
        # contact / hourglass energies are reported separately in Radioss TH; include them in the balance
        tot_full = tot + sum(E[k] for k in ("contact", "hourglass") if E[k] is not None)
        fig, ax = plt.subplots(figsize=(10, 6))
        for k in ("internal", "kinetic", "rotational", "contact", "hourglass", "spring", "external_work"):
            if E[k] is not None and np.any(E[k] != 0):
                ax.plot(t, E[k], label=GLOBAL_ENERGY[k].title())
        ax.plot(t, tot, "k--", label="Total (IE+KE+RKE)")
        ax.plot(t, tot_full, "k-", lw=2, label="Total incl. contact+hourglass")
        ax.set_xlabel(time_label); ax.set_ylabel("energy (model units)"); ax.grid(alpha=.3); ax.legend(); ax.set_title("Global energies")
        p = os.path.join(outdir, "energies.png"); fig.tight_layout(); fig.savefig(p, dpi=120); plt.close(fig)
        res["plots"].append(p)
        wext = E["external_work"] if E["external_work"] is not None else np.zeros_like(t)

        def bal(T):
            err = T - T[0] - wext
            ref = max(np.max(np.abs(T)), np.max(np.abs(wext)), 1e-30)
            return float(err[-1] / ref), float(np.max(np.abs(err)) / ref)
        b1, b2 = bal(tot), bal(tot_full)
        mx = lambda a: float(np.max(a)) if a is not None else None
        hg_ratio = (mx(E["hourglass"]) / mx(E["internal"])) if E["hourglass"] is not None and mx(E["internal"]) else None
        res["energy"] = {
            "initial": {k: float(v[0]) for k, v in E.items() if v is not None},
            "final": {k: float(v[-1]) for k, v in E.items() if v is not None},
            "max": {k: mx(v) for k, v in E.items() if v is not None},
            "balance_IE_KE_RKE": {"final_rel_error": b1[0], "max_rel_error": b1[1]},
            "balance_incl_contact_hourglass": {"final_rel_error": b2[0], "max_rel_error": b2[1]},
            "definition": "rel_error = (Etot(t) - Etot(0) - Wext(t)) / max(|Etot|,|Wext|)",
            "balance_ok_5pct": bool(min(abs(b1[1]), abs(b2[1])) < 0.05),
            "hourglass_to_internal_ratio": hg_ratio,
            "hourglass_warning": bool(hg_ratio is not None and hg_ratio > 0.1),
        }
        # momentum / timestep side plots
        extra = [c for c in ("TIME STEP", "ADDED MASS", "PERCENTAGE ADDED MASS") if c in df.columns and df[c].any()]
        if extra:
            fig, axs = plt.subplots(len(extra), 1, figsize=(10, 3 * len(extra)), squeeze=False)
            for a, c in zip(axs[:, 0], extra):
                a.plot(t, df[c]); a.set_ylabel(c.title()); a.grid(alpha=.3)
            axs[-1, 0].set_xlabel(time_label)
            p = os.path.join(outdir, "timestep_mass.png"); fig.tight_layout(); fig.savefig(p, dpi=110); plt.close(fig)
            res["plots"].append(p)

    # per-entity histories: columns "<ENTITY NAME (padded)> <VAR>"
    globals_ = set(GLOBAL_ENERGY.values()) | {tcol}
    first_entity = None
    groups = {}
    known_global = True
    for c in df.columns[1:]:
        toks = [x for x in re.split(r"\s{2,}", c.strip()) if x]
        if len(toks) < 2:
            if known_global:
                continue  # global variable (energies, momentum, mass...) without entity prefix
            ent, var = "misc", c
        else:
            known_global = False
            ent, var = " | ".join(toks[:-1]), toks[-1]
        groups.setdefault(ent, []).append((var, c))
    # fracture/erosion onset from part TH "ERODED" (finer than animation frames)
    er = []
    for ent, cols in groups.items():
        for v, c in cols:
            if v.upper().startswith("ERODED"):
                y = df[c].values
                if np.any(y > 0):
                    i = int(np.argmax(y > 0))
                    er.append({"entity": ent, "first_time_display": float(t[i]),
                               "first_time_solver": float(df[tcol].values[i]), "final_eroded": float(y[-1])})
    res["erosion_th"] = sorted(er, key=lambda x: x["first_time_display"])
    res["entities"] = {}
    for n, (ent, cols) in enumerate(groups.items()):
        live = [(v, c) for v, c in cols if np.nanmax(np.abs(df[c].values)) > 0]
        res["entities"][ent] = {v: {"min": float(df[c].min()), "max": float(df[c].max())} for v, c in live}
        if not live or n >= max_entity_plots:
            continue
        nc = 3
        nr = int(np.ceil(len(live) / nc))
        fig, axs = plt.subplots(nr, nc, figsize=(13, 2.6 * nr), squeeze=False)
        for a in axs.ravel()[len(live):]:
            a.axis("off")
        for a, (v, c) in zip(axs.ravel(), live):
            a.plot(t, df[c].values, lw=1.2); a.set_title(v, fontsize=9); a.grid(alpha=.3); a.tick_params(labelsize=7)
        fig.suptitle(ent)
        safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", ent)[:60]
        p = os.path.join(outdir, f"th_{n:03d}_{safe}.png"); fig.tight_layout(); fig.savefig(p, dpi=100); plt.close(fig)
        res["plots"].append(p)
    return res


# ---------------------------------------------------------------- listings
def parse_listings(outs):
    info = []
    for o in outs:
        txt = open(o, errors="ignore").read()
        d = {"file": o, "normal_termination": "NORMAL TERMINATION" in txt,
             "errors": len(re.findall(r"^\s*ERROR ID", txt, re.M)), "warnings": len(re.findall(r"^\s*WARNING ID", txt, re.M))}
        rows = re.findall(r"^\s*(\d+)\s+(\S+)\s+(\S+)\s+\S+\s+\d+\s+(-?[\d.]+)%", txt, re.M)
        if rows:
            d["cycles"] = int(rows[-1][0]); d["final_time"] = float(rows[-1][1])
            d["listing_energy_error_pct_final"] = float(rows[-1][3])
            d["listing_energy_error_pct_maxabs"] = max(abs(float(r[3])) for r in rows)
        m = re.search(r"TOTAL NUMBER OF CYCLES\s*:\s*(\d+)", txt)
        if m:
            d["cycles"] = int(m.group(1))
        m = re.search(r"ELAPSED TIME\s*=\s*([\d.]+)", txt)
        if m:
            d["elapsed_s"] = float(m.group(1))
        info.append(d)
    return info


def time_scaling(a, t_end, units):
    """Returns (factor, axis label). Display time = solver time * factor."""
    if getattr(a, "display_duration", None):
        if not t_end:
            log("WARNING: --display-duration given but /RUN end time not found; using factor 1")
            return 1.0, "time (solver units)"
        return a.display_duration / t_end, f"equivalent load time (s)  [solver 0-{t_end:g} {units.get('time') or ''} -> 0-{a.display_duration:g} s]"
    if getattr(a, "time_scale", None):
        return a.time_scale, "equivalent load time (s)"
    if units.get("known") and units["time_to_s"] != 1.0:
        return 1.0, f"time ({units['time']})"
    return 1.0, "time"


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("run")
    ap.add_argument("--name", help="run root name (default: auto from *A001)")
    ap.add_argument("--field", help="substring of field to color by (overrides auto)")
    ap.add_argument("--warp", type=float, default=1.0, help="displacement scale factor for rendering")
    ap.add_argument("--fps", type=int, default=8)
    ap.add_argument("--size", default="1280x720")
    ap.add_argument("--no-video", action="store_true")
    ap.add_argument("--out", help="output dir (default <run>/post)")
    ap.add_argument("--own-conversion", action="store_true", help="ignore <run>/vtk/frames.json exports")
    ap.add_argument("--cmap", default="jet", help="colormap (default jet: blue=low -> red=high)")
    ap.add_argument("--time-scale", type=float, help="multiply solver time by this for display")
    ap.add_argument("--display-duration", type=float,
                    help="map solver end time (/RUN) to this many seconds of 'equivalent load time'")
    a = ap.parse_args()
    run = os.path.abspath(a.run)
    post = os.path.abspath(a.out or os.path.join(run, "post"))
    os.makedirs(post, exist_ok=True)
    name, frames, th, outs = discover(run, a.name)
    if not name:
        sys.exit(f"no OpenRadioss outputs (A00x / T01) found in {run}")
    log(f"run={run} name={name} frames={len(frames)} th={bool(th)} listings={len(outs)}")
    summary = {"run": run, "name": name, "post_dir": post, "anim_files": len(frames), "th_file": th,
               "tools": {}, "listings": parse_listings(outs)}
    units = deck_units(run, name)
    t_end = solver_end_time(run, name)
    tscale, tlabel = time_scaling(a, t_end, units)
    summary.update(units=units, solver_end_time=t_end, time_scale=tscale, time_axis=tlabel)
    stress_unit = "Pa" if units["known"] else "model units"
    a2v, t2c = find_tool("anim_to_vtk"), find_tool("th_to_csv")
    summary["tools"] = {"anim_to_vtk": a2v, "th_to_csv": t2c}

    ext0 = None if a.own_conversion else external_frames(run)
    if frames or ext0:
        if not a2v and not ext0:
            summary["anim_error"] = "anim_to_vtk not found (source /workspace/openradioss/tools/env.sh)"
        else:
            ext = None if a.own_conversion else external_frames(run)
            if ext:
                vtks = [e["file"] for e in ext]
                summary["vtk_dir"] = os.path.join(run, "vtk"); summary["frames_source"] = "run/vtk/frames.json (generator export)"
                log(f"using {len(vtks)} pre-exported frames from {summary['vtk_dir']}")
            else:
                vtks = convert_anim(frames, name, os.path.join(post, "vtk"), a2v)
                summary["vtk_dir"] = os.path.join(post, "vtk"); summary["frames_source"] = "anim_to_vtk"
            if vtks:
                W, H = (int(x) for x in a.size.lower().split("x"))
                r = render(vtks, post, name, a.field, a.warp, a.fps, (W - W % 16, H - H % 16), not a.no_video,
                           cmap=a.cmap, stress_fac=1.0 if ext else units["stress_to_Pa"],
                           stress_unit=(ext[0]["stress_unit"] if ext else stress_unit), ext=ext,
                           time_scale=tscale, time_label="equivalent load time" if a.display_duration or a.time_scale else "t")
                summary.update(r)
                ts = [x for x in r["frame_times"] if x is not None]
                summary["time_range"] = [min(ts), max(ts)] if ts else None
                summary["time_range_display"] = [min(ts) * tscale, max(ts) * tscale] if ts else None
    if th:
        if not t2c:
            summary["th_error"] = "th_to_csv not found"
        else:
            csv = convert_th(th, name, os.path.join(post, "th"), t2c)
            if csv:
                summary["time_history"] = th_plots(csv, os.path.join(post, "plots"), time_scale=tscale, time_label=tlabel)
    js = os.path.join(post, "summary.json")
    with open(js, "w") as f:
        json.dump(summary, f, indent=2, default=lambda o: o.item() if hasattr(o, "item") else str(o))
    log("wrote", js)
    for k in ("mp4", "gif"):
        if summary.get("videos", {}).get(k):
            log(k, summary["videos"][k])


if __name__ == "__main__":
    main()
