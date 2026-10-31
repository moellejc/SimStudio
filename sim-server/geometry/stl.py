"""STL -> voxel hex mesh (watertight STL required). Units: whatever the STL uses, scaled by `scale` to mm."""
from __future__ import annotations
import struct
import numpy as np
from scipy import ndimage
from .mesh import HexMesh


def read_stl(path: str) -> np.ndarray:
    """Return triangles (T, 3, 3). Handles binary and ASCII."""
    data = open(path, "rb").read()
    if len(data) >= 84:
        n = struct.unpack("<I", data[80:84])[0]
        if 84 + 50 * n == len(data):
            rec = np.frombuffer(data[84:], dtype=[("n", "<f4", 3), ("v", "<f4", (3, 3)), ("a", "<u2")], count=n)
            return rec["v"].astype(float)
    v = [list(map(float, l.split()[1:4])) for l in data.decode(errors="ignore").splitlines() if l.strip().startswith("vertex")]
    return np.array(v, float).reshape(-1, 3, 3)


def voxelize(tris: np.ndarray, h: float):
    """Inside test by ray parity along +Z through each (x, y) cell center."""
    lo = tris.reshape(-1, 3).min(0) - h; hi = tris.reshape(-1, 3).max(0) + h
    n = np.ceil((hi - lo) / h).astype(int)
    cx = lo[0] + (np.arange(n[0]) + 0.5) * h; cy = lo[1] + (np.arange(n[1]) + 0.5) * h
    cz = lo[2] + (np.arange(n[2]) + 0.5) * h
    occ = np.zeros(n, bool)
    a, b, c = tris[:, 0], tris[:, 1], tris[:, 2]
    eps = 1e-9 * h
    for i, x in enumerate(cx):
        sel = (np.minimum(np.minimum(a[:, 0], b[:, 0]), c[:, 0]) <= x) & (np.maximum(np.maximum(a[:, 0], b[:, 0]), c[:, 0]) >= x)
        if not sel.any():
            continue
        A, B, C = a[sel], b[sel], c[sel]
        for j, y in enumerate(cy):
            px, py = x + eps * 0.37, y + eps * 0.71
            d = (B[:, 1] - C[:, 1]) * (A[:, 0] - C[:, 0]) + (C[:, 0] - B[:, 0]) * (A[:, 1] - C[:, 1])
            ok = np.abs(d) > 1e-15
            l1 = ((B[:, 1] - C[:, 1]) * (px - C[:, 0]) + (C[:, 0] - B[:, 0]) * (py - C[:, 1])) / np.where(ok, d, 1)
            l2 = ((C[:, 1] - A[:, 1]) * (px - C[:, 0]) + (A[:, 0] - C[:, 0]) * (py - C[:, 1])) / np.where(ok, d, 1)
            l3 = 1 - l1 - l2
            hit = ok & (l1 >= 0) & (l2 >= 0) & (l3 >= 0)
            if not hit.any():
                continue
            zs = np.sort(l1[hit] * A[hit, 2] + l2[hit] * B[hit, 2] + l3[hit] * C[hit, 2])
            for z0, z1 in zip(zs[0::2], zs[1::2]):
                occ[i, j, (cz >= z0) & (cz <= z1)] = True
    return occ, lo


def stl_to_mesh(path: str, voxel_mm: float = 1.0, scale: float = 1.0, part_id: int = 1,
                place_on_floor: bool = False, floor_gap: float = 1.0) -> HexMesh:
    tris = read_stl(path) * scale
    occ, lo = voxelize(tris, voxel_mm)
    lab, n = ndimage.label(occ)
    if n > 1:
        occ = lab == 1 + int(np.argmax(ndimage.sum(occ, lab, range(1, n + 1))))
    I, J, K = np.nonzero(occ)
    grid = -np.ones(np.array(occ.shape) + 1, int)
    corners = [(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0), (0, 0, 1), (1, 0, 1), (1, 1, 1), (0, 1, 1)]
    for di, dj, dk in corners:
        grid[I + di, J + dj, K + dk] = 0
    used = np.argwhere(grid == 0)
    grid[tuple(used.T)] = np.arange(len(used))
    elems = np.stack([grid[I + di, J + dj, K + dk] for di, dj, dk in corners], 1)
    m = HexMesh(nodes=lo + used * voxel_mm, elems=elems, part_id=part_id)
    m.fix_orientation()
    if place_on_floor:
        m.place_on_floor(2, floor_gap)
    m.meta = {"source": "stl", "stl": str(path), "voxel_mm": voxel_mm, "scale": scale,
              "note": "voxel (stair-stepped) hex mesh"}
    return m
