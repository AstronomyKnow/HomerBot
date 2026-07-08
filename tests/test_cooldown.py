from pathlib import Path


def test_economy_cog_does_not_handle_cooldown_directly():
    source = Path(__file__).resolve().parents[1] / "cogs" / "economy.py"
    text = source.read_text(encoding="utf-8")

    assert "if isinstance(error, commands.CommandOnCooldown):" not in text
    assert "if isinstance(error, discord.app_commands.CommandOnCooldown):" not in text
