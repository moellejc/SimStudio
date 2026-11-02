"""Displacement-controlled fork deck (original imposed-velocity study).

Units: mm, ms, kg. The handle end is driven at `velocity_mm_ms` (mm/ms = m/s)
through a smoothstep ramp, into a rigid wall at Z = 0.
"""

from __future__ import annotations

import os

import numpy as np

from runner import DeckFiles, DeckSpec

MATS = {
    "steel": dict(rho=7.9e-6, E=193.0, nu=0.29, a=0.215, b=0.725, title="AISI 304 stainless (bilinear)"),
    "aluminum": dict(rho=2.7e-6, E=69.0, nu=0.33, a=0.276, b=0.425, title="Al 6061-T6 (bilinear)"),
    "plastic": dict(rho=1.05e-6, E=3.0, nu=0.34, a=0.040, b=0.030, title="Polystyrene (bilinear, ductile idealisation)"),
}


def generate(spec: DeckSpec) -> DeckFiles:
    m = MATS[spec.material]
    name = spec.name
    rd = os.path.join(spec.out_dir, name)
    os.makedirs(rd, exist_ok=True)
    src = open(spec.mesh_path).read().split("\n")
    ib = src.index("/BRICK/1")
    xyz = np.array([[float(v) for v in line.split()] for line in src[2:ib] if line.strip()])
    nid = xyz[:, 0].astype(int)
    z = xyz[:, 3]
    zmax = float(z.max())
    topn = nid[z > zmax - 10.0 + 1e-6]
    wall = nid[z < 80.0]
    if len(topn) == 0:
        topn = nid[z >= np.quantile(z, 0.9)]
    if len(wall) == 0:
        wall = nid
    main = 900001
    cog = xyz[np.isin(nid, topn), 1:].mean(0)

    def grnod(gid, title, ids):
        s = f"/GRNOD/NODE/{gid}\n{title}\n"
        ids = [int(x) for x in ids]
        for k in range(0, len(ids), 10):
            s += "".join(f"{x:10d}" for x in ids[k : k + 10]) + "\n"
        return s

    f = lambda x: f"{x:20.6g}"
    tt = np.linspace(0, spec.ramp_ms, 21)
    ss = tt / max(spec.ramp_ms, 1e-9)
    ff = 3 * ss**2 - 2 * ss**3
    fun = "".join(f"{f(t)}{f(y)}\n" for t, y in zip(tt, ff)) + f"{f(1e6)}{f(1.0)}\n"
    deck = f"""#RADIOSS STARTER
# {name}: dinner fork pushed tines-down onto a rigid plane. Units mm ms kg (kN, GPa, J)
/BEGIN
{name}
      2026         0
                  kg                  mm                  ms
                  kg                  mm                  ms
/TITLE
{name} fork axial buckling on rigid wall
/MAT/PLAS_JOHNS/1
{m['title']}
#              RHO_I
{f(m['rho'])}
#                  E                  Nu     Iflag
{f(m['E'])}{f(m['nu'])}         0
#                  a                   b                   n           EPS_p_max            SIG_max0
{f(m['a'])}{f(m['b'])}{f(1.0)}{f(0)}{f(0)}
#                  c           EPS_DOT_0       ICC   Fsmooth               F_cut               Chard
{f(0)}{f(0)}         0         0{f(0)}{f(0)}
#                  m              T_melt              rhoC_p                 T_r
{f(0)}{f(0)}{f(0)}{f(0)}
/PROP/SOLID/1
fork solid
#   Isolid    Ismstr      Iale     Icpre  Itetra10     Inpts   Itetra4    Iframe                  Dn
        24         4         0         0         0         0         0         0{f(0)}
#                 qa                  qb                   h              Lambda                  Mu
{f(0)}{f(0)}{f(0)}{f(0)}{f(0)}
#         deltaT_min            vdef_min            vdef_max             ASP_max             COL_min
{f(0)}{f(0)}{f(0)}{f(0)}{f(0)}
#     Ndir sphpartID  Icontrol
         0         0         0
/PART/1
fork
#     prop_ID    mat_ID
         1         1
"""
    deck += "\n".join(src[:ib]).split("\n", 1)[1] + "\n"
    deck += f"{main:10d}{cog[0]:20.6f}{cog[1]:20.6f}{cog[2]:20.6f}\n"
    deck += "\n".join(src[ib:]) + "\n"
    deck += f"""/RBODY/1
pushed handle end (top 10 mm)
#  node_ID   sens_ID   Skew_ID    Ispher                Mass   grnd_ID     Ikrem      ICoG   surf_ID
{main:10d}         0         0         0{f(0)}        11         0         1         0
#                Jxx                 Jyy                 Jzz
{f(0)}{f(0)}{f(0)}
#                Jxy                 Jyz                 Jxz
{f(0)}{f(0)}{f(0)}
#  Ioptoff   Iexpams
         0         0
""" + grnod(11, "handle top nodes", topn) + """/BCS/1
pushed end: lateral X,Y fixed, rotations free (pinned)
#  Tra rot   skew_ID  grnod_ID
   110 000         0        12
""" + grnod(12, "rbody main", [main]) + f"""/FUNCT/1
smooth velocity ramp
#                  X                   Y
{fun}/IMPVEL/1
push -Z
#funct_IDT       Dir   skew_ID sensor_ID  grnod_ID  frame_ID     Icoor
         1         Z         0         0        12         0         0
#           Ascale_x            Fscale_Y              Tstart               Tstop
{f(1)}{f(-spec.velocity_mm_ms)}{f(0)}{f(1e30)}
/RWALL/PLANE/1
table surface Z=0 normal +Z
#  node_ID     Slide  grnd_ID1  grnd_ID2     Iform
         0         2        13         0         0
#                  d                fric            Diameter                           ifq
{f(0)}{f(spec.friction)}{f(0)}{'':20s}         0
#                 XM                  YM                  ZM
{f(0)}{f(0)}{f(0)}
#                XM1                 YM1                 ZM1
{f(0)}{f(0)}{f(1)}
""" + grnod(13, "wall candidate nodes Z<80", wall) + f"""/TH/RWALL/1
rigid wall force
#     var1      var2      var3      var4      var5      var6      var7      var8      var9     var10
DEF
#     Obj1      Obj2      Obj3      Obj4      Obj5      Obj6      Obj7      Obj8      Obj9     Obj10
         1
/TH/NODE/2
pushed end node
#     var1      var2      var3      var4      var5      var6      var7      var8      var9     var10
DEF
#    NODid     Iskew                                           NODname
{main:10d}         0                                                                      rbody_main
/TH/PART/3
fork part energies
#     var1      var2      var3      var4      var5      var6      var7      var8      var9     var10
DEF
#     Obj1      Obj2      Obj3      Obj4      Obj5      Obj6      Obj7      Obj8      Obj9     Obj10
         1
/END
"""
    starter = os.path.join(rd, f"{name}_0000.rad")
    engine_path = os.path.join(rd, f"{name}_0001.rad")
    open(starter, "w").write(deck)
    danim = spec.end_time_ms / max(spec.nanim, 1)
    dth = spec.end_time_ms / max(spec.nth, 1)
    engine = f"""/RUN/{name}/1
{f(spec.end_time_ms)}
/VERS/2026
/ANIM/DT
#   TSTART     TFREQ
{f(0)}{f(danim)}
/ANIM/VECT/DISP
/ANIM/VECT/VEL
/ANIM/ELEM/VONM
/ANIM/ELEM/EPSP
/ANIM/BRICK/TENS/STRESS
/TFILE/4
{f(dth)}
/PRINT/-100
/MON/ON
/PARITH/ON
"""
    open(engine_path, "w").write(engine)
    return DeckFiles(
        starter_path=starter,
        engine_path=engine_path,
        run_dir=rd,
        params={"name": name, "mat": spec.material, "mode": "displacement", "v": spec.velocity_mm_ms, "T_ms": spec.end_time_ms},
    )
