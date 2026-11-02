"""Hex mesh container and writers (Radioss include, STL, VTK). Units: mm."""
from __future__ import annotations
from dataclasses import dataclass, field
import struct
import numpy as np

# Six-tet split of a hex around diagonal 0-6 (VTK / Radioss node order) for volume checks.
_TETS = [(0, 1, 2, 6), (0, 2, 3, 6), (0, 3, 7, 6), (0, 7, 4, 6), (0, 4, 5, 6), (0, 5, 1, 6)]
_FACES = [(0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)]


@dataclass
class HexMesh:
    nodes: np.ndarray            # (N, 3) float, mm
    elems: np.ndarray            # (M, 8) int, 0-based node indices, VTK hexahedron order
    part_id: int = 1
    meta: dict = field(default_factory=dict)

    # ---------- checks ----------
    def element_volumes(self) -> np.ndarray:
        p = self.nodes[self.elems]
        v = np.zeros(len(self.elems))
        for a, b, c, d in _TETS:
            v += np.einsum("ij,ij->i", p[:, b] - p[:, a], np.cross(p[:, c] - p[:, a], p[:, d] - p[:, a])) / 6.0
        return v

    def fix_orientation(self) -> int:
        """Flip inverted bricks (swap bottom/top faces). Returns how many were flipped."""
        neg = self.element_volumes() < 0
        if neg.any():
            e = self.elems[neg]
            self.elems[neg] = np.concatenate([e[:, 4:], e[:, :4]], axis=1)
        return int(neg.sum())

    def boundary_quads(self) -> np.ndarray:
        q = self.elems[:, _FACES].reshape(-1, 4)
        key = np.sort(q, axis=1)
        _, inv, cnt = np.unique(key, axis=0, return_inverse=True, return_counts=True)
        return q[cnt[inv.ravel()] == 1]

    def stats(self) -> dict:
        v = self.element_volumes()
        return {
            "nodes": int(len(self.nodes)), "bricks": int(len(self.elems)),
            "volume_mm3": float(v.sum()), "min_brick_volume_mm3": float(v.min()) if len(v) else 0.0,
            "inverted_bricks": int((v <= 0).sum()),
            "bbox_min_mm": self.nodes.min(0).round(4).tolist(), "bbox_max_mm": self.nodes.max(0).round(4).tolist(),
        }

    # ---------- transforms ----------
    def translate(self, d):
        self.nodes = self.nodes + np.asarray(d, float); return self

    def place_on_floor(self, axis: int = 2, gap: float = 1.0, downward: bool = True):
        """Shift so the lowest point along `axis` sits `gap` mm above 0 (contact surface at 0)."""
        self.nodes[:, axis] += gap - self.nodes[:, axis].min(); return self

    # ---------- writers ----------
    def write_radioss_inc(self, path: str, node_offset: int = 0, elem_offset: int = 0, title: str = "mesh"):
        with open(path, "w") as f:
            f.write(f"#--- {title}. Units mm. Part {self.part_id}\n/NODE\n")
            for i, (x, y, z) in enumerate(self.nodes, 1 + node_offset):
                f.write(f"{i:10d}{x:20.6f}{y:20.6f}{z:20.6f}\n")
            f.write(f"/BRICK/{self.part_id}\n")
            for i, e in enumerate(self.elems + 1 + node_offset, 1 + elem_offset):
                f.write(f"{i:10d}" + "".join(f"{n:10d}" for n in e) + "\n")

    def write_stl(self, path: str):
        quads = self.boundary_quads()
        tris = np.concatenate([quads[:, [0, 1, 2]], quads[:, [0, 2, 3]]])
        p = self.nodes[tris]
        n = np.cross(p[:, 1] - p[:, 0], p[:, 2] - p[:, 0])
        n /= np.maximum(np.linalg.norm(n, axis=1, keepdims=True), 1e-12)
        with open(path, "wb") as f:
            f.write(b"simstudio geometry".ljust(80, b" "))
            f.write(struct.pack("<I", len(tris)))
            rec = np.zeros(len(tris), dtype=[("n", "<f4", 3), ("v", "<f4", (3, 3)), ("a", "<u2")])
            rec["n"], rec["v"] = n, p
            f.write(rec.tobytes())

    def write_vtk(self, path: str, cell_data: dict | None = None):
        with open(path, "w") as f:
            f.write("# vtk DataFile Version 3.0\nsimstudio geometry\nASCII\nDATASET UNSTRUCTURED_GRID\n")
            f.write(f"POINTS {len(self.nodes)} float\n")
            np.savetxt(f, self.nodes, fmt="%.6f")
            f.write(f"CELLS {len(self.elems)} {9 * len(self.elems)}\n")
            np.savetxt(f, np.hstack([np.full((len(self.elems), 1), 8), self.elems]), fmt="%d")
            f.write(f"CELL_TYPES {len(self.elems)}\n")
            f.write("12\n" * len(self.elems))
            if cell_data:
                f.write(f"CELL_DATA {len(self.elems)}\n")
                for k, arr in cell_data.items():
                    f.write(f"SCALARS {k} float 1\nLOOKUP_TABLE default\n")
                    np.savetxt(f, np.asarray(arr, float), fmt="%.6f")

    def write_glb(self, path: str, rgb=(0.75, 0.77, 0.8)):
        """Binary glTF 2.0 of the outer surface (meters, Y-up per glTF spec) for the web preview."""
        import json
        quads = self.boundary_quads()
        used, inv = np.unique(quads, return_inverse=True)
        tris = np.concatenate([inv.reshape(-1, 4)[:, [0, 1, 2]], inv.reshape(-1, 4)[:, [0, 2, 3]]]).astype(np.uint32)
        # mm, Z-up  ->  m, Y-up
        pos = (self.nodes[used][:, [0, 2, 1]] * np.array([1, 1, -1]) / 1000.0).astype(np.float32)
        col = np.tile(np.asarray(rgb, np.float32), (len(pos), 1))
        blobs = [pos.tobytes(), col.tobytes(), tris.tobytes()]
        offs = np.cumsum([0] + [len(b) for b in blobs])
        gl = {
            "asset": {"version": "2.0", "generator": "simstudio geometry"},
            "scene": 0, "scenes": [{"nodes": [0]}], "nodes": [{"mesh": 0, "name": "part"}],
            "meshes": [{"primitives": [{"attributes": {"POSITION": 0, "COLOR_0": 1}, "indices": 2,
                                        "material": 0}]}],
            "materials": [{"pbrMetallicRoughness": {"metallicFactor": 0.6, "roughnessFactor": 0.4},
                           "doubleSided": True}],
            "buffers": [{"byteLength": int(offs[-1])}],
            "bufferViews": [{"buffer": 0, "byteOffset": int(offs[i]), "byteLength": len(blobs[i]),
                             "target": 34963 if i == 2 else 34962} for i in range(3)],
            "accessors": [
                {"bufferView": 0, "componentType": 5126, "count": len(pos), "type": "VEC3",
                 "min": pos.min(0).tolist(), "max": pos.max(0).tolist()},
                {"bufferView": 1, "componentType": 5126, "count": len(col), "type": "VEC3"},
                {"bufferView": 2, "componentType": 5125, "count": int(tris.size), "type": "SCALAR"}],
        }
        js = json.dumps(gl, separators=(",", ":")).encode(); js += b" " * (-len(js) % 4)
        bin_ = b"".join(blobs); bin_ += b"\0" * (-len(bin_) % 4)
        with open(path, "wb") as f:
            f.write(struct.pack("<III", 0x46546C67, 2, 12 + 8 + len(js) + 8 + len(bin_)))
            f.write(struct.pack("<II", len(js), 0x4E4F534A) + js)
            f.write(struct.pack("<II", len(bin_), 0x004E4942) + bin_)

    def export_all(self, out_dir: str, stem: str = "model", title: str = "mesh") -> dict:
        import os, json
        os.makedirs(out_dir, exist_ok=True)
        paths = {k: os.path.join(out_dir, f"{stem}.{ext}") for k, ext in
                 [("radioss_inc", "inc"), ("stl", "stl"), ("vtk", "vtk"), ("glb", "glb"), ("summary", "json")]}
        self.write_radioss_inc(paths["radioss_inc"], title=title)
        self.write_stl(paths["stl"])
        self.write_vtk(paths["vtk"])
        self.write_glb(paths["glb"])
        summary = {**self.stats(), "part_id": self.part_id, "meta": self.meta, "files": paths}
        with open(paths["summary"], "w") as f:
            json.dump(summary, f, indent=2)
        return summary


def extrude_columns(mask: np.ndarray, xs: np.ndarray, ys: np.ndarray, z_bot, z_top, nz: int) -> HexMesh:
    """Structured hex mesh from a 2D cell mask (nx, ny) on grid lines xs (nx+1), ys (ny+1).
    z_bot/z_top: callables f(x, y) -> z, or arrays shaped (nx+1, ny+1) sampled at grid nodes."""
    nx, ny = mask.shape
    used = np.zeros((nx + 1, ny + 1), bool)
    I, J = np.nonzero(mask)
    for di in (0, 1):
        for dj in (0, 1):
            used[I + di, J + dj] = True
    X, Y = np.meshgrid(xs, ys, indexing="ij")
    zb = z_bot(X, Y) if callable(z_bot) else np.asarray(z_bot)
    zt = z_top(X, Y) if callable(z_top) else np.asarray(z_top)
    col = -np.ones((nx + 1, ny + 1), int)
    col[used] = np.arange(used.sum())
    ncol = used.sum()
    k = np.arange(nz + 1)[None, :] / nz
    zz = zb[used][:, None] + (zt[used] - zb[used])[:, None] * k
    nodes = np.stack([np.repeat(X[used], nz + 1), np.repeat(Y[used], nz + 1), zz.ravel()], 1)
    nid = lambda c, kk: c * (nz + 1) + kk
    c00, c10, c11, c01 = col[I, J], col[I + 1, J], col[I + 1, J + 1], col[I, J + 1]
    elems = []
    for kk in range(nz):
        elems.append(np.stack([nid(c00, kk), nid(c10, kk), nid(c11, kk), nid(c01, kk),
                               nid(c00, kk + 1), nid(c10, kk + 1), nid(c11, kk + 1), nid(c01, kk + 1)], 1))
    m = HexMesh(nodes=nodes, elems=np.concatenate(elems) if elems else np.zeros((0, 8), int))
    m.fix_orientation()
    return m
