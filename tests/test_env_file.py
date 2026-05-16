"""Tests for gantt_lib.env_file."""
from __future__ import annotations

import os

import pytest

from gantt_lib.env_file import load_env_file


def test_loads_simple_key_value_pairs(tmp_path, monkeypatch):
    monkeypatch.delenv("FOO", raising=False)
    monkeypatch.delenv("BAR", raising=False)
    env = tmp_path / ".env"
    env.write_text("FOO=hello\nBAR=world\n")
    count = load_env_file(env)
    assert count == 2
    assert os.environ["FOO"] == "hello"
    assert os.environ["BAR"] == "world"


def test_missing_file_is_noop(tmp_path):
    count = load_env_file(tmp_path / "does-not-exist")
    assert count == 0


def test_does_not_overwrite_existing_env(tmp_path, monkeypatch):
    monkeypatch.setenv("ALREADY_SET", "shell-value")
    env = tmp_path / ".env"
    env.write_text("ALREADY_SET=file-value\n")
    count = load_env_file(env)
    assert count == 0
    assert os.environ["ALREADY_SET"] == "shell-value"


def test_skips_comments_and_blanks(tmp_path, monkeypatch):
    monkeypatch.delenv("REAL", raising=False)
    env = tmp_path / ".env"
    env.write_text("# this is a comment\n\nREAL=yes\n   \n")
    assert load_env_file(env) == 1
    assert os.environ["REAL"] == "yes"


def test_strips_export_prefix(tmp_path, monkeypatch):
    monkeypatch.delenv("EXPORTED", raising=False)
    env = tmp_path / ".env"
    env.write_text("export EXPORTED=ok\n")
    assert load_env_file(env) == 1
    assert os.environ["EXPORTED"] == "ok"


def test_warns_on_malformed_line(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("GOOD", raising=False)
    env = tmp_path / ".env"
    env.write_text("not_a_pair_line\nGOOD=yes\n")
    count = load_env_file(env)
    assert count == 1  # only GOOD got set
    err = capsys.readouterr().err
    assert "malformed" in err
    assert "no `=`" in err


def test_gantt_env_file_override(tmp_path, monkeypatch):
    monkeypatch.delenv("OVERRIDE_ME", raising=False)
    custom = tmp_path / "custom.env"
    custom.write_text("OVERRIDE_ME=via-override\n")
    monkeypatch.setenv("GANTT_ENV_FILE", str(custom))
    assert load_env_file() == 1
    assert os.environ["OVERRIDE_ME"] == "via-override"
