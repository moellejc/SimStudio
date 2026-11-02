#!/usr/bin/env python3
"""Build ONE self-contained, offline HTML 3D viewer that steps through OpenRadioss/OpenCourant animation frames.

Usage:
    source /workspace/openradioss/tools/env.sh
    python /workspace/openradioss/post/make_viewer.py RUN1 [RUN2 ...] --out viewer.html
        [--labels "304 stainless,6061-T6 Al,Polystyrene"]
        [--ramp-duration 5 | --display-duration 5 | --time-scale K]   equivalent load time axis
        [--post-root DIR]      own VTK conversion goes to DIR/<run>/post/vtk (run folders untouched)
        [--own-conversion]     ignore <run>/vtk/frames.json exports
        [--comparison FILE]    compare.py comparison.json for buckling points (default: next to --out, if present)
        [--max-mb 30]          total size budget; frames are subsampled in time if exceeded

Data: <run>/vtk/*.vtk + frames.json (stress already in Pa; time/load labels) when present, else anim_to_vtk
conversion of <run>/<name>A### (stress converted to Pa from /BEGIN units). Only the deforming structure is kept:
cells with non-zero stress in some frame that are active in frame 1 (rigid caps / rigid bodies inactive from t=0
and never-stressed parts are dropped); never-stressed 2D cells (floor plate) are shown as a static translucent
surface. Outer surface triangles are stored once; per frame: Int16 displacements, Uint16 per-vertex von Mises (Pa)
and plastic strain, each with a per-frame scale, plus a bitmask of eroded triangles (hidden in that frame).
three.js r147 (MIT) is inlined from /workspace/openradioss/tools/three/.
"""
import argparse, base64, glob, gzip, json, math, os, re, sys
os.environ.setdefault("VTK_DEFAULT_OPENGL_WINDOW", "vtkEGLRenderWindow")
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import postprocess as pp  # noqa: E402
import compare as cmp  # noqa: E402

THREE_DIR = os.path.join(os.path.dirname(HERE), "assets", "three")  # simstudio: vendored copy
VM_RE, EPSP_RE = pp.FIELD_PRIORITY[0][1], pp.FIELD_PRIORITY[1][1]
CELL_DIM = {1: 0, 3: 1, 4: 1, 5: 2, 7: 2, 8: 2, 9: 2, 10: 3, 11: 3, 12: 3, 13: 3, 14: 3, 21: 1, 22: 2, 23: 2, 24: 3, 25: 3}


def log(*a):
    print("[viewer]", *a, flush=True)


GZIP = True   # set from --no-gzip in main()


def b64(arr):
    """base64 of the raw little-endian bytes; gzip-compressed (prefixed 'z:') unless --no-gzip.
    The viewer inflates with the browser's built-in DecompressionStream (no network)."""
    raw = np.ascontiguousarray(arr).tobytes()
    if GZIP:
        return "z:" + base64.b64encode(gzip.compress(raw, 6, mtime=0)).decode("ascii")
    return base64.b64encode(raw).decode("ascii")


def cell_field(m, rx, fac=1.0):
    a, loc, names = pp.merged_field(m, rx)
    if not names:
        return None, None
    return np.nan_to_num(np.asarray(a, float)) * fac, loc


def post_dir(run, a):
    return os.path.join(a.post_root, os.path.basename(os.path.normpath(run)), "post") if a.post_root else os.path.join(run, "post")


def time_scale(a, run, name, deck):
    if a.time_scale:
        return a.time_scale
    if a.ramp_duration:
        te = cmp.ramp_end_time(deck)
        if te:
            return a.ramp_duration / te
        log("WARNING: --ramp-duration: no load/velocity function found; time not scaled")
    if a.display_duration:
        te = pp.solver_end_time(run, name)
        if te:
            return a.display_duration / te
        log("WARNING: /RUN end time not found; time not scaled")
    return 1.0


def frame_loads(run, name, deck, times, post):
    """(kind, loads[]) - applied load from /CLOAD, else floor reaction from TH (velocity-controlled runs)."""
    t = np.array([x if x is not None else np.nan for x in times], float)
    if deck["cload"]:
        F, _, _ = cmp.applied_load(np.nan_to_num(t), deck)
        if F is not None:
            return "applied load", [float(x) for x in F]
    _, _, th, _ = pp.discover(run, name)
    t2c = pp.find_tool("th_to_csv")
    if th and t2c:
        csv = pp.convert_th(th, name, os.path.join(post, "th"), t2c)
        if csv:
            import pandas as pd
            df = pd.read_csv(csv); df.columns = [c.strip() for c in df.columns]
            R = cmp.extract_reaction(df, deck)
            if R:
                return "floor reaction", [float(x) for x in np.interp(np.nan_to_num(t), R["time"], R["force"])]
    return None, [None] * len(times)


def load_run(run, label, a):
    import pyvista as pv
    name, frames, _, _ = pp.discover(run)
    deck = cmp.parse_deck(cmp.find_deck(run, name), run, name)
    post = post_dir(run, a)
    ext = None if a.own_conversion else pp.external_frames(run)
    if ext:
        files, sfac, src = [e["file"] for e in ext], 1.0, "run/vtk/frames.json"
    else:
        a2v = pp.find_tool("anim_to_vtk")
        if not frames or not a2v:
            raise SystemExit(f"{run}: no animation frames or anim_to_vtk not found (source tools/env.sh)")
        files = pp.convert_anim(frames, name, os.path.join(post, "vtk"), a2v)
        sfac, src = (deck["stress_to_Pa"] or 1.0), "anim_to_vtk"
    if not files:
        raise SystemExit(f"{run}: no frames")
    log(f"{label}: {len(files)} frames from {src}")
    ts = time_scale(a, run, name, deck)
    pts, vm, ep, es, times = [], [], [], [], []
    m0 = None
    for i, f in enumerate(files):
        m = pv.read(f)
        if m0 is None:
            m0 = m
        elif m.n_cells != m0.n_cells or m.n_points != m0.n_points:
            log(f"WARNING: frame {i + 1} topology differs; stopping at frame {i}")
            break
        pts.append(np.asarray(m.points, np.float64))
        v, vloc = cell_field(m, VM_RE, sfac)
        e, eloc = cell_field(m, EPSP_RE)
        vm.append(v if vloc == "cell" else None)
        ep.append(e if eloc == "cell" else None)
        es.append(np.asarray(m.cell_data["EROSION_STATUS"]) if "EROSION_STATUS" in m.cell_data.keys() else None)
        t = pp.frame_time(m)
        if ext and ext[i].get("time") is not None:
            t = float(ext[i]["time"])
        times.append(t)
    nF = len(pts)
    nc = m0.n_cells
    ctype = np.asarray(m0.celltypes)
    dim = np.array([CELL_DIM.get(int(c), 0) for c in ctype])
    es0 = es[0] if es[0] is not None else np.ones(nc, int)
    live = np.zeros(nc, bool)
    for v in vm:
        if v is not None:
            live |= v > 0
    if not live.any():
        live[:] = True
    struct = live & (es0 != 0) & (dim >= 2)
    if not struct.any():
        raise SystemExit(f"{run}: no structural cells found")
    ctx2d = ~live & (dim == 2)
    # outer surface of the structure (triangles), mapped back to frame cell/point ids
    sub = m0.extract_cells(np.where(struct)[0])
    sub.point_data["gid_p"] = np.asarray(sub.point_data["vtkOriginalPointIds"])
    sub.cell_data["gid_c"] = np.asarray(sub.cell_data["vtkOriginalCellIds"])
    surf = sub.extract_surface(pass_pointid=False, pass_cellid=False, algorithm="dataset_surface").triangulate()
    vid = np.asarray(surf.point_data["gid_p"], np.int64)
    tcell = np.asarray(surf.cell_data["gid_c"], np.int64)
    tri = surf.faces.reshape(-1, 4)[:, 1:].astype(np.uint32)
    nv, nt = len(vid), len(tri)
    base = pts[0][vid]
    cnt = np.bincount(tri.ravel(), minlength=nv).astype(float)
    cnt[cnt == 0] = 1.0

    def to_vertex(cv):
        w = np.repeat(cv[tcell], 3)
        return np.bincount(tri.ravel(), weights=w, minlength=nv) / cnt

    # floor / never-stressed 2D context, static
    ctx = None
    if ctx2d.any():
        cs = m0.extract_cells(np.where(ctx2d)[0]).extract_surface(algorithm="dataset_surface").triangulate()
        if 0 < cs.n_cells <= 40000:
            ctx = {"pos": b64(np.asarray(cs.points, np.float32)),
                   "idx": b64(cs.faces.reshape(-1, 4)[:, 1:].astype(np.uint32)), "ntri": int(cs.n_cells)}
    # loads, display times
    kind, loads = frame_loads(run, name, deck, times, post)
    disp_t = [None] * nF
    for i in range(nF):
        if ext and ext[i].get("display_time") is not None:
            disp_t[i] = float(ext[i]["display_time"])
        elif times[i] is not None and ts != 1.0:
            disp_t[i] = times[i] * ts
        if ext and ext[i].get("load_N") is not None:
            loads[i] = float(ext[i]["load_N"]); kind = kind or "applied load"
    # per-frame quantized data
    fr, vmax, emax, vpool, epool, eroded = [], 0.0, 0.0, [], [], []
    for i in range(nF):
        d = (pts[i][vid] - base)
        ds = float(np.abs(d).max()) / 32767 or 1.0
        rec = {"t": times[i], "td": disp_t[i], "load": loads[i], "ds": ds, "d": b64(np.round(d / ds).astype(np.int16))}
        for key, arr, pool in (("v", vm[i], vpool), ("e", ep[i], epool)):
            if arr is None:
                rec[key + "s"] = 0.0; rec[key] = None; continue
            vv = np.clip(to_vertex(arr), 0, None)
            mx = float(vv.max())
            sc = mx / 65535   # 0 when the field is all zero (keeps 'frame max' honest)
            q = np.round(vv / sc) if mx > 0 else np.zeros_like(vv)
            rec[key + "s"] = sc; rec[key] = b64(q.astype(np.uint16))
            pool.append(vv[vv > 0])
            if key == "v":
                vmax = max(vmax, mx)
            else:
                emax = max(emax, mx)
        ner = 0
        if es[i] is not None:
            er = (es[i] == 0) & (es0 != 0)
            hid = er[tcell]
            ner = int(er[struct].sum())
            if hid.any():
                rec["hide"] = b64(np.packbits(hid, bitorder="little"))
        rec["ner"] = ner
        eroded.append(ner)
        fr.append(rec)
    robust = lambda pool, mx: float(np.percentile(np.concatenate(pool), 99.5)) if pool and sum(map(len, pool)) else mx
    bb = np.vstack([base.min(0), base.max(0)])
    log(f"{label}: {nv} vertices, {nt} triangles, {int(struct.sum())} structural cells, "
        f"{int((es0 == 0).sum())} inactive at t=0 skipped, ctx tris {ctx['ntri'] if ctx else 0}")
    return {"label": label, "run": run, "name": name, "frame_source": src, "nv": nv, "nt": nt,
            "base": b64(base.astype(np.float32)), "tri": b64(tri), "frames": fr, "ctx": ctx,
            "vmax": vmax, "emax": emax, "vrob": robust(vpool, vmax), "erob": robust(epool, emax),
            "has_v": any(v is not None for v in vm), "has_e": any(e is not None for e in ep),
            "load_kind": kind, "time_scale": ts, "length_unit": deck["length_unit"], "time_unit": deck["time_unit"],
            "stress_unit": "Pa" if (ext or deck["stress_to_Pa"]) else "model units",
            "bbox": bb.tolist(), "eroded_cells_per_frame": eroded, "_times": times}


def attach_buckling(runs, comp_path):
    if not comp_path or not os.path.exists(comp_path):
        return None
    try:
        C = json.load(open(comp_path))
    except Exception as e:
        log(f"comparison.json unreadable: {e}"); return None
    cts = C.get("time_scale", 1.0) or 1.0
    for r in runs:
        match = next((x for x in C.get("runs", []) if os.path.normpath(x.get("run", "")) == os.path.normpath(r["run"])), None) \
            or next((x for x in C.get("runs", []) if x.get("label") == r["label"]), None)
        if not match:
            continue
        b, inst = match.get("buckling") or {}, match.get("instability") or {}
        row = match.get("table_row") or {}
        if b.get("time") is None or not (inst.get("detected") or match.get("mode") == "velocity"):
            r["buckling"] = {"text": row.get("buckling_load_N") or "no buckling detected", "frame": None}
            continue
        ts_solver = b["time"] / cts
        tt = np.array([t if t is not None else np.nan for t in r["_times"]])
        fi = int(np.nanargmin(np.abs(tt - ts_solver))) if np.isfinite(tt).any() else None
        r["buckling"] = {"frame": fi, "load_N": b.get("load_N"), "time_display": ts_solver * r["time_scale"],
                         "criterion": inst.get("criterion"), "text": f"{b.get('load_N', 0):.4g} N"}
    return comp_path


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("runs", nargs="+")
    ap.add_argument("--out", required=True)
    ap.add_argument("--labels")
    ap.add_argument("--ramp-duration", type=float)
    ap.add_argument("--display-duration", type=float)
    ap.add_argument("--time-scale", type=float)
    ap.add_argument("--post-root")
    ap.add_argument("--own-conversion", action="store_true")
    ap.add_argument("--comparison")
    ap.add_argument("--max-mb", type=float, default=30.0)
    ap.add_argument("--no-gzip", action="store_true",
                    help="store arrays as plain base64 (bigger file; for browsers without DecompressionStream)")
    ap.add_argument("--fps", type=float, default=8.0)
    ap.add_argument("--title", default="OpenRadioss frame viewer")
    a = ap.parse_args(argv)
    labels = a.labels.split(",") if a.labels else \
        [re.sub(r"^(fork45v2_|fork45_|fork_|t45_)", "", os.path.basename(os.path.normpath(r))) for r in a.runs]
    if len(labels) != len(a.runs):
        raise SystemExit("--labels count must match runs")
    global GZIP
    GZIP = not a.no_gzip
    runs = [load_run(os.path.abspath(r), l.strip(), a) for r, l in zip(a.runs, labels)]

    # size budget: subsample frames uniformly (keep first and last) if needed
    budget = a.max_mb * 1e6 * 0.97
    def est():
        return sum(len(r["base"]) + len(r["tri"]) + sum(len(f["d"]) + len(f["v"] or "") + len(f["e"] or "") +
                                                         len(f.get("hide", "")) + 200 for f in r["frames"])
                   + (len(r["ctx"]["pos"]) + len(r["ctx"]["idx"]) if r["ctx"] else 0) for r in runs) + 900_000
    size0, sub_note = est(), None
    if size0 > budget:
        per_frame = (size0 - 900_000 - sum(len(r["base"]) + len(r["tri"]) for r in runs)) / max(1, sum(len(r["frames"]) for r in runs))
        static = size0 - per_frame * sum(len(r["frames"]) for r in runs)
        nmax = max(2, int((budget - static) / per_frame / len(runs)))
        for r in runs:
            n = len(r["frames"])
            if n > nmax:
                keep = sorted(set(np.round(np.linspace(0, n - 1, nmax)).astype(int).tolist()))
                r["frames"] = [r["frames"][k] for k in keep]
                r["_times"] = [r["_times"][k] for k in keep]
                r["frame_numbers"] = [k + 1 for k in keep]
        sub_note = f"frames subsampled to <= {nmax} per run to stay under {a.max_mb:g} MB (was {size0 / 1e6:.1f} MB)"
        log("NOTE:", sub_note)
    for r in runs:
        r.setdefault("frame_numbers", list(range(1, len(r["frames"]) + 1)))
    comp = a.comparison or os.path.join(os.path.dirname(os.path.abspath(a.out)), "comparison.json")
    comp = attach_buckling(runs, comp)
    for r in runs:
        r.pop("_times", None)
    scaled = any(r["time_scale"] != 1.0 for r in runs) or any(f["td"] is not None for r in runs for f in r["frames"])
    meta = {"title": a.title, "runs": runs, "fps": a.fps, "scaled_time": scaled, "comparison": comp, "note": sub_note}
    js_three = open(os.path.join(THREE_DIR, "three.min.js")).read()
    js_orbit = open(os.path.join(THREE_DIR, "OrbitControls.js")).read()
    data = json.dumps(meta, separators=(",", ":"), default=lambda o: o.item() if hasattr(o, "item") else str(o))
    html = HTML.replace("__TITLE__", a.title).replace("__THREE__", js_three).replace("__ORBIT__", js_orbit) \
        .replace("__APP__", APP_JS)
    html = html.replace("__DATA__", data.replace("</", "<\\/"))
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    with open(a.out, "w") as f:
        f.write(html)
    log(f"wrote {a.out} ({os.path.getsize(a.out) / 1e6:.1f} MB)")


HTML = r"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><title>__TITLE__</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
html,body{margin:0;height:100%;font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;background:#fff;color:#111;overflow:hidden}
#bar{position:absolute;left:0;right:0;top:0;height:auto;padding:6px 10px;background:#f3f4f6;border-bottom:1px solid #ccc;
 display:flex;flex-wrap:wrap;gap:8px;align-items:center;font-size:13px;z-index:5}
#bar label{display:flex;gap:4px;align-items:center}
#bar button{padding:3px 8px;font-size:13px;cursor:pointer}
#slider{width:260px}
#view{position:absolute;left:0;right:0;bottom:0}
#gl{display:block;width:100%;height:100%}
.ov{position:absolute;pointer-events:none;box-sizing:border-box;border-right:1px solid #ddd}
.ov .hd{position:absolute;left:8px;top:6px;font-size:13px;line-height:1.35;background:rgba(255,255,255,.75);padding:3px 6px;border-radius:4px}
.ov .hd b{font-size:14px}
.ov .bk{color:#b00020;font-weight:600}
.ov.atbk{outline:3px solid #d00;outline-offset:-3px}
.ov canvas{position:absolute;left:50%;transform:translateX(-50%);bottom:10px}
#msg{position:absolute;left:10px;bottom:72px;font-size:11px;color:#666;z-index:4}
</style></head><body>
<div id="bar">
 <label>View <select id="mode"></select></label>
 <label>Field <select id="field"><option value="v">von Mises stress (Pa)</option><option value="e">plastic strain</option></select></label>
 <label>Scale <select id="scale"><option value="shared">shared</option><option value="per">per material</option></select></label>
 <button id="prev" title="previous frame">&#9664;</button>
 <button id="play">Play</button>
 <button id="next" title="next frame">&#9654;</button>
 <input id="slider" type="range" min="0" max="0" value="0" step="1" list="bkticks"><datalist id="bkticks"></datalist>
 <span id="fl"></span>
 <label>Speed <select id="speed"><option>0.25</option><option>0.5</option><option selected>1</option><option>2</option><option>4</option></select>x</label>
 <label><input type="checkbox" id="floor" checked> floor</label>
 <button id="bkbtn">Go to buckling</button>
 <button id="reset">Reset view</button>
</div>
<div id="view"><canvas id="gl"></canvas></div>
<div id="msg"></div>
<script id="data" type="application/json">__DATA__</script>
<script>__THREE__</script>
<script>__ORBIT__</script>
<script>__APP__</script>
</body></html>
"""

APP_JS = r"""
(async function(){
const D = JSON.parse(document.getElementById('data').textContent);
const $ = id => document.getElementById(id);
function raw64(s){ const bin = atob(s); const u = new Uint8Array(bin.length); for (let i = 0; i < bin.length; i++) u[i] = bin.charCodeAt(i); return u; }
async function unpack(s){
  if (typeof s !== 'string') return s;
  if (!s.startsWith('z:')) return raw64(s);
  const ds = new DecompressionStream('gzip');
  const buf = await new Response(new Blob([raw64(s.slice(2))]).stream().pipeThrough(ds)).arrayBuffer();
  return new Uint8Array(buf);
}
if (typeof DecompressionStream === 'undefined' && JSON.stringify(D).includes('"z:')) {
  $('msg').textContent = 'This browser lacks DecompressionStream; rebuild the viewer with --no-gzip.'; return;
}
$('msg').textContent = 'decoding…';
{ const jobs = [];
  const fix = (o, k) => { if (o && typeof o[k] === 'string') jobs.push(unpack(o[k]).then(u => { o[k] = u; })); };
  for (const r of D.runs) { fix(r, 'base'); fix(r, 'tri'); if (r.ctx) { fix(r.ctx, 'pos'); fix(r.ctx, 'idx'); }
    for (const f of r.frames) ['d', 'v', 'e', 'hide'].forEach(k => fix(f, k)); }
  await Promise.all(jobs); }
$('msg').textContent = '';
function dec(s, T){ const u = typeof s === 'string' ? raw64(s) : s; return new T(u.buffer, u.byteOffset, u.byteLength / T.BYTES_PER_ELEMENT); }
function jet(t){ t = Math.min(1, Math.max(0, t)); const c = x => Math.min(1, Math.max(0, x));
  return [c(1.5 - Math.abs(4*t - 3)), c(1.5 - Math.abs(4*t - 2)), c(1.5 - Math.abs(4*t - 1))]; }
const fmtPa = v => (v === 0 ? '0' : v.toExponential(2));
const fmtE = v => (Math.abs(v) >= 1e-3 || v === 0 ? (+v.toPrecision(3)).toString() : v.toExponential(2));
const view = $('view'), bar = $('bar'), canvas = $('gl');
let renderer;
try { renderer = new THREE.WebGLRenderer({canvas, antialias: true, preserveDrawingBuffer: true}); }
catch (e) { $('msg').textContent = 'WebGL not available: ' + e; return; }
renderer.setPixelRatio(window.devicePixelRatio || 1);
renderer.setClearColor(0xffffff, 1);
renderer.setScissorTest(true);
const runs = D.runs;
const nF = Math.max(...runs.map(r => r.frames.length));
// ---------- geometry per run
const bbmin = [Infinity, Infinity, Infinity], bbmax = [-Infinity, -Infinity, -Infinity];
runs.forEach((r, k) => {
  r.basePos = dec(r.base, Float32Array);
  r.idx = dec(r.tri, Uint32Array);
  r.cache = {};
  const g = new THREE.BufferGeometry();
  r.pos = new Float32Array(r.basePos);
  r.col = new Float32Array(r.nv * 3);
  g.setAttribute('position', new THREE.BufferAttribute(r.pos, 3));
  g.setAttribute('color', new THREE.BufferAttribute(r.col, 3));
  g.setIndex(new THREE.BufferAttribute(r.idx, 1));
  r.geom = g;
  r.scene = new THREE.Scene();
  r.mesh = new THREE.Mesh(g, new THREE.MeshLambertMaterial({vertexColors: true, side: THREE.DoubleSide}));
  r.scene.add(r.mesh);
  r.scene.add(new THREE.AmbientLight(0xffffff, 0.78));
  r.light = new THREE.DirectionalLight(0xffffff, 0.4); r.scene.add(r.light);
  if (r.ctx) {
    const cg = new THREE.BufferGeometry();
    cg.setAttribute('position', new THREE.BufferAttribute(dec(r.ctx.pos, Float32Array), 3));
    cg.setIndex(new THREE.BufferAttribute(dec(r.ctx.idx, Uint32Array), 1));
    cg.computeVertexNormals();
    r.ctxMesh = new THREE.Mesh(cg, new THREE.MeshLambertMaterial({color: 0xb0b0b0, transparent: true, opacity: 0.28,
      side: THREE.DoubleSide, depthWrite: false}));
    r.scene.add(r.ctxMesh);
  }
  for (let a = 0; a < 3; a++) { bbmin[a] = Math.min(bbmin[a], r.bbox[0][a]); bbmax[a] = Math.max(bbmax[a], r.bbox[1][a]); }
  // overlay
  const ov = document.createElement('div'); ov.className = 'ov';
  ov.innerHTML = '<div class="hd"></div><canvas width="320" height="60"></canvas>';
  view.appendChild(ov); r.ov = ov; r.hd = ov.querySelector('.hd'); r.cb = ov.querySelector('canvas');
});
// ---------- camera, shared by all viewports
const ext = [0, 1, 2].map(a => bbmax[a] - bbmin[a]);
const center = new THREE.Vector3((bbmin[0]+bbmax[0])/2, (bbmin[1]+bbmax[1])/2, (bbmin[2]+bbmax[2])/2);
const radius = Math.max(0.5 * Math.hypot(ext[0], ext[1], ext[2]), 1e-9);
const upAxis = ext[2] >= Math.max(ext[0], ext[1]) * 0.999 ? 2 : 1;     // long axis vertical (fork: Z)
const camera = new THREE.PerspectiveCamera(30, 1, radius / 100, radius * 100);
camera.up.set(upAxis === 0 ? 1 : 0, upAxis === 1 ? 1 : 0, upAxis === 2 ? 1 : 0);
function homePos(){
  const asp = layout.length ? Math.min(...layout.map(L => L.w / Math.max(1, L.h))) : 1.6;
  const vf = 30 * Math.PI / 180, hf = 2 * Math.atan(Math.tan(vf / 2) * asp);
  const d = 1.08 * radius / Math.sin(Math.min(vf, hf) / 2), az = 35 * Math.PI/180, el = 18 * Math.PI/180;
  if (upAxis === 2) return new THREE.Vector3(center.x + d*Math.cos(el)*Math.cos(az), center.y - d*Math.cos(el)*Math.sin(az), center.z + d*Math.sin(el));
  return new THREE.Vector3(center.x + d*Math.cos(el)*Math.sin(az), center.y + d*Math.sin(el), center.z + d*Math.cos(el)*Math.cos(az));
}
const controls = new THREE.OrbitControls(camera, canvas);
controls.enableDamping = false; controls.screenSpacePanning = true;
function resetView(){ camera.position.copy(homePos()); controls.target.copy(center); camera.lookAt(center); controls.update(); }
// ---------- UI state
const S = {frame: 0, mode: runs.length > 1 ? 'all' : '0', field: 'v', scale: 'shared', playing: false, speed: 1, floor: true};
runs.forEach((r, k) => { const o = document.createElement('option'); o.value = String(k); o.textContent = r.label; $('mode').appendChild(o); });
if (runs.length > 1) { const o = document.createElement('option'); o.value = 'all'; o.textContent = 'All side-by-side'; $('mode').insertBefore(o, $('mode').firstChild); }
if (!runs.some(r => r.has_e)) $('field').querySelector('option[value=e]').disabled = true;
if (!runs.some(r => r.has_v)) { $('field').value = 'e'; S.field = 'e'; }
// hash params (for scripted screenshots): #mode=all&frame=-1&field=e&scale=per
for (const kv of location.hash.replace(/^#/, '').split('&')) {
  const [k, v] = kv.split('='); if (!k) continue;
  if (k === 'mode') S.mode = v; if (k === 'field') S.field = v; if (k === 'scale') S.scale = v;
  if (k === 'frame') S.frame = (+v < 0 ? nF + (+v) : +v);
}
$('mode').value = S.mode; $('field').value = S.field; $('scale').value = S.scale;
const sl = $('slider'); sl.max = nF - 1;
runs.forEach(r => { if (r.buckling && r.buckling.frame != null) { const o = document.createElement('option'); o.value = r.buckling.frame; $('bkticks').appendChild(o); } });
if (!runs.some(r => r.buckling && r.buckling.frame != null)) $('bkbtn').style.display = 'none';
const visible = () => S.mode === 'all' ? runs.map((_, k) => k) : [+S.mode];
// ---------- per-frame data
function frameData(r, i){
  if (r.cache[i]) return r.cache[i];
  const f = r.frames[i];
  const o = {d: dec(f.d, Int16Array), v: f.v ? dec(f.v, Uint16Array) : null, e: f.e ? dec(f.e, Uint16Array) : null, hide: null};
  if (f.hide) {
    const bits = dec(f.hide, Uint8Array), keep = [];
    for (let t = 0; t < r.nt; t++) if (!((bits[t >> 3] >> (t & 7)) & 1)) keep.push(r.idx[3*t], r.idx[3*t+1], r.idx[3*t+2]);
    o.hide = new Uint32Array(keep);
  }
  const keys = Object.keys(r.cache); if (keys.length > 12) delete r.cache[keys[0]];
  return (r.cache[i] = o);
}
function clim(r){
  const key = S.field === 'v' ? 'vrob' : 'erob';
  if (S.scale === 'per') return r[key] || 1;
  return Math.max(...runs.map(x => x[key] || 0)) || 1;
}
function drawColorbar(r, hi){
  const c = r.cb, g = c.getContext('2d'); const W = c.width, x0 = 26, x1 = W - 26;
  g.clearRect(0, 0, W, c.height);
  for (let x = x0; x <= x1; x++) { const [R, G, B] = jet((x - x0) / (x1 - x0)); g.fillStyle = `rgb(${R*255|0},${G*255|0},${B*255|0})`; g.fillRect(x, 18, 1, 14); }
  g.strokeStyle = '#333'; g.strokeRect(x0, 18, x1 - x0, 14);
  g.fillStyle = '#111'; g.font = '11px sans-serif'; g.textAlign = 'center';
  g.fillText((S.field === 'v' ? 'von Mises stress (' + r.stress_unit + ')' : 'plastic strain (-)') + (S.scale === 'per' ? ', per-material scale' : ', shared scale'), W/2, 12);
  const fmt = S.field === 'v' ? fmtPa : fmtE;
  for (let k = 0; k <= 4; k++) { const x = x0 + (x1 - x0) * k / 4; g.fillRect(x, 32, 1, 4); g.fillText(fmt(hi * k / 4), x, 48); }
}
function updateRun(r){
  const n = r.frames.length, i = Math.min(S.frame, n - 1), f = r.frames[i], o = frameData(r, i);
  const ds = f.ds, b = r.basePos, p = r.pos;
  for (let j = 0; j < p.length; j++) p[j] = b[j] + o.d[j] * ds;
  r.geom.attributes.position.needsUpdate = true;
  r.geom.setIndex(new THREE.BufferAttribute(o.hide || r.idx, 1));
  r.geom.computeVertexNormals();
  const q = S.field === 'v' ? o.v : o.e, sc = S.field === 'v' ? f.vs : f.es, hi = clim(r), col = r.col;
  for (let j = 0; j < r.nv; j++) { const [R, G, B] = q ? jet(q[j] * sc / hi) : [0.75, 0.75, 0.75]; col[3*j] = R; col[3*j+1] = G; col[3*j+2] = B; }
  r.geom.attributes.color.needsUpdate = true;
  r.geom.computeBoundingSphere();
  if (r.ctxMesh) r.ctxMesh.visible = S.floor;
  const curMax = (S.field === 'v' ? f.vs : f.es) * 65535;
  const tl = f.td != null ? `equivalent load time = ${f.td.toFixed(2)} s` : (f.t != null ? `t = ${(+f.t).toPrecision(4)} ${r.time_unit}` : '');
  const ll = f.load != null ? `${r.load_kind || 'load'} = ${f.load.toFixed(f.load < 10 ? 2 : 0)} N` : 'load: n/a';
  let bk = '';
  if (r.buckling) bk = r.buckling.frame != null ? `<div class="bk">buckling: ${r.buckling.text} at frame ${r.frame_numbers[r.buckling.frame] || r.buckling.frame + 1}` +
      (r.buckling.time_display != null && D.scaled_time ? ` (${r.buckling.time_display.toFixed(2)} s)` : '') + '</div>'
      : `<div>${r.buckling.text}</div>`;
  const ended = S.frame > n - 1 ? ' <i>(run data ends here)</i>' : '';
  r.hd.innerHTML = `<b>${r.label}</b>${ended}<br>${tl}<br>${ll}<br>` +
    `frame max: ${S.field === 'v' ? fmtPa(curMax) + ' Pa' : fmtE(curMax)}` + (f.ner ? `<br>eroded elements: ${f.ner}` : '') + bk;
  r.ov.classList.toggle('atbk', !!(r.buckling && r.buckling.frame === i));
  drawColorbar(r, hi);
}
let layout = [];
function doLayout(){
  const top = bar.offsetHeight; view.style.top = top + 'px';
  const W = view.clientWidth, H = view.clientHeight;
  renderer.setSize(W, H, false); canvas.style.width = W + 'px'; canvas.style.height = H + 'px';
  const vis = visible(); const w = W / vis.length;
  layout = vis.map((k, n) => ({k, x: Math.round(n * w), w: Math.round(w), h: H}));
  runs.forEach((r, k) => { r.ov.style.display = vis.includes(k) ? 'block' : 'none'; });
  layout.forEach(L => { const s = runs[L.k].ov.style; s.left = L.x + 'px'; s.top = '0px'; s.width = L.w + 'px'; s.height = L.h + 'px';
    runs[L.k].cb.width = Math.min(340, Math.max(180, L.w - 20)); });
}
function update(){
  sl.value = S.frame;
  const r0 = runs[visible()[0]], i0 = Math.min(S.frame, r0.frames.length - 1);
  $('fl').textContent = `frame ${S.frame + 1}/${nF}`;
  visible().forEach(k => updateRun(runs[k]));
  $('msg').textContent = (D.note ? D.note + '  ' : '') + 'drag: rotate, right-drag/shift: pan, wheel: zoom' +
    (D.comparison ? '  | buckling from ' + D.comparison.split('/').pop() : '');
}
function render(){
  const H = view.clientHeight;
  layout.forEach(L => {
    const r = runs[L.k];
    renderer.setViewport(L.x, 0, L.w, H); renderer.setScissor(L.x, 0, L.w, H);
    camera.aspect = L.w / H; camera.updateProjectionMatrix();
    r.light.position.copy(camera.position);
    renderer.render(r.scene, camera);
  });
}
// ---------- events
$('mode').onchange = e => { S.mode = e.target.value; doLayout(); update(); resetView(); };
$('field').onchange = e => { S.field = e.target.value; update(); };
$('scale').onchange = e => { S.scale = e.target.value; update(); };
$('floor').onchange = e => { S.floor = e.target.checked; update(); };
sl.oninput = e => { S.frame = +e.target.value; update(); };
$('prev').onclick = () => { S.frame = (S.frame - 1 + nF) % nF; update(); };
$('next').onclick = () => { S.frame = (S.frame + 1) % nF; update(); };
$('speed').onchange = e => { S.speed = +e.target.value; };
$('reset').onclick = resetView;
$('bkbtn').onclick = () => { const r = runs[visible().find(k => runs[k].buckling && runs[k].buckling.frame != null) ?? runs.findIndex(x => x.buckling && x.buckling.frame != null)];
  if (r) { S.frame = r.buckling.frame; update(); } };
$('play').onclick = () => { S.playing = !S.playing; $('play').textContent = S.playing ? 'Pause' : 'Play'; last = performance.now(); };
window.addEventListener('keydown', e => { if (e.key === 'ArrowRight') $('next').onclick(); if (e.key === 'ArrowLeft') $('prev').onclick(); if (e.key === ' ') { e.preventDefault(); $('play').onclick(); } });
window.addEventListener('resize', () => { doLayout(); update(); });
let last = performance.now();
function loop(now){
  requestAnimationFrame(loop);
  if (S.playing && now - last > 1000 / (D.fps * S.speed)) { last = now; S.frame = (S.frame + 1) % nF; update(); }
  controls.update(); render();
}
doLayout(); resetView(); update(); requestAnimationFrame(loop);
window.__viewerReady = true;
})();
"""

if __name__ == "__main__":
    main()
