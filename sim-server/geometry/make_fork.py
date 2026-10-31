"""Parametric hex mesh of a dinner fork reconstructed from a reference photo.

Units: mm. Output oriented tines-down: length axis = Z, tine tips near z = 1 mm
(flat table at z = 0). This is the geometry teammates own; `build_fork` is the
callable form of the original script.

Coarser `dx_mm` / `dy_mm` / `nz` are for tests and dry runs. The study defaults
are dx=0.8, dy=0.35, nz=3, length=190.
"""

from __future__ import annotations

import math
import struct
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from geometry import HexMesh, MeshRequest


def width(x: float, length: float) -> float:
    if x <= 38:
        return 21.0 + 3.0 * x / 38
    if x <= 62:
        s = (x - 38) / 24
        return 24.0 + (9.0 - 24.0) * (3 * s * s - 2 * s**3)
    if x <= 80:
        return 9.0 - 1.0 * (x - 62) / 18
    if x <= 176:
        s = (x - 80) / 96
        return 8.0 + 14.0 * math.sin(s * math.pi / 2) ** 1.3
    r = 11.0
    c = length - r
    return 2 * math.sqrt(max(r * r - (x - c) ** 2, 0))


def thick(x: float) -> float:
    if x <= 38:
        return 1.8
    if x <= 60:
        return 1.8 + 1.0 * (x - 38) / 22
    if x <= 85:
        return 2.8
    return 2.8 - 0.5 * min((x - 85) / 40, 1)


def zc(x: float) -> float:
    """Neck bend: head/tines offset from the handle plane."""
    if x <= 45:
        return 7.0 - 0.02 * (45 - x) ** 1.5 * 0.15
    if x >= 95:
        return 0.0
    s = (x - 45) / 50
    return 7.0 * (1 - (3 * s * s - 2 * s**3))


def inside(x: float, y: float, length: float) -> bool:
    w = width(x, length)
    if abs(y) > w / 2:
        return False
    if x < 36:
        g = 2.2
        tw = (w - 3 * g) / 4
        u = y + w / 2
        k = int(u // (tw + g)) if tw + g else 0
        r = u - k * (tw + g)
        if k > 3 or r > tw:
            return False
        if x < 5:
            m = tw / 2 * (1 - math.sqrt(max(0.0, 1 - (x / 5) ** 2)))
            if r < m or r > tw - m:
                return False
    return True


def build_fork(request: "MeshRequest") -> "HexMesh":
    """Photo -> Radioss hex mesh. See module docstring for orientation."""
    from geometry import HexMesh

    dx = float(request.dx_mm)
    dy = float(request.dy_mm)
    nz = int(request.nz)
    length = float(request.length_mm)
    if dx <= 0 or dy <= 0 or nz < 1:
        raise ValueError("dx_mm, dy_mm must be positive and nz >= 1")
    nx = max(2, int(round(length / dx)))
    span = 24.0
    ny = max(2, int(round(span / dy)))
    xs = np.linspace(0, length, nx + 1)
    ys = np.linspace(-span / 2, span / 2, ny + 1)
    mask = np.zeros((nx, ny), bool)
    for i in range(nx):
        xc = (xs[i] + xs[i + 1]) / 2
        for j in range(ny):
            mask[i, j] = inside(float(xc), float((ys[j] + ys[j + 1]) / 2), length)
    if not mask.any():
        raise ValueError("fork mask is empty; increase resolution")
    used = np.zeros((nx + 1, ny + 1), bool)
    for i, j in zip(*np.nonzero(mask)):
        used[i : i + 2, j : j + 2] = True
    nid = -np.ones((nx + 1, ny + 1, nz + 1), int)
    coords: list[tuple[int, float, float, float]] = []
    n = 0
    for i in range(nx + 1):
        x = float(xs[i])
        t = thick(x)
        z0 = zc(x)
        for j in range(ny + 1):
            if not used[i, j]:
                continue
            for k in range(nz + 1):
                n += 1
                nid[i, j, k] = n
                # Z = 1 + x (length up), X = y (width), Y = through-thickness + bend
                coords.append((n, float(ys[j]), z0 + (k / nz - 0.5) * t, 1.0 + x))
    bricks = []
    e = 0
    for i, j in zip(*np.nonzero(mask)):
        for k in range(nz):
            e += 1

            def a(ii: int, jj: int, kk: int, i=i, j=j, k=k) -> int:
                return int(nid[i + ii, j + jj, k + kk])

            bricks.append((e, a(0, 0, 0), a(0, 1, 0), a(0, 1, 1), a(0, 0, 1), a(1, 0, 0), a(1, 1, 0), a(1, 1, 1), a(1, 0, 1)))
    lines = ["#--- Fork mesh (mm). Tines down, tips at Z=1.0, axis along +Z", "/NODE"]
    for node_id, x, y, z in coords:
        lines.append(f"{node_id:10d}{x:20.6f}{y:20.6f}{z:20.6f}")
    lines.append(f"/BRICK/{request.part_id}")
    for el in bricks:
        lines.append("".join(f"{v:10d}" for v in el))
    lines.append("")
    include = "\n".join(lines)
    stl = _boundary_stl(coords, bricks)
    xyz = np.array([c[1:] for c in coords])
    return HexMesh(
        nodes=coords,
        bricks=bricks,
        include_text=include,
        stl_bytes=stl,
        meta={
            "kind": "fork",
            "implementation": "make_fork",
            "nodes": len(coords),
            "bricks": len(bricks),
            "bbox_min": [float(v) for v in xyz.min(0)],
            "bbox_max": [float(v) for v in xyz.max(0)],
            "dx_mm": dx,
            "dy_mm": dy,
            "nz": nz,
            "length_mm": length,
        },
    )


def _boundary_stl(coords, bricks) -> bytes:
    c = {n: (x, y, z) for n, x, y, z in coords}
    faces: dict[tuple, tuple | None] = {}
    quads = ((0, 1, 2, 3), (4, 7, 6, 5), (0, 4, 5, 1), (1, 5, 6, 2), (2, 6, 7, 3), (3, 7, 4, 0))
    for el in bricks:
        for fc in quads:
            q = tuple(el[1 + v] for v in fc)
            key = tuple(sorted(q))
            faces[key] = None if key in faces else q
    boundary = [q for q in faces.values() if q]
    buf = bytearray(b"fork".ljust(80, b" "))
    buf += struct.pack("<I", 2 * len(boundary))
    for q in boundary:
        for tri in ((q[0], q[1], q[2]), (q[0], q[2], q[3])):
            p = [np.array(c[v], float) for v in tri]
            nrm = np.cross(p[1] - p[0], p[2] - p[0])
            ln = float(np.linalg.norm(nrm))
            nrm = nrm / ln if ln else nrm
            buf += struct.pack("<12fH", *nrm, *p[0], *p[1], *p[2], 0)
    return bytes(buf)
