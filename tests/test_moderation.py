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


def test_parse_duration_permanent_ban():
    moderation = mod.Moderation.__new__(mod.Moderation)
    assert moderation.parse_duration("p", allow_permanent=True) == (None, "Permanente")


def test_parse_duration_rejects_permanent_when_not_allowed():
    moderation = mod.Moderation.__new__(mod.Moderation)
    import pytest
    with pytest.raises(ValueError):
        moderation.parse_duration("p")


def test_warn_and_unwarn_lifecycle(tmp_path, monkeypatch):
    monkeypatch.setattr(mod, "resolve_database_paths", lambda name: (tmp_path / name, None))

    class FakeBot:
        def get_emoji(self, emoji_id):
            return None

    moderation = mod.Moderation.__new__(mod.Moderation)
    moderation.bot = FakeBot()
    moderation.log_channel_id = 999
    moderation.db_path = str(tmp_path / "moderation.db")
    moderation.allowed_say_roles = [1, 2, 3]
    moderation.allowed_info_roles = [1, 2, 3, 4, 5]
    moderation.initialize_database()

    moderation.warn_user(guild_id=10, user_id=20, moderator_id=30, reason="Spam", amount=3)
    warnings = moderation.get_user_warnings(10, 20)
    assert len(warnings) == 3

    # El más reciente es el índice 0 (orden DESC), como se muestra en &warns
    warning_id, moderator_id, reason, created_at = warnings[0]
    assert moderation.delete_user_warning(10, 20, warning_id) is True

    remaining = moderation.get_user_warnings(10, 20)
    assert len(remaining) == 2
    assert warning_id not in [w[0] for w in remaining]

    # No debe poder borrarse dos veces, ni desde otro guild
    assert moderation.delete_user_warning(10, 20, warning_id) is False
    assert moderation.delete_user_warning(999, 20, remaining[0][0]) is False