"""Animated glTF 2.0 (.glb) export: one mesh, one morph target per frame, step-interpolated weights animation.

Contract (the SimStudio web viewer is coded against this):
- One mesh / one primitive (TRIANGLES). The outer surface is built ONCE from frame 1 of the structural cells
  (stressed in some frame, active at t=0, 2D/3D). Vertices are duplicated per (element, node) so the faces of one
  element can collapse independently of their neighbours.
- POSITION = frame-1 coordinates. morph target k = POSITION delta of frame k (target 0 is all zeros).
  Elements eroded in frame k (EROSION_STATUS 0, active at t=0) have all their vertices morphed onto the element's
  centroid, so their faces become degenerate (invisible). Vertices of live elements never move with them.
- Animation "frames": weights channel, STEP interpolation, keyframe k at times[k] (load_time s if given, else
  solver time) with a one-hot weights vector (target k = 1).
- COLOR_0 (float VEC3): jet (blue = low -> red = high) of von Mises for the LAST frame, on one fixed global range
  [vmin, vmax] over all frames. glTF cannot animate vertex colours, so the web view recolours per frame from:
- _VON_MISES (last frame) and _VON_MISES_0000 ... _VON_MISES_NNNN (float32 SCALAR, Pa, one per morph target),
  element values averaged to the nodes over adjacent live structural elements. Same for _PLASTIC_STRAIN[_####]
  when the field exists. three.js GLTFLoader lower-cases custom names: geometry.attributes._von_mises_0003.
- mesh.extras and the sidecar manifest.json hold frames [{index, time, load_time, load_N, eroded}], times,
  ranges, units, colormap and the attribute names.
- Coordinates match geometry/ (HexMesh.write_glb, model.glb): metres, Y-up, gltf = (x, z, -y)_model * length_to_m.
  (x,z,-y) is a proper rotation (det +1, -90 deg about x), so winding/handedness are preserved; the solver's -Z
  (push / gravity) points to three.js -Y. extras: length_unit "m", up_axis "Y", source_to_gltf "(x,z,-y)*scale", scale.
- No NORMAL attribute: three.js then uses flat shading, which stays correct under morphing.
"""
import json
import os
import struct as _st

import numpy as np

from ._vendor import postprocess as pp

VM_RE, EPSP_RE = pp.FIELD_PRIORITY[0][1], pp.FIELD_PRIORITY[1][1]
CELL_DIM = {5: 2, 7: 2, 8: 2, 9: 2, 10: 3, 11: 3, 12: 3, 13: 3, 14: 3, 22: 2, 23: 2, 24: 3, 25: 3}
FLOAT, UINT32 = 5126, 5125
ARRAY_BUFFER, ELEMENT_ARRAY_BUFFER = 34962, 34963


def jet(t):
    """Same colormap as the HTML viewer: 0 -> dark blue (0,0,.5), 1 -> dark red (.5,0,0)."""
    t = np.clip(np.asarray(t, float), 0, 1)[..., None]
    c = np.clip(1.5 - np.abs(4 * t - np.array([3.0, 2.0, 1.0])), 0, 1)
    return c.astype(np.float32)


def to_gltf_coords(p, scale=1.0):
    """Model coords (Z-up, deck length unit) -> glTF (metres, Y-up): (x, z, -y) * scale. Same as geometry.HexMesh.write_glb."""
    p = np.asarray(p, np.float64)
    return np.stack([p[..., 0], p[..., 2], -p[..., 1]], -1) * scale


def length_scale(units):
    """Model length unit -> metres from a postprocess.deck_units dict (only the length token is needed)."""
    l = (units or {}).get("length")
    return (pp.LEN_SI[l], True) if l in pp.LEN_SI else (1.0, False)


def _cell_field(m, rx):
    a, loc, names = pp.merged_field(m, rx)
    return np.nan_to_num(np.asarray(a, float)) if names and loc == "cell" else None


def build_surface(m0, struct):
    """Outer surface of the struct cells of m0 -> (tri (nt,3) vertex ids, vert_point (nv) m0 point ids,
    vert_cell (nv) m0 cell ids). Vertices are unique per (cell, point)."""
    idx = np.where(struct)[0]
    sub = m0.extract_cells(idx)
    sub.point_data["_gp"] = np.asarray(sub.point_data["vtkOriginalPointIds"], np.int64)
    sub.cell_data["_gc"] = np.asarray(sub.cell_data["vtkOriginalCellIds"], np.int64)
    surf = sub.extract_surface(pass_pointid=False, pass_cellid=False, algorithm="dataset_surface").triangulate()
    gp = np.asarray(surf.point_data["_gp"], np.int64)
    gc = np.asarray(surf.cell_data["_gc"], np.int64)
    tri_p = gp[surf.faces.reshape(-1, 4)[:, 1:]]            # (nt,3) m0 point ids
    keys = np.stack([np.repeat(gc, 3), tri_p.ravel()], 1)    # (cell, point) per corner
    uk, inv = np.unique(keys, axis=0, return_inverse=True)
    return inv.reshape(-1, 3).astype(np.uint32), uk[:, 1], uk[:, 0]


def _point_avg(cell_vals, conn_cells, conn_pts, alive, npts):
    """Average element values onto nodes over adjacent alive cells. conn_*: flattened (cell, point) incidence."""
    w = alive[conn_cells].astype(float)
    s = np.bincount(conn_pts, weights=cell_vals[conn_cells] * w, minlength=npts)
    n = np.bincount(conn_pts, weights=w, minlength=npts)
    return np.where(n > 0, s / np.maximum(n, 1), 0.0)


def _incidence(m0, struct):
    """Flattened (cell id, point id) pairs of the struct cells of an UnstructuredGrid."""
    off = np.asarray(m0.cell_offsets, np.int64)
    conn = np.asarray(m0.cell_connectivity, np.int64)
    sizes = np.diff(off)
    cells = np.repeat(np.arange(m0.n_cells), sizes)
    keep = struct[cells]
    return cells[keep], conn[keep]


def frames_to_glb(vtk_files, out_glb, *, times=None, load_times=None, loads=None, stress_unit="Pa",
                  stress_fac=1.0, cmap="jet", name="run", max_frames=None, manifest=True, length_to_m=1.0,
                  length_known=True):
    """Write the animated GLB (and <dir>/manifest.json). vtk_files: frame VTKs (stress already in Pa unless
    stress_fac given). times/load_times/loads: per-frame lists (None entries allowed). length_to_m: deck length unit
    -> m (mm decks 1e-3); coordinates are written as (x, z, -y) * length_to_m. Returns the manifest dict."""
    import pyvista as pv
    if cmap != "jet":
        raise ValueError("only cmap='jet' is baked into COLOR_0")
    files = list(vtk_files)
    nF0 = len(files)
    keep = list(range(nF0))
    if max_frames and nF0 > max_frames:
        keep = sorted(set(np.round(np.linspace(0, nF0 - 1, max_frames)).astype(int).tolist()))
    meshes = []
    for k in keep:
        m = pv.read(files[k])
        if meshes and (m.n_points != meshes[0].n_points or m.n_cells != meshes[0].n_cells):
            break
        meshes.append(m)
    keep = keep[:len(meshes)]
    m0 = meshes[0]
    nc = m0.n_cells
    vm = [_cell_field(m, VM_RE) for m in meshes]
    vm = [None if v is None else v * stress_fac for v in vm]
    ep = [_cell_field(m, EPSP_RE) for m in meshes]
    es = [np.asarray(m.cell_data["EROSION_STATUS"]) if "EROSION_STATUS" in m.cell_data.keys() else np.ones(nc)
          for m in meshes]
    es0 = es[0]
    dim = np.array([CELL_DIM.get(int(c), 0) for c in np.asarray(m0.celltypes)])
    live = np.zeros(nc, bool)
    for v in vm:
        if v is not None:
            live |= v > 0
    if not live.any():
        live[:] = True
    struct = live & (es0 != 0) & (dim >= 2)     # rigid caps / bodies inactive from t=0 are excluded
    if "ELEMENT_ID" in m0.cell_data.keys():     # OpenRadioss elements only (drops floor / helper cells with id 0)
        struct &= np.asarray(m0.cell_data["ELEMENT_ID"]) > 0
    if not struct.any():
        raise ValueError("no structural cells")
    tri, vpt, vcell = build_surface(m0, struct)
    nv, nt, nF = len(vpt), len(tri), len(meshes)
    ic, ip = _incidence(m0, struct)
    base = to_gltf_coords(np.asarray(m0.points)[vpt], length_to_m)

    targets, vmv, epv, eroded = [], [], [], []
    for k, m in enumerate(meshes):
        dead = (es[k] == 0) & (es0 != 0)
        alive = struct & ~dead
        pos = to_gltf_coords(np.asarray(m.points)[vpt], length_to_m)
        dv = dead[vcell]
        if dv.any():   # collapse each dead element's vertices onto its own centroid
            cells = vcell[dv]
            uc, inv = np.unique(cells, return_inverse=True)
            cen = np.zeros((len(uc), 3))
            np.add.at(cen, inv, pos[dv])
            cen /= np.bincount(inv)[:, None]
            pos[dv] = cen[inv]
        targets.append((pos - base).astype(np.float32))
        eroded.append(int((dead & struct).sum()))
        for arr, out in ((vm[k], vmv), (ep[k], epv)):
            if arr is not None:
                out.append(_point_avg(arr, ic, ip, alive, m0.n_points)[vpt].astype(np.float32))
            else:
                out.append(None)
    has_vm, has_ep = all(v is not None for v in vmv), all(v is not None for v in epv)
    rng = lambda L: [float(min(x.min() for x in L)), float(max(x.max() for x in L))] if L else None
    vr = rng(vmv) if has_vm else None
    er = rng(epv) if has_ep else None
    if has_vm:
        span = (vr[1] - vr[0]) or 1.0
        color = jet((vmv[-1] - vr[0]) / span)
    else:
        color = np.full((nv, 3), 0.75, np.float32)

    # ---- timeline
    def pick(L, k):
        return None if L is None or L[k] is None else float(L[k])
    tsel = [pick(times, k) for k in keep]
    lsel = [pick(load_times, k) for k in keep]
    use_load = all(x is not None for x in lsel)
    tl = lsel if use_load else [x if x is not None else float(i) for i, x in enumerate(tsel)]
    anim_t = []
    for x in tl:   # glTF needs strictly increasing keyframe times
        anim_t.append(x if not anim_t or x > anim_t[-1] else np.nextafter(np.float32(anim_t[-1]), np.float32(np.inf)))
    anim_t = np.asarray(anim_t, np.float32)
    frames_meta = [{"index": i, "source_frame": keep[i] + 1, "time": tsel[i], "load_time": lsel[i],
                    "anim_time": float(anim_t[i]), "load_N": pick(loads, keep[i]), "eroded": eroded[i]}
                   for i in range(nF)]

    # ---- binary buffer
    blob, views, accs = bytearray(), [], []

    def add(arr, comp, typ, target=None, minmax=False):
        arr = np.ascontiguousarray(arr)
        while len(blob) % 4:
            blob.append(0)
        off = len(blob); blob.extend(arr.tobytes())
        v = {"buffer": 0, "byteOffset": off, "byteLength": arr.nbytes}
        if target:
            v["target"] = target
        views.append(v)
        a = {"bufferView": len(views) - 1, "componentType": comp, "count": int(arr.shape[0]), "type": typ}
        if minmax:
            a2 = arr.reshape(arr.shape[0], -1)
            a["min"] = [float(x) for x in a2.min(0)]; a["max"] = [float(x) for x in a2.max(0)]
        accs.append(a)
        return len(accs) - 1

    attrs = {"POSITION": add(base.astype(np.float32), FLOAT, "VEC3", ARRAY_BUFFER, True),
             "COLOR_0": add(color, FLOAT, "VEC3", ARRAY_BUFFER)}
    if has_vm:
        attrs["_VON_MISES"] = add(vmv[-1], FLOAT, "SCALAR", ARRAY_BUFFER)
        for k in range(nF):
            attrs[f"_VON_MISES_{k:04d}"] = add(vmv[k], FLOAT, "SCALAR", ARRAY_BUFFER)
    if has_ep:
        attrs["_PLASTIC_STRAIN"] = add(epv[-1], FLOAT, "SCALAR", ARRAY_BUFFER)
        for k in range(nF):
            attrs[f"_PLASTIC_STRAIN_{k:04d}"] = add(epv[k], FLOAT, "SCALAR", ARRAY_BUFFER)
    tgts = [{"POSITION": add(d, FLOAT, "VEC3", ARRAY_BUFFER, True)} for d in targets]
    ind = add(tri.ravel().astype(np.uint32), UINT32, "SCALAR", ELEMENT_ARRAY_BUFFER)
    t_acc = add(anim_t, FLOAT, "SCALAR", minmax=True)
    w_acc = add(np.eye(nF, dtype=np.float32).ravel(), FLOAT, "SCALAR")

    extras = {"format": "simstudio-morph-v1", "name": name, "n_frames": nF, "n_frames_source": nF0,
              "frames": frames_meta, "times": [f["anim_time"] for f in frames_meta],
              "time_axis": "load_time (s)" if use_load else "solver time",
              "length_unit": "m" if length_known else "model units", "up_axis": "Y",
              "source_to_gltf": "(x,z,-y)*scale", "scale": float(length_to_m),
              "stress_unit": stress_unit, "colormap": cmap, "color_frame": nF - 1,
              "color_attribute": "COLOR_0 = jet(von Mises of last frame) on von_mises_range (glTF cannot animate colors)",
              "von_mises_range": vr, "plastic_strain_range": er,
              "von_mises_attributes": [f"_VON_MISES_{k:04d}" for k in range(nF)] if has_vm else [],
              "plastic_strain_attributes": [f"_PLASTIC_STRAIN_{k:04d}" for k in range(nF)] if has_ep else [],
              "erosion": "eroded elements' vertices are morphed onto their centroid (degenerate faces)",
              "n_vertices": int(nv), "n_triangles": int(nt), "n_structural_cells": int(struct.sum()),
              "inactive_at_t0_skipped": int((es0 == 0).sum())}
    gl = {"asset": {"version": "2.0", "generator": "simstudio post.gltf"},
          "scene": 0, "scenes": [{"nodes": [0]}], "nodes": [{"mesh": 0, "name": name}],
          "meshes": [{"name": name, "weights": [0.0] * nF, "extras": {**extras, "targetNames": [f"frame_{k:04d}" for k in range(nF)]},
                      "primitives": [{"attributes": attrs, "indices": ind, "material": 0, "mode": 4, "targets": tgts}]}],
          "materials": [{"name": "jet", "doubleSided": True,
                         "pbrMetallicRoughness": {"baseColorFactor": [1, 1, 1, 1], "metallicFactor": 0.0, "roughnessFactor": 0.8}}],
          "animations": [{"name": "frames", "samplers": [{"input": t_acc, "output": w_acc, "interpolation": "STEP"}],
                          "channels": [{"sampler": 0, "target": {"node": 0, "path": "weights"}}]}],
          "accessors": accs, "bufferViews": views, "buffers": [{"byteLength": len(blob)}]}
    while len(blob) % 4:
        blob.append(0)
    gl["buffers"][0]["byteLength"] = len(blob)
    js = json.dumps(gl, separators=(",", ":")).encode()
    js += b" " * ((4 - len(js) % 4) % 4)
    os.makedirs(os.path.dirname(os.path.abspath(out_glb)), exist_ok=True)
    with open(out_glb, "wb") as f:
        f.write(_st.pack("<III", 0x46546C67, 2, 12 + 8 + len(js) + 8 + len(blob)))
        f.write(_st.pack("<II", len(js), 0x4E4F534A)); f.write(js)
        f.write(_st.pack("<II", len(blob), 0x004E4942)); f.write(bytes(blob))
    man = {"glb": os.path.basename(out_glb), "bytes": os.path.getsize(out_glb), **extras}
    if manifest:
        with open(os.path.join(os.path.dirname(os.path.abspath(out_glb)), "manifest.json"), "w") as f:
            json.dump(man, f, indent=2)
    return man


def read_glb(path):
    """Minimal reader (tests / tooling): returns (gltf json, {accessor index: numpy array})."""
    data = open(path, "rb").read()
    magic, ver, total = _st.unpack_from("<III", data, 0)
    assert magic == 0x46546C67 and ver == 2 and total == len(data)
    jl, _ = _st.unpack_from("<II", data, 12)
    gl = json.loads(data[20:20 + jl])
    bl, _ = _st.unpack_from("<II", data, 20 + jl)
    blob = data[28 + jl:28 + jl + bl]
    dt = {FLOAT: np.float32, UINT32: np.uint32}
    ncomp = {"SCALAR": 1, "VEC3": 3, "VEC4": 4}
    arrs = {}
    for i, a in enumerate(gl["accessors"]):
        v = gl["bufferViews"][a["bufferView"]]
        x = np.frombuffer(blob, dt[a["componentType"]], a["count"] * ncomp[a["type"]], v["byteOffset"])
        arrs[i] = x.reshape(a["count"], -1) if ncomp[a["type"]] > 1 else x
    return gl, arrs
