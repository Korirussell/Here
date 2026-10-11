"""Every Python file in the repo byte-compiles. Entry points like laptop/__main__.py are never imported by the
other tests, so a syntax error there only shows up when someone runs the demo."""

import py_compile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SKIP = {".venv", "reference", "legacy", "node_modules", ".git", "logs", "__pycache__"}
FILES = sorted(p for p in ROOT.rglob("*.py") if not SKIP.intersection(p.relative_to(ROOT).parts))


@pytest.mark.parametrize("path", FILES, ids=lambda p: str(p.relative_to(ROOT)))
def test_compiles(path, tmp_path):
    py_compile.compile(str(path), cfile=str(tmp_path / "x.pyc"), doraise=True)
