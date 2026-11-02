"""post deck_units: SI factors come from the /BEGIN work-units line (solver output units), else the input line."""
import os

import pytest

from post._vendor.postprocess import deck_units

HEAD = "#RADIOSS STARTER\n/BEGIN\nRUN\n      2019         0\n"


def _deck(tmp_path, *unit_lines, tail="/TITLE\nx\n/END\n"):
    p = tmp_path / "R_0000.rad"
    p.write_text(HEAD + "".join(f"{l}\n" for l in unit_lines) + tail)
    return str(p)


def test_kg_mm_ms_defaults_unchanged(tmp_path):
    u = deck_units(None, deck=_deck(tmp_path, "kg mm ms", "kg mm ms"))
    assert u["known"] and (u["mass"], u["length"], u["time"]) == ("kg", "mm", "ms")
    assert u["stress_to_Pa"] == pytest.approx(1e9)
    assert u["force_to_N"] == pytest.approx(1e3)
    assert u["energy_to_J"] == pytest.approx(1.0)
    assert u["length_to_m"] == pytest.approx(1e-3)


def test_si_deck_has_unit_factors(tmp_path):
    u = deck_units(None, deck=_deck(tmp_path, "kg m s", "kg m s"))
    assert u["known"]
    for k in ("stress_to_Pa", "force_to_N", "energy_to_J", "length_to_m", "time_to_s"):
        assert u[k] == pytest.approx(1.0)


def test_work_units_win_over_input_units(tmp_path):
    # input in g/mm/ms (MPa), work in kg/mm/ms (GPa): results are written in work units
    u = deck_units(None, deck=_deck(tmp_path, "g mm ms", "kg mm ms"))
    assert u["input_units"] == ["g", "mm", "ms"] and u["work_units"] == ["kg", "mm", "ms"]
    assert u["mass"] == "kg" and u["stress_to_Pa"] == pytest.approx(1e9)


def test_missing_work_line_falls_back_to_input(tmp_path):
    u = deck_units(None, deck=_deck(tmp_path, "kg mm ms"))       # next line is /TITLE
    assert u["work_units"] is None and u["mass"] == "kg"
    assert u["stress_to_Pa"] == pytest.approx(1e9)


def test_unknown_units_are_flagged(tmp_path):
    u = deck_units(None, deck=_deck(tmp_path, "foo bar baz", "foo bar baz"))
    assert not u["known"] and u["stress_to_Pa"] == 1.0


BALL = "/workspace/openradioss/results/demo/BOULE1V4_0000.rad"


@pytest.mark.skipif(not os.path.exists(BALL), reason="ball demo deck is not on this machine")
def test_ball_demo_is_si():
    u = deck_units(None, deck=BALL)
    assert u["work_units"] == ["kg", "m", "s"] and u["stress_to_Pa"] == 1.0 and u["force_to_N"] == 1.0


def test_override_replaces_begin(tmp_path):
    u = deck_units(None, deck=_deck(tmp_path, "kg m s", "kg m s"), override="g-mm-ms")
    assert u["override"] == ["g", "mm", "ms"] and u["work_units"] == ["kg", "m", "s"]
    assert u["stress_to_Pa"] == pytest.approx(1e6) and u["force_to_N"] == pytest.approx(1.0)
    assert u["length_to_m"] == pytest.approx(1e-3) and u["energy_to_J"] == pytest.approx(1e-3)
    assert deck_units(None, deck=_deck(tmp_path, "kg m s", "kg m s"), override="auto")["override"] is None
    with pytest.raises(ValueError):
        deck_units(None, deck=_deck(tmp_path, "kg m s"), override="furlong-mm-s")


from simstudio.sanity import force_stress_warning, material_cards, material_warnings  # noqa: E402

BALL_MAT = """/MAT/PLAS_JOHNS/1
acier XES standard
#              RHO_I
               .0078                   0
#                  E                  Nu     Iflag
              200000                 .29         0
#                  a                   b                   n           EPS_p_max            SIG_max0
                 180                 450                  .5                1E30                 380
/END
"""


def test_material_check_flags_mislabelled_si_deck():
    assert material_cards(BALL_MAT) == [{"law": "PLAS_JOHNS", "id": 1, "title": "acier XES standard",
                                         "rho": 0.0078, "E": 200000.0}]
    notes = material_warnings(BALL_MAT, ["kg", "m", "s"])
    assert len(notes) == 1 and "g-mm-ms" in notes[0] and "t-mm-s" not in notes[0]
    assert material_warnings(BALL_MAT, ["g", "mm", "ms"]) == []          # 200 GPa, 7800 kg/m^3
    steel_mm = BALL_MAT.replace(".0078   ", "7.8E-6  ").replace("200000", "   210")
    assert material_warnings(steel_mm, ["kg", "mm", "ms"]) == []          # the fork templates' system


def test_force_stress_warning():
    box = ([0, 0, 0], [0.01, 0.01, 0.1])
    assert force_stress_warning(441.0, 1.4e8, *box) is None
    assert force_stress_warning(441.0, 10.0, *box) is not None        # 1000x below F / diag^2
    assert force_stress_warning(441.0, 1e15, *box) is not None
    assert force_stress_warning(None, 3.78e2, *box) is None
