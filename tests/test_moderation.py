import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location("moderation", Path(__file__).resolve().parents[1] / "cogs" / "moderation.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def test_parse_duration_supports_suffixes():
    moderation = mod.Moderation.__new__(mod.Moderation)
    assert moderation.parse_duration("5m") == (300, "5m")
    assert moderation.parse_duration("2h") == (7200, "2h")
    assert moderation.parse_duration("3d") == (259200, "3d")
    assert moderation.parse_duration("1a") == (31536000, "1a")
