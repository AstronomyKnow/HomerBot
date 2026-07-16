import asyncio
import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location("user_cog", Path(__file__).resolve().parents[1] / "cogs" / "user.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


class FakeBot:
    def get_emoji(self, emoji_id):
        return None


class FakeRole:
    def __init__(self, id_):
        self.id = id_


class FakeMember:
    def __init__(self, id_, roles=None, is_bot=False):
        self.id = id_
        self.roles = roles or []
        self.mention = f"<@{id_}>"
        self.bot = is_bot

    def __str__(self):
        return f"user{self.id}"


class FakeThread:
    def __init__(self):
        self.mention = "<#thread123>"
        self.added_users = []
        self.sent = []

    async def add_user(self, user):
        self.added_users.append(user)

    async def send(self, *args, **kwargs):
        self.sent.append(kwargs)


class FakeMessage:
    def __init__(self):
        self.deleted = False

    async def delete(self):
        self.deleted = True


class FakeChannel:
    def __init__(self):
        self.created_thread = None
        self.thread_kwargs = None

    async def create_thread(self, **kwargs):
        self.thread_kwargs = kwargs
        self.created_thread = FakeThread()
        return self.created_thread


class FakeCtx:
    def __init__(self, author, channel, message=None, interaction=None):
        self.author = author
        self.channel = channel
        self.message = message
        self.interaction = interaction
        self.sent = []

    async def send(self, *args, **kwargs):
        self.sent.append(kwargs)


def make_cog():
    cog = mod.UserCommands.__new__(mod.UserCommands)
    cog.bot = FakeBot()
    return cog


def test_has_staff_role_true_for_staff_member():
    cog = make_cog()
    staff_member = FakeMember(1, roles=[FakeRole(mod.STAFF_ROLE_IDS[0])])
    assert cog.has_staff_role(staff_member) is True


def test_has_staff_role_false_for_regular_member():
    cog = make_cog()
    regular_member = FakeMember(2, roles=[FakeRole(999999)])
    assert cog.has_staff_role(regular_member) is False


def test_has_staff_role_false_with_no_roles():
    cog = make_cog()
    no_roles_member = FakeMember(3, roles=[])
    assert cog.has_staff_role(no_roles_member) is False


def test_msg_rejects_sending_to_self():
    cog = make_cog()
    author = FakeMember(1)
    channel = FakeChannel()
    ctx = FakeCtx(author, channel, message=FakeMessage())

    asyncio.run(cog.msg_prefix(ctx, author, message="hola"))

    assert "ti mismo" in ctx.sent[0]["embed"].description
    assert channel.created_thread is None


def test_msg_rejects_sending_to_a_bot():
    cog = make_cog()
    author = FakeMember(1)
    bot_target = FakeMember(2, is_bot=True)
    channel = FakeChannel()
    ctx = FakeCtx(author, channel, message=FakeMessage())

    asyncio.run(cog.msg_prefix(ctx, bot_target, message="hola"))

    assert "bot" in ctx.sent[0]["embed"].description
    assert channel.created_thread is None


def test_msg_creates_private_thread_and_deletes_original_message():
    cog = make_cog()
    author = FakeMember(1)
    target = FakeMember(2)
    channel = FakeChannel()
    message = FakeMessage()
    ctx = FakeCtx(author, channel, message=message)

    asyncio.run(cog.msg_prefix(ctx, target, message="Este es un mensaje secreto"))

    assert message.deleted is True
    assert channel.thread_kwargs["type"] == mod.discord.ChannelType.private_thread
    assert channel.thread_kwargs["invitable"] is False

    thread = channel.created_thread
    assert author in thread.added_users
    assert target in thread.added_users

    sent_kwargs = thread.sent[0]
    assert sent_kwargs["content"] == target.mention
    assert sent_kwargs["embed"].description == "Este es un mensaje secreto"