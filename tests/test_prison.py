import asyncio
import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("economy", Path(__file__).resolve().parents[1] / "cogs" / "economy.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


class FakeBot:
    def get_emoji(self, emoji_id):
        return None

    def get_cog(self, name):
        return None


class FakeRole:
    def __init__(self, id_):
        self.id = id_


class FakeGuild:
    def __init__(self):
        self.id = 999
        self._roles = {}
        self._members = {}

    def get_role(self, role_id):
        return self._roles.get(role_id)

    def get_member(self, user_id):
        return self._members.get(user_id)


class FakeMember:
    def __init__(self, user_id, roles=None):
        self.id = user_id
        self.roles = roles or []
        self.mention = f"<@{user_id}>"
        self.dm_channel = None

    async def create_dm(self):
        class FakeDM:
            async def send(self, *a, **k):
                pass
        return FakeDM()

    async def remove_roles(self, role, reason=None):
        if role in self.roles:
            self.roles.remove(role)

    async def add_roles(self, role, reason=None):
        if role not in self.roles:
            self.roles.append(role)


@pytest.fixture
def cog(tmp_path, monkeypatch):
    db_path = tmp_path / "prison.db"
    monkeypatch.setattr(mod, "resolve_database_paths", lambda name: (db_path, None))
    prison_cog = mod.Economy.__new__(mod.Economy)
    prison_cog.bot = FakeBot()
    prison_cog.db_path = str(tmp_path / "economy.db")
    prison_cog.prison_db_path = str(db_path)
    prison_cog.staff_roles = []
    prison_cog.initialize_database()
    prison_cog.initialize_prison_database()
    return prison_cog


@pytest.fixture
def guild_and_member():
    guild = FakeGuild()
    free_role = FakeRole(mod.FREE_ROLE_ID)
    prison_role = FakeRole(mod.PRISON_ROLE_ID)
    guild._roles[mod.FREE_ROLE_ID] = free_role
    guild._roles[mod.PRISON_ROLE_ID] = prison_role
    member = FakeMember(123, roles=[free_role])
    guild._members[123] = member
    return guild, member, free_role, prison_role


def test_jail_member_swaps_roles(cog, guild_and_member):
    guild, member, free_role, prison_role = guild_and_member

    asyncio.run(cog.jail_member(member, guild, 3600, "Prueba", notify=False))

    assert prison_role in member.roles
    assert free_role not in member.roles
    assert cog.is_in_prison(member) is True
    assert cog.get_prisoner(123) is not None


def test_jail_member_extends_existing_sentence(cog, guild_and_member):
    guild, member, free_role, prison_role = guild_and_member

    asyncio.run(cog.jail_member(member, guild, 3600, "Primera", notify=False))
    first_release = cog.get_prisoner(123)[2]

    asyncio.run(cog.jail_member(member, guild, 3600, "Segunda", notify=False))
    second_release = cog.get_prisoner(123)[2]

    assert second_release > first_release


def test_release_member_swaps_roles_back(cog, guild_and_member):
    guild, member, free_role, prison_role = guild_and_member

    asyncio.run(cog.jail_member(member, guild, 3600, "Prueba", notify=False))
    asyncio.run(cog.release_member(member, guild, "Liberado"))

    assert cog.get_prisoner(123) is None
    assert free_role in member.roles
    assert prison_role not in member.roles


def test_release_converts_illegal_money_to_legal_wallet(cog, guild_and_member):
    guild, member, free_role, prison_role = guild_and_member

    asyncio.run(cog.jail_member(member, guild, 3600, "Prueba", notify=False))
    cog.update_illegal_balance(123, 1500)
    assert cog.get_illegal_data(123)["balance"] == 1500

    wallet_before = cog.get_user_data(123)["wallet"]
    asyncio.run(cog.release_member(member, guild, "Liberado"))

    assert cog.get_illegal_data(123)["balance"] == 0
    wallet_after = cog.get_user_data(123)["wallet"]
    assert wallet_after == wallet_before + 1500


def test_illegal_balance_never_goes_negative(cog):
    assert cog.get_illegal_data(123) == {"balance": 0, "hitmen": 0}
    cog.update_illegal_balance(123, 500)
    cog.update_illegal_balance(123, -200)
    assert cog.get_illegal_data(123)["balance"] == 300
    cog.update_illegal_balance(123, -10000)
    assert cog.get_illegal_data(123)["balance"] == 0


def test_hitman_purchase_and_consumption(cog):
    cog.add_hitman(123, 2)
    assert cog.get_illegal_data(123)["hitmen"] == 2
    assert cog.consume_hitman(123) is True
    assert cog.get_illegal_data(123)["hitmen"] == 1
    assert cog.consume_hitman(123) is True
    assert cog.consume_hitman(123) is False  # ya no quedan


def test_hitman_contract_is_single_use(cog):
    cog.create_contract(thief_id=123, victim_id=456, guild_id=999)
    assert cog.consume_contract(123, 456) is True
    assert cog.consume_contract(123, 456) is False


def test_schedule_cycle_is_valid():
    assert mod.current_schedule() in mod.SCHEDULE_CYCLE
    assert 0 <= mod.seconds_left_in_schedule() <= 3600


def test_market_cycle_is_consistent():
    open_now, remaining = mod.market_seconds_remaining()
    assert isinstance(open_now, bool)
    assert 0 <= remaining <= mod.MARKET_CYCLE_SECONDS


def test_parse_duration_to_seconds():
    assert mod.parse_duration_to_seconds("30m") == 1800
    assert mod.parse_duration_to_seconds("12h") == 43200
    assert mod.parse_duration_to_seconds("2d") == 172800
    assert mod.parse_duration_to_seconds("1a") == 31536000
    assert mod.parse_duration_to_seconds("abc") is None
    assert mod.parse_duration_to_seconds("-5h") is None


def test_has_staff_role(cog):
    staff_member = FakeMember(1, roles=[FakeRole(mod.STAFF_ROLE_IDS[0])])
    regular_member = FakeMember(2, roles=[FakeRole(999999)])
    assert cog.has_staff_role(staff_member) is True
    assert cog.has_staff_role(regular_member) is False