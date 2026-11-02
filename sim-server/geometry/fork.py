"""Parametric dinner fork (the ForkSim reference part). Units mm.
Built in local coords (x along length from tine tips, y across width, z through thickness),
then oriented tines-down: length along +Z, tine tips `gap` mm above the floor at Z=0."""
import numpy as np
from .mesh import extrude_columns, HexMesh

L = 190.0

def _ss(a, b, u):
    s = np.clip(u, 0, 1); return a + (b - a) * (3 * s * s - 2 * s ** 3)

def width(x):
    x = np.asarray(x, float); r = 11.0; c = L - r
    return np.select(
        [x <= 38, x <= 62, x <= 80, x <= 176],
        [21.0 + 3.0 * x / 38, _ss(24.0, 9.0, (x - 38) / 24), 9.0 - (x - 62) / 18,
         8.0 + 14.0 * np.sin(np.clip((x - 80) / 96, 0, 1) * np.pi / 2) ** 1.3],
        2 * np.sqrt(np.clip(r * r - (x - c) ** 2, 0, None)))

def centerline(x):
    """Neck bend: head and tines sit 7 mm off the handle plane."""
    x = np.asarray(x, float)
    return np.where(x <= 45, 7.0, np.where(x >= 95, 0.0, 7.0 * (1 - _ss(0, 1, (x - 45) / 50))))

def thickness(x, y, profile="realistic"):
    x = np.asarray(x, float); y = np.asarray(y, float)
    if profile == "uniform":
        t = np.select([x <= 38, x <= 60, x <= 85], [1.8, 1.8 + (x - 38) / 22, 2.8],
                      2.8 - 0.5 * np.clip((x - 85) / 40, 0, 1))
        return t
    t = np.select([x <= 36, x <= 55, x <= 85, x <= 110, x <= 150],
                  [1.2 + 0.8 * x / 36, _ss(2.0, 3.0, (x - 36) / 19), 3.0, _ss(3.0, 2.2, (x - 85) / 25), 2.2],
                  _ss(2.2, 3.0, (x - 150) / 26))
    w = np.maximum(width(x), 1e-6)
    g = 2.2; tw = (w - 3 * g) / 4; r = (y + w / 2) % (tw + g)
    e_tine = np.clip(np.abs(r - tw / 2) / (tw / 2), 0, 1)
    e_body = np.clip(np.abs(y) / (w / 2), 0, 1)
    return np.where(x <= 36, t * (1 - 0.1 * e_tine ** 2), t * (1 - 0.15 * e_body ** 2))

def _inside(x, y):
    w = width(x)
    ok = np.abs(y) <= w / 2
    g = 2.2; tw = (w - 3 * g) / 4; u = y + w / 2
    k = np.floor(u / (tw + g)); r = u - k * (tw + g)
    tine_ok = (k <= 3) & (r <= tw)
    m = tw / 2 * (1 - np.sqrt(np.clip(1 - (x / 5) ** 2, 0, None)))
    tip_ok = np.where(x < 5, (r >= m) & (r <= tw - m), True)
    return ok & np.where(x < 36, tine_ok & tip_ok, True)

def build_fork(profile: str = "realistic", nz: int = 5, dx: float = 0.8, dy: float = 0.35,
               floor_gap: float = 1.0) -> HexMesh:
    nx = int(round(L / dx)); W = 24.0; ny = int(round(W / dy))
    xs = np.linspace(0, L, nx + 1); ys = np.linspace(-W / 2, W / 2, ny + 1)
    XC, YC = np.meshgrid((xs[:-1] + xs[1:]) / 2, (ys[:-1] + ys[1:]) / 2, indexing="ij")
    mask = _inside(XC, YC)
    zb = lambda X, Y: centerline(X) - 0.5 * thickness(X, Y, profile)
    zt = lambda X, Y: centerline(X) + 0.5 * thickness(X, Y, profile)
    m = extrude_columns(mask, xs, ys, zb, zt, nz)
    # orient: X_out = width (y), Y_out = thickness/bend (z), Z_out = length (x)
    m.nodes = m.nodes[:, [1, 2, 0]].copy()
    m.fix_orientation()
    m.place_on_floor(axis=2, gap=floor_gap)
    m.meta = {"source": "parametric_fork", "profile": profile, "length_mm": L, "nz": nz,
              "orientation": "tines down, length along +Z, floor at Z=0"}
    return m
