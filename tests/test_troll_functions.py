import asyncio
import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location("troll", Path(__file__).resolve().parents[1] / "cogs" / "troll_functions.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


class FakeBot:
    def get_emoji(self, emoji_id):
        return None


class FakeAuthor:
    def __init__(self, user_id, is_bot=False):
        self.id = user_id
        self.bot = is_bot


class FakeMessage:
    def __init__(self, author_id, content, is_bot=False):
        self.author = FakeAuthor(author_id, is_bot)
        self.content = content
        self.reactions_added = []

    async def add_reaction(self, emoji):
        self.reactions_added.append(emoji)


def make_cog():
    cog = mod.TrollFunctions.__new__(mod.TrollFunctions)
    cog.bot = FakeBot()
    return cog


def test_troll_target_user_always_gets_monkey_reaction():
    cog = make_cog()
    message = FakeMessage(mod.TROLL_TARGET_USER_ID, "cualquier cosa random")
    asyncio.run(cog.on_message(message))
    assert mod.MONKEY_UNICODE_EMOJI in message.reactions_added


def test_other_users_do_not_get_monkey_reaction():
    cog = make_cog()
    message = FakeMessage(999, "cualquier cosa random")
    asyncio.run(cog.on_message(message))
    assert mod.MONKEY_UNICODE_EMOJI not in message.reactions_added


def test_message_containing_peru_triggers_reaction():
    cog = make_cog()
    message = FakeMessage(999, "Peru es peruano")
    asyncio.run(cog.on_message(message))
    assert mod.PERU_FLAG_EMOJI in message.reactions_added


def test_message_with_accented_peru_still_triggers():
    cog = make_cog()
    message = FakeMessage(999, "amo el Perú")
    asyncio.run(cog.on_message(message))
    assert mod.PERU_FLAG_EMOJI in message.reactions_added


def test_message_with_pero_does_not_trigger():
    cog = make_cog()
    message = FakeMessage(999, "no tengo tilde pero no debería activarse")
    asyncio.run(cog.on_message(message))
    assert mod.PERU_FLAG_EMOJI not in message.reactions_added


def test_detection_is_case_insensitive():
    cog = make_cog()
    message = FakeMessage(999, "PERU mayusculas")
    asyncio.run(cog.on_message(message))
    assert mod.PERU_FLAG_EMOJI in message.reactions_added


def test_message_without_peru_does_not_trigger():
    cog = make_cog()
    message = FakeMessage(999, "hola mundo")
    asyncio.run(cog.on_message(message))
    assert mod.PERU_FLAG_EMOJI not in message.reactions_added


def test_bot_messages_are_ignored():
    cog = make_cog()
    message = FakeMessage(mod.TROLL_TARGET_USER_ID, "peru", is_bot=True)
    asyncio.run(cog.on_message(message))
    assert message.reactions_added == []


def test_custom_emoji_ids_are_configured():
    assert mod.PERU_MONKEY_EMOJI_ID == 1527109146334204047
    assert mod.PERUKONG_EMOJI_ID == 1395853256772817057
    assert mod.PERU_MONKEY_EMOJI_ID != mod.PERUKONG_EMOJI_ID