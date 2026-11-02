"""Parse the mesh of an uploaded OpenRadioss starter deck for the 3D input view.

Supports /NODE with /BRICK (8-node hexes), /SHELL (4-node quads; n3 == n4 is a
triangle) and /SH3N (3-node triangles), in free (whitespace) or fixed-width
(10-char ids, 20-char coordinates) format. A brick-only deck still yields a
geometry.HexMesh so its exports are unchanged; any deck with shells yields a
DeckMesh, which writes the same files (model.inc/.stl/.vtk/.glb/.json) with the
same mm -> metres, Z-up -> Y-up mapping as HexMesh.write_glb.
"""

from __future__ import annotations

import json
import os
import re
import struct
from dataclasses import dataclass, field

import numpy as np

from geometry import HexMesh

_SECTION = re.compile(r"^/(NODE|BRICK|SHELL|SH3N)(?:/|\s|$)")
_HEX_FACES = [(0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)]
_NODES_PER = {"brick": 8, "shell": 4, "sh3n": 3}


def _fixed(line: str, widths: list[int]) -> list[str]:
    out, pos = [], 0
    for w in widths:
        out.append(line[pos:pos + w].strip())
        pos += w
    return out


def _parse_node(line: str):
    parts = line.split()
    if len(parts) >= 4:
        try:
            return int(float(parts[0])), float(parts[1]), float(parts[2]), float(parts[3])
        except ValueError:
            pass
    try:
        f = _fixed(line, [10, 20, 20, 20])
        return int(f[0]), float(f[1]), float(f[2]), float(f[3])
    except ValueError:
        return None


def _parse_elem(line: str, n: int):
    parts = line.split()
    if len(parts) >= n + 1:
        try:
            return [int(float(p)) for p in parts[1:n + 1]]
        except ValueError:
            pass
    try:
        f = _fixed(line, [10] * (n + 1))
        return [int(x) for x in f[1:]]
    except ValueError:
        return None


@dataclass
class DeckMesh:
    nodes: np.ndarray                      # (N, 3) float, deck units (mm for SimStudio decks)
    bricks: np.ndarray                     # (B, 8) 0-based
    quads: np.ndarray                      # (Q, 4) 0-based
    tris: np.ndarray                       # (T, 3) 0-based
    meta: dict = field(default_factory=dict)

    def surface_tris(self) -> np.ndarray:
        faces = []
        if len(self.bricks):
            q = self.bricks[:, _HEX_FACES].reshape(-1, 4)
            key = np.sort(q, axis=1)
            _, inv, cnt = np.unique(key, axis=0, return_inverse=True, return_counts=True)
            q = q[cnt[inv.ravel()] == 1]
            faces += [q[:, [0, 1, 2]], q[:, [0, 2, 3]]]
        if len(self.quads):
            faces += [self.quads[:, [0, 1, 2]], self.quads[:, [0, 2, 3]]]
        if len(self.tris):
            faces.append(self.tris)
        t = np.concatenate(faces) if faces else np.zeros((0, 3), int)
        return t[(t[:, 0] != t[:, 1]) & (t[:, 1] != t[:, 2]) & (t[:, 0] != t[:, 2])]

    def stats(self) -> dict:
        return {
            "nodes": int(len(self.nodes)),
            "bricks": int(len(self.bricks)),
            "shells": int(len(self.quads) + len(self.tris)),
            "quad_shells": int(len(self.quads)),
            "tri_shells": int(len(self.tris)),
            "volume_mm3": None,
            "bbox_min_mm": self.nodes.min(0).round(4).tolist(),
            "bbox_max_mm": self.nodes.max(0).round(4).tolist(),
        }

    def write_radioss_inc(self, path: str, title: str = "mesh"):
        with open(path, "w") as f:
            f.write(f"#--- {title}. Mesh read back from the uploaded starter (renumbered).\n/NODE\n")
            for i, (x, y, z) in enumerate(self.nodes, 1):
                f.write(f"{i:10d}{x:20.6f}{y:20.6f}{z:20.6f}\n")
            eid = 1
            for kw, arr in (("BRICK", self.bricks), ("SHELL", self.quads), ("SH3N", self.tris)):
                if len(arr):
                    f.write(f"/{kw}/1\n")
                    for e in arr + 1:
                        f.write(f"{eid:10d}" + "".join(f"{n:10d}" for n in e) + "\n")
                        eid += 1

    def write_stl(self, path: str):
        tris = self.surface_tris()
        p = self.nodes[tris]
        n = np.cross(p[:, 1] - p[:, 0], p[:, 2] - p[:, 0])
        n /= np.maximum(np.linalg.norm(n, axis=1, keepdims=True), 1e-12)
        with open(path, "wb") as f:
            f.write(b"simstudio geometry".ljust(80, b" "))
            f.write(struct.pack("<I", len(tris)))
            rec = np.zeros(len(tris), dtype=[("n", "<f4", 3), ("v", "<f4", (3, 3)), ("a", "<u2")])
            rec["n"], rec["v"] = n, p
            f.write(rec.tobytes())

    def write_vtk(self, path: str):
        groups = [(self.bricks, 12), (self.quads, 9), (self.tris, 5)]
        ncell = sum(len(a) for a, _ in groups)
        size = sum(len(a) * (a.shape[1] + 1) for a, _ in groups if len(a))
        with open(path, "w") as f:
            f.write("# vtk DataFile Version 3.0\nsimstudio geometry\nASCII\nDATASET UNSTRUCTURED_GRID\n")
            f.write(f"POINTS {len(self.nodes)} float\n")
            np.savetxt(f, self.nodes, fmt="%.6f")
            f.write(f"CELLS {ncell} {size}\n")
            for a, _ in groups:
                if len(a):
                    np.savetxt(f, np.hstack([np.full((len(a), 1), a.shape[1]), a]), fmt="%d")
            f.write(f"CELL_TYPES {ncell}\n")
            for a, t in groups:
                f.write(f"{t}\n" * len(a))

    def write_glb(self, path: str, rgb=(0.75, 0.77, 0.8)):
        """Binary glTF of the outer surface: metres, Y-up, (x, z, -y) / 1000 like HexMesh.write_glb."""
        tri = self.surface_tris()
        used, inv = np.unique(tri, return_inverse=True)
        tris = inv.reshape(-1, 3).astype(np.uint32)
        pos = (self.nodes[used][:, [0, 2, 1]] * np.array([1, 1, -1]) / 1000.0).astype(np.float32)
        col = np.tile(np.asarray(rgb, np.float32), (len(pos), 1))
        blobs = [pos.tobytes(), col.tobytes(), tris.tobytes()]
        offs = np.cumsum([0] + [len(b) for b in blobs])
        gl = {
            "asset": {"version": "2.0", "generator": "simstudio deck_mesh"},
            "scene": 0, "scenes": [{"nodes": [0]}], "nodes": [{"mesh": 0, "name": "part"}],
            "meshes": [{"primitives": [{"attributes": {"POSITION": 0, "COLOR_0": 1}, "indices": 2, "material": 0}]}],
            "materials": [{"pbrMetallicRoughness": {"metallicFactor": 0.6, "roughnessFactor": 0.4}, "doubleSided": True}],
            "buffers": [{"byteLength": int(offs[-1])}],
            "bufferViews": [{"buffer": 0, "byteOffset": int(offs[i]), "byteLength": len(blobs[i]),
                             "target": 34963 if i == 2 else 34962} for i in range(3)],
            "accessors": [
                {"bufferView": 0, "componentType": 5126, "count": len(pos), "type": "VEC3",
                 "min": pos.min(0).tolist(), "max": pos.max(0).tolist()},
                {"bufferView": 1, "componentType": 5126, "count": len(col), "type": "VEC3"},
                {"bufferView": 2, "componentType": 5125, "count": int(tris.size), "type": "SCALAR"}],
        }
        js = json.dumps(gl, separators=(",", ":")).encode()
        js += b" " * (-len(js) % 4)
        bin_ = b"".join(blobs)
        bin_ += b"\0" * (-len(bin_) % 4)
        with open(path, "wb") as f:
            f.write(struct.pack("<III", 0x46546C67, 2, 12 + 8 + len(js) + 8 + len(bin_)))
            f.write(struct.pack("<II", len(js), 0x4E4F534A) + js)
            f.write(struct.pack("<II", len(bin_), 0x004E4942) + bin_)

    def export_all(self, out_dir: str, stem: str = "model", title: str = "mesh") -> dict:
        os.makedirs(out_dir, exist_ok=True)
        paths = {k: os.path.join(out_dir, f"{stem}.{ext}") for k, ext in
                 [("radioss_inc", "inc"), ("stl", "stl"), ("vtk", "vtk"), ("glb", "glb"), ("summary", "json")]}
        self.write_radioss_inc(paths["radioss_inc"], title=title)
        self.write_stl(paths["stl"])
        self.write_vtk(paths["vtk"])
        self.write_glb(paths["glb"])
        summary = {**self.stats(), "meta": self.meta, "files": paths}
        with open(paths["summary"], "w") as f:
            json.dump(summary, f, indent=2)
        return summary


def mesh_from_deck(text: str) -> HexMesh | DeckMesh | None:
    """Best-effort /NODE + /BRICK, /SHELL, /SH3N parse. None when there are no nodes or no usable elements."""
    nodes: list[tuple[int, float, float, float]] = []
    elems: dict[str, list[list[int]]] = {"brick": [], "shell": [], "sh3n": []}
    section = None
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("/"):
            m = _SECTION.match(stripped)
            section = m.group(1).lower() if m else None
            continue
        if not stripped or stripped.startswith("#") or section is None:
            continue
        if section == "node":
            row = _parse_node(line)
            if row:
                nodes.append(row)
        else:
            row = _parse_elem(line, _NODES_PER[section])
            if row:
                elems[section].append(row)
    if not nodes:
        return None
    id_map = {node_id: index for index, (node_id, *_) in enumerate(nodes)}
    length_unit, to_mm = deck_length_unit(text)
    pts = np.array([[x, y, z] for _, x, y, z in nodes], float) * to_mm   # previews and stats are in mm

    def mapped(rows, n):
        ok = [r for r in rows if all(v in id_map for v in r)]
        return np.array([[id_map[v] for v in r] for r in ok], int).reshape(-1, n)

    bricks = mapped(elems["brick"], 8)
    quads = mapped(elems["shell"], 4)
    tris = mapped(elems["sh3n"], 3)
    if len(quads):                                    # /SHELL with n3 == n4 is a triangle
        degen = quads[:, 2] == quads[:, 3]
        if degen.any():
            tris = np.concatenate([tris, quads[degen][:, :3]])
            quads = quads[~degen]
    if not len(quads) and not len(tris):
        if len(pts) < 8 or not len(bricks):
            return None
        mesh = HexMesh(nodes=pts, elems=bricks)
        mesh.meta = {"source": "upload"}
        if length_unit and length_unit != "mm":
            mesh.meta["deck_length_unit"] = length_unit
        return mesh
    mesh = DeckMesh(nodes=pts, bricks=bricks, quads=quads, tris=tris)
    mesh.meta = {"source": "upload", "element_types": [k for k, a in (("BRICK", bricks), ("SHELL", quads), ("SH3N", tris)) if len(a)]}
    if length_unit:
        mesh.meta["deck_length_unit"] = length_unit
    return mesh


_LEN_TO_M = {"m": 1.0, "dm": 0.1, "cm": 0.01, "mm": 1e-3, "um": 1e-6, "mum": 1e-6, "km": 1e3, "in": 0.0254, "ft": 0.3048}


def deck_length_unit(text: str) -> tuple[str | None, float]:
    """Input length unit from /BEGIN (4th non-comment line after it: mass length time) and its factor to mm.
    Unknown or missing units keep the old assumption (mm, factor 1)."""
    lines = []
    for raw in text.splitlines():
        if raw.startswith("#"):
            continue
        lines.append(raw)
        if len(lines) > 400:
            break
    for i, line in enumerate(lines):
        if line.startswith("/BEGIN") and i + 3 < len(lines):
            toks = lines[i + 3].split()
            if len(toks) >= 3:
                unit = toks[1]
                try:
                    from post._vendor.postprocess import LEN_SI
                except Exception:  # pragma: no cover - post/ always ships with the server
                    LEN_SI = _LEN_TO_M
                if unit in LEN_SI:
                    return unit, LEN_SI[unit] * 1000.0
                return unit, 1.0
            break
    return None, 1.0


_INCLUDE = re.compile(r"^#include\s+(\S+)", re.IGNORECASE)


def skip_missing_includes(path, log=None) -> list[str]:
    """Comment out `#include <file>` lines whose file is not next to the deck (only the two decks are
    uploaded). Returns the skipped names."""
    from pathlib import Path

    deck = Path(path)
    lines = deck.read_text().splitlines(keepends=True)
    skipped = []
    for i, line in enumerate(lines):
        m = _INCLUDE.match(line.strip())
        if m and not (deck.parent / m.group(1)).is_file():
            skipped.append(m.group(1))
            lines[i] = f"# [simstudio] include not uploaded, skipped: {m.group(1)}\n"
    if skipped:
        deck.write_text("".join(lines))
        if log:
            for name in skipped:
                log(f"{deck.name}: #include {name} was not uploaded; the line is commented out")
    return skipped
