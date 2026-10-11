"""The three .env readers (laptop, relay, memory): trailing comments, quotes with '#', existing env wins."""

import os

import pytest

from extras.memory.config import load_dotenv as memory_load
from laptop.settings import load_dotenv as laptop_load
from relay.relay import load_dotenv as relay_load


@pytest.mark.parametrize("load", [laptop_load, relay_load, memory_load], ids=["laptop", "relay", "memory"])
def test_quotes_comments_and_precedence(load, tmp_path, monkeypatch):
    for k in ("A", "B", "C", "D", "E", "F", "G"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("F", "from-env")
    p = tmp_path / ".env"
    p.write_text('A=plain   # comment\nB="quoted # not a comment"  # real comment\n'
                 "C='single#hash'\nD=no#space\n# E=commented\nE=\nF=from-file\n"
                 "G=127.0.0.1       # Caddy terminates TLS in front\n", encoding="utf-8")
    load(p)
    assert os.environ["A"] == "plain"
    assert os.environ["B"] == "quoted # not a comment"
    assert os.environ["C"] == "single#hash"
    assert os.environ["D"] == "no#space"
    assert os.environ["E"] == ""
    assert os.environ["F"] == "from-env"
    assert os.environ["G"] == "127.0.0.1"
