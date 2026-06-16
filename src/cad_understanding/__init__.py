"""CAD Understanding Layer.

Pure-Python analysis, grounding, validation, and plan helpers that operate on
the scanned CAD metadata database. These modules intentionally avoid direct
AutoCAD COM access; any live drawing interaction goes through existing tools.
"""

from .result import error_result, ok_result

__all__ = ["ok_result", "error_result"]
