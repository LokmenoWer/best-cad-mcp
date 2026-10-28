"""Let the unit suite import the COM bridge on hosts without pywin32."""

from __future__ import annotations

import sys
import types


def _ensure_windows_com_stubs() -> None:
    try:
        import pythoncom  # noqa: F401
        import win32com.client  # noqa: F401
    except ImportError:
        win32com = types.ModuleType("win32com")
        client = types.ModuleType("win32com.client")
        pythoncom = types.ModuleType("pythoncom")
        pythoncom.VT_ARRAY = 0x2000
        pythoncom.VT_R8 = 5
        pythoncom.VT_I2 = 2
        pythoncom.VT_I4 = 3
        pythoncom.VT_BSTR = 8
        pythoncom.VT_VARIANT = 12
        pythoncom.VT_DISPATCH = 9
        client.VARIANT = lambda vt, value: value
        client.Dispatch = lambda obj: obj
        client.GetActiveObject = lambda prog_id: None
        win32com.client = client
        sys.modules["win32com"] = win32com
        sys.modules["win32com.client"] = client
        sys.modules["pythoncom"] = pythoncom


_ensure_windows_com_stubs()
