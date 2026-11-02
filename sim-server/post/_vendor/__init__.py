"""Vendored copies of /workspace/openradioss/post/{postprocess,compare,make_viewer}.py.

Logic is unchanged. Only edits: compare.main / make_viewer.main take argv, and make_viewer.THREE_DIR points at
post/assets/three. The modules are aliased as top-level `postprocess` / `compare` so their own
`import postprocess as pp` resolves to this copy (one module object, no second import).
"""
import os
import sys

from .. import _env

_env.ensure_tools()
from . import postprocess  # noqa: E402

sys.modules["postprocess"] = postprocess
from . import compare  # noqa: E402

sys.modules["compare"] = compare
from . import make_viewer  # noqa: E402

VENDOR_DIR = os.path.dirname(os.path.abspath(__file__))
__all__ = ["postprocess", "compare", "make_viewer", "VENDOR_DIR"]
