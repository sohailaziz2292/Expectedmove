import json
from datetime import date, datetime

from mmd import clock, publish
from mmd.config import session_dir


def test_locked_file_is_never_overwritten(tmp_path, monkeypatch):
    from mmd import collect, config
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(collect, "session_dir",
                        lambda d: config.session_dir(d))
    target = date(2026, 8, 7)

    first = {"predictions": [{"symbol": "AAA"}], "locked": False}
    collect.write(dict(first), target, lock=True)

    second = {"predictions": [{"symbol": "BBB"}], "locked": False}
    result = collect.write(dict(second), target, lock=False)

    assert result["predictions"][0]["symbol"] == "AAA"
    assert result["locked"] is True
