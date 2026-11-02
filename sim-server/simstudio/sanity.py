"""Unit sanity checks for uploaded decks: material cards vs the unit system used, and stress vs force/area.

A deck can be dimensionally self-consistent in two systems at once (e.g. data written in g/mm/ms but
/BEGIN saying kg/m/s): the solver result is the same numbers, only the labels change, so the run looks
fine but every stress is 1e6x off. Real material constants are the giveaway, so check them."""

from __future__ import annotations

import re

from post._vendor.postprocess import LEN_SI, MASS_SI, TIME_SI

# laws whose card is: title / RHO_I [...] / E nu [...]
_E_LAWS = {"ELAST", "LAW1", "PLAS_JOHNS", "LAW2", "PLAS_TAB", "LAW36", "PLAS_BRIT", "LAW27",
           "COWPER", "LAW44", "PLAS_ZERIL", "LAW48", "PLAS_HARD", "LAW23", "HILL", "LAW32"}
UPLOAD_UNIT_SYSTEMS = {"kg-m-s": ("kg", "m", "s"), "g-mm-ms": ("g", "mm", "ms"),
                       "kg-mm-ms": ("kg", "mm", "ms"), "t-mm-s": ("t", "mm", "s")}
_NUM = re.compile(r"^[+-]?(\d+\.?\d*|\.\d+)([eEdD][+-]?\d+)?$")


def _first_float(line: str):
    tok = line.split()
    if not tok or not _NUM.match(tok[0]):
        return None
    return float(tok[0].replace("D", "E").replace("d", "e"))


def material_cards(text: str) -> list[dict]:
    """[{law, id, title, rho, E}] in deck units for /MAT cards whose 2nd/3rd lines are RHO and E."""
    out, lines = [], text.splitlines()
    for i, line in enumerate(lines):
        m = re.match(r"^/MAT/([A-Z0-9_]+)/(\d+)", line.strip())
        if not m or m.group(1) not in _E_LAWS:
            continue
        data = []
        for nxt in lines[i + 1:]:
            if nxt.startswith("/"):
                break
            if nxt.startswith("#") or not nxt.strip():
                continue
            data.append(nxt)
            if len(data) == 3:
                break
        if len(data) < 3:
            continue
        rho, E = _first_float(data[1]), _first_float(data[2])
        if rho and E:
            out.append({"law": m.group(1), "id": int(m.group(2)), "title": data[0].strip(), "rho": rho, "E": E})
    return out


def _factors(mlt):
    m, l, t = MASS_SI[mlt[0]], LEN_SI[mlt[1]], TIME_SI[mlt[2]]
    return m / (l * t * t), m / l ** 3          # stress -> Pa, density -> kg/m^3


def _plausible(E_pa: float, rho: float) -> bool:
    return 1e5 <= E_pa <= 1.5e12 and 5.0 <= rho <= 3e4


def material_warnings(text: str, units) -> list[str]:
    """Notes when a material's E / density are implausible in the unit system used (units = (M, L, T))."""
    if not units or any(u is None for u in units) or units[0] not in MASS_SI or units[1] not in LEN_SI \
            or units[2] not in TIME_SI:
        return []
    s_pa, r_si = _factors(units)
    notes = []
    for mat in material_cards(text):
        E_pa, rho = mat["E"] * s_pa, mat["rho"] * r_si
        if _plausible(E_pa, rho):
            continue
        fits = [key for key, mlt in UPLOAD_UNIT_SYSTEMS.items()
                if _plausible(mat["E"] * _factors(mlt)[0], mat["rho"] * _factors(mlt)[1])]
        hint = f" The card fits {', '.join(fits)}; set the upload's unit system to match." if fits else ""
        notes.append(
            f"Unit check: /MAT/{mat['law']}/{mat['id']} '{mat['title']}' reads E = {E_pa:.3g} Pa and "
            f"density = {rho:.3g} kg/m³ in {'/'.join(units)}, which is not a real solid; stresses are "
            f"probably off by a constant factor.{hint}")
    return notes


def force_stress_warning(peak_force_N, max_vm_Pa, bbox_min_m, bbox_max_m, factor=1e3) -> str | None:
    """Note when peak stress and peak force / a rough contact area disagree by more than `factor`.

    The rough area spans (diag/100)^2 .. diag^2 for the model's bounding-box diagonal."""
    try:
        diag = sum((b - a) ** 2 for a, b in zip(bbox_min_m, bbox_max_m)) ** 0.5
    except TypeError:
        return None
    if not peak_force_N or not max_vm_Pa or diag <= 0:
        return None
    lo, hi = peak_force_N / diag ** 2, peak_force_N / (diag / 100.0) ** 2
    if max_vm_Pa * factor < lo or max_vm_Pa > hi * factor:
        return (f"Unit check: max von Mises {max_vm_Pa:.3g} Pa is far from peak force / contact area "
                f"({lo:.3g}–{hi:.3g} Pa for {peak_force_N:.3g} N on a {diag:.3g} m model); check the /BEGIN units.")
    return None
