"""Photo -> extruded hex mesh. A single photo gives the outline only, so thickness is a user input
(constant, or a callable of the normalized position along the long axis). Units mm."""
from __future__ import annotations
import numpy as np
from PIL import Image
from scipy import ndimage
from .mesh import extrude_columns, HexMesh


def silhouette(path: str, tol: float | None = None) -> np.ndarray:
    """Foreground mask: pixels that differ from the border (background) color. Largest blob, holes filled."""
    img = np.asarray(Image.open(path).convert("RGBA"), float)
    rgb, alpha = img[..., :3], img[..., 3]
    if alpha.min() < 250:  # transparent PNG: alpha is the mask
        fg = alpha > 128
    else:
        border = np.concatenate([rgb[0], rgb[-1], rgb[:, 0], rgb[:, -1]])
        bg = np.median(border, axis=0)
        d = np.linalg.norm(rgb - bg, axis=-1)
        if tol is None:
            bd = np.linalg.norm(border - bg, axis=-1)
            tol = max(18.0, np.percentile(bd, 99) * 1.5)
        fg = d > tol
    fg = ndimage.binary_closing(fg, iterations=2)
    lab, n = ndimage.label(fg)
    if n == 0:
        raise ValueError("no foreground object found in image")
    sizes = ndimage.sum(fg, lab, range(1, n + 1))
    fg = lab == (1 + int(np.argmax(sizes)))
    return ndimage.binary_fill_holes(fg)


def _align(mask: np.ndarray, res: int = 4):
    """Rotate the object's long (principal) axis onto +x. Returns point cloud in pixel units."""
    ys, xs = np.nonzero(mask)
    # supersample pixel centers so thin features survive rotation
    o = (np.arange(res) + 0.5) / res - 0.5
    ox, oy = np.meshgrid(o, o)
    px = (xs[:, None] + ox.ravel()[None]).ravel(); py = (-(ys[:, None] + oy.ravel()[None])).ravel()
    P = np.stack([px, py], 1); P -= P.mean(0)
    w, v = np.linalg.eigh(np.cov(P.T))
    a = v[:, np.argmax(w)]
    R = np.array([[a[0], a[1]], [-a[1], a[0]]])
    return P @ R.T


def image_to_mesh(path: str, length_mm: float, thickness_mm=2.0, cell_mm: float | None = None,
                  nz: int = 3, align: bool = True, flip: bool = False, tol: float | None = None,
                  part_id: int = 1, upright: bool = True, floor_gap: float = 1.0) -> HexMesh:
    """Build a solid from a photo's outline, scaled so its longest extent equals `length_mm`.
    thickness_mm: float, or f(s) with s in [0,1] along the long axis (0 = end that ends up at the floor).
    upright: stand the part on its end (long axis along +Z, lowest point `floor_gap` above Z=0)."""
    mask = silhouette(path, tol)
    if align:
        P = _align(mask)
    else:
        ys, xs = np.nonzero(mask); P = np.stack([xs, -ys], 1).astype(float)
    if flip:
        P[:, 0] *= -1
    span = P[:, 0].max() - P[:, 0].min()
    P = (P - P.min(0)) * (length_mm / span)
    if cell_mm is None:
        cell_mm = length_mm / 250.0
    nx = int(np.ceil(P[:, 0].max() / cell_mm)) + 1; ny = int(np.ceil(P[:, 1].max() / cell_mm)) + 1
    hist, _, _ = np.histogram2d(P[:, 0], P[:, 1], bins=[nx, ny], range=[[0, nx * cell_mm], [0, ny * cell_mm]])
    density = hist / max(np.percentile(hist[hist > 0], 90), 1)
    cells = ndimage.binary_fill_holes(density > 0.5)
    lab, n = ndimage.label(cells)
    if n > 1:
        cells = lab == 1 + int(np.argmax(ndimage.sum(cells, lab, range(1, n + 1))))
    xs = np.arange(nx + 1) * cell_mm; ys = np.arange(ny + 1) * cell_mm
    ys = ys - ys[-1] / 2
    t = (lambda X, Y: thickness_mm(np.clip(X / length_mm, 0, 1)) * np.ones_like(X)) if callable(thickness_mm) \
        else (lambda X, Y: float(thickness_mm) * np.ones_like(X))
    m = extrude_columns(cells, xs, ys, lambda X, Y: -0.5 * t(X, Y), lambda X, Y: 0.5 * t(X, Y), nz)
    if upright:
        m.nodes = m.nodes[:, [1, 2, 0]].copy(); m.fix_orientation(); m.place_on_floor(2, floor_gap)
    m.part_id = part_id
    m.meta = {"source": "image", "image": str(path), "length_mm": length_mm, "cell_mm": cell_mm, "nz": nz,
              "thickness": "variable" if callable(thickness_mm) else float(thickness_mm),
              "note": "outline from photo; thickness is assumed, not measured"}
    return m
