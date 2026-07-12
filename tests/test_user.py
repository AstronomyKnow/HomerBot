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
    def __init__(self, id_, roles=None):
        self.id = id_
        self.roles = roles or []
        self.mention = f"<@{id_}>"


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