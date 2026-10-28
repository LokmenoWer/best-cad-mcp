import math
from unittest.mock import MagicMock

import pytest

from src.cad_controller import CADController
from src.cad_utils import lisp_handent, lisp_number, lisp_point, lisp_quoted


def _controller() -> CADController:
    """Build a controller without touching the process-wide COM singleton."""
    ctrl = object.__new__(CADController)
    ctrl._initialized = True
    ctrl.acad = None
    ctrl.doc = None
    return ctrl


def test_lisp_handle_rejects_breakout_characters():
    with pytest.raises(ValueError):
        lisp_handent('AB"); (command "ERASE" "ALL")')


def test_lisp_number_rejects_non_finite_and_code():
    assert lisp_number("1.25", "radius") == "1.25"
    assert lisp_number(4, "segments") == "4"
    with pytest.raises(ValueError):
        lisp_number(float("nan"), "radius")
    with pytest.raises(ValueError):
        lisp_number(math.inf, "radius")
    with pytest.raises(ValueError):
        lisp_number('1); (command "ERASE")', "radius")


def test_lisp_point_and_quoted_text_are_literals():
    assert lisp_point([1, 2, 3], "point") == "(list 1 2 3)"
    assert lisp_quoted('block"name', "block_name") == 'block\\"name'
    with pytest.raises(ValueError):
        lisp_quoted("bad\nname", "block_name")


def test_fillet_does_not_send_an_injected_handle(monkeypatch):
    ctrl = _controller()
    monkeypatch.setattr(ctrl, "_ensure_connected", lambda: None)
    acad = MagicMock()
    acad.Documents.Count = 1
    doc = MagicMock()
    acad.ActiveDocument = doc
    ctrl.acad = acad
    ctrl.doc = doc
    sent = []
    monkeypatch.setattr(ctrl, "run_lisp", lambda lisp, wait=True: sent.append(lisp) or {"success": True})
    rejected = ctrl.fillet('ABC"); (command "ERASE")', "10", 1)
    assert rejected["success"] is False
    assert sent == []

    accepted = ctrl.fillet("ab", "10", 1.25)
    assert accepted["success"] is True
    assert sent
    assert '(handent "AB")' in sent[0]
    assert '(handent "10")' in sent[0]
    assert 'FILLETRAD" 1.25' in sent[0]
    assert "ERASE" not in sent[0]


def test_divide_escapes_block_names_before_send(monkeypatch):
    ctrl = _controller()
    monkeypatch.setattr(ctrl, "_ensure_connected", lambda: None)
    acad = MagicMock()
    acad.Documents.Count = 1
    doc = MagicMock()
    acad.ActiveDocument = doc
    ctrl.acad = acad
    ctrl.doc = doc
    sent = []
    monkeypatch.setattr(ctrl, "run_lisp", lambda lisp, wait=True: sent.append(lisp) or {"success": True})
    rejected = ctrl.divide("AA", 4, "bad\nname")
    assert rejected["success"] is False
    assert sent == []

    accepted = ctrl.divide("aa", 4, 'bolt"mark')
    assert accepted["success"] is True
    assert '(handent "AA")' in sent[0]
    assert 'bolt\\"mark' in sent[0]
