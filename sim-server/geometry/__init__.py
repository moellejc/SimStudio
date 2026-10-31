"""Hex meshing for Radioss starter decks.

Public interface (stable for other contributors):

    build_hex_mesh(request: MeshRequest) -> HexMesh

Implementations:
- kind="box" or request.mock: a tiny hex block (always available, no numpy).
- kind="fork": photo-derived dinner fork in make_fork.py (/NODE + /BRICK).
- kind="stl": STL bytes are accepted. A production voxelizer can replace
  `_mesh_stl`; until then an STL request returns the box mock and sets
  meta["stl_fallback"].

The include text is a Radioss fragment: `/NODE` then `/BRICK/<part>`.
Coordinates are millimetres. Fork meshes are oriented tines-down, length along +Z,
tips near Z = 1 mm.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class MeshRequest:
    """What to mesh.

    kind: "fork" | "stl" | "box"
    mock: force the analytic box, ignoring kind.
    """

    kind: str = "fork"
    dx_mm: float = 0.8
    dy_mm: float = 0.35
    nz: int = 3
    length_mm: float = 190.0
    stl: bytes | None = None
    mock: bool = False
    part_id: int = 1


@dataclass
class HexMesh:
    """Radioss hex mesh.

    nodes: (node_id, x, y, z) in mm, 1-based ids.
    bricks: (elem_id, n1..n8) using Radioss node order.
    include_text: starter fragment beginning with /NODE.
    """

    nodes: list[tuple[int, float, float, float]]
    bricks: list[tuple[int, int, int, int, int, int, int, int, int]]
    include_text: str
    stl_bytes: bytes | None = None
    meta: dict = field(default_factory=dict)


def build_hex_mesh(request: MeshRequest | None = None) -> HexMesh:
    """STL or photo -> Radioss `/NODE` + `/BRICK` hex mesh."""
    request = request or MeshRequest()
    kind = (request.kind or "box").lower()
    if request.mock or kind == "box":
        mesh = _mesh_box(request.part_id)
        mesh.meta["implementation"] = "mock-box"
        return mesh
    if kind == "stl":
        mesh = _mesh_stl(request)
        return mesh
    if kind == "fork":
        from geometry.make_fork import build_fork

        return build_fork(request)
    raise ValueError(f"unknown geometry kind {request.kind!r}")


def _fmt_include(nodes, bricks, part_id: int, comment: str) -> str:
    lines = [comment, "/NODE"]
    for nid, x, y, z in nodes:
        lines.append(f"{nid:10d}{x:20.6f}{y:20.6f}{z:20.6f}")
    lines.append(f"/BRICK/{part_id}")
    for el in bricks:
        lines.append("".join(f"{v:10d}" for v in el))
    lines.append("")
    return "\n".join(lines)


def _mesh_box(part_id: int) -> HexMesh:
    """2x1x1 hexes. Used by tests and by dry-run jobs that do not need a fork."""
    nodes = []
    nid = 1
    xs = (0.0, 10.0, 20.0)
    ys = (0.0, 8.0)
    zs = (0.0, 6.0)
    index = {}
    for k, z in enumerate(zs):
        for j, y in enumerate(ys):
            for i, x in enumerate(xs):
                nodes.append((nid, x, y, z))
                index[(i, j, k)] = nid
                nid += 1
    bricks = []
    eid = 1
    for k in range(len(zs) - 1):
        for j in range(len(ys) - 1):
            for i in range(len(xs) - 1):
                n = lambda di, dj, dk, i=i, j=j, k=k: index[(i + di, j + dj, k + dk)]
                bricks.append((eid, n(0, 0, 0), n(1, 0, 0), n(1, 1, 0), n(0, 1, 0), n(0, 0, 1), n(1, 0, 1), n(1, 1, 1), n(0, 1, 1)))
                eid += 1
    text = _fmt_include(nodes, bricks, part_id, "# mock hex block, mm")
    return HexMesh(nodes=nodes, bricks=bricks, include_text=text, meta={"kind": "box", "part_id": part_id})


def _mesh_stl(request: MeshRequest) -> HexMesh:
    """Placeholder STL path. Swap this function for a real voxel hex mesher."""
    mesh = _mesh_box(request.part_id)
    mesh.stl_bytes = request.stl
    mesh.meta.update(kind="stl", stl_fallback=True, stl_bytes=len(request.stl or b""), implementation="mock-box")
    return mesh
