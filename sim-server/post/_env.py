"""Locate the OpenRadioss/OpenCourant converter binaries (anim_to_vtk, th_to_csv)."""
import glob
import os

DEFAULT_EXEC = "/workspace/openradioss/tools/OpenCourant/exec"


def exec_dir():
    return os.environ.get("OPENRADIOSS_EXEC") or DEFAULT_EXEC


def ensure_tools():
    """Put $OPENRADIOSS_EXEC first on PATH (the vendored find_tool and any subprocess pick it up from there),
    set OPENRADIOSS_PATH and the converters' library path, and default VTK to headless EGL."""
    d = os.path.abspath(exec_dir())
    path = os.environ.get("PATH", "")
    if d not in path.split(os.pathsep):
        os.environ["PATH"] = d + os.pathsep + path
    root = os.path.dirname(d)
    os.environ.setdefault("OPENRADIOSS_PATH", root)
    libs = [p for p in (os.path.join(root, "extlib", "hm_reader", "linux64"), os.path.join(root, "extlib", "h3d", "lib", "linux64"))
            if os.path.isdir(p)]
    ld = os.environ.get("LD_LIBRARY_PATH", "")
    for p in libs:
        if p not in ld.split(os.pathsep):
            ld = p + (os.pathsep + ld if ld else "")
    if ld:
        os.environ["LD_LIBRARY_PATH"] = ld
    os.environ.setdefault("VTK_DEFAULT_OPENGL_WINDOW", "vtkEGLRenderWindow")
    return d


def tool(stem):
    """Full path of <stem>_linux64_gf in the exec dir, else None."""
    for c in sorted(glob.glob(os.path.join(exec_dir(), stem + "*"))):
        if os.access(c, os.X_OK) and not c.endswith(".exe") and "_sp" not in os.path.basename(c):
            return c
    return None
