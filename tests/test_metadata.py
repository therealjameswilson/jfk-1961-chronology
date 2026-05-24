"""Tests for document metadata extraction helpers."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from metadata import _agency_from_rif


def test_agency_from_rif_uses_standard_prefix_mapping() -> None:
    assert _agency_from_rif("104-10219-10143") == "CIA"
    assert _agency_from_rif("124-90084-10077") == "FBI"
    assert _agency_from_rif("157-10014-10178") == "SSCIA"
    assert _agency_from_rif("180-10068-10319") == "HSCA"
    assert _agency_from_rif("194-10002-10203") == "FBI"
    assert _agency_from_rif("198-10001-10001") == "JFK Task Force"
    assert _agency_from_rif("202-10001-10001") == "Secret Service"
    assert _agency_from_rif("999-10001-10001") is None
