"""SimStudio geometry: turn a photo, an STL, or a parametric template into a Radioss hex mesh."""
from .mesh import HexMesh, extrude_columns
from .fork import build_fork
from .image import image_to_mesh, silhouette
from .stl import stl_to_mesh, read_stl

__all__ = ["HexMesh", "extrude_columns", "build_fork", "image_to_mesh", "silhouette", "stl_to_mesh", "read_stl"]
