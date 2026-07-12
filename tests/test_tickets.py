import importlib.util
import sqlite3
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("tickets", Path(__file__).resolve().parents[1] / "cogs" / "tickets.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


class FakeLoop:
    def create_task(self, coro):
        coro.close()
        return None


class FakeBot:
    def __init__(self):
        self.loop = FakeLoop()


@pytest.fixture
def cog(tmp_path, monkeypatch):
    db_path = tmp_path / "tickets.db"
    monkeypatch.setattr(mod, "resolve_database_paths", lambda name: (db_path, None))
    return mod.Tickets(FakeBot())


def test_ticket_numbering_is_sequential(cog):
    first = cog.create_ticket_row(guild_id=1, creator_id=100)
    second = cog.create_ticket_row(guild_id=1, creator_id=200)
    assert first == 1
    assert second == 2


def test_duplicate_open_ticket_is_detected(cog):
    ticket_id = cog.create_ticket_row(guild_id=1, creator_id=100)
    cog.set_ticket_channel(ticket_id, 999)

    existing = cog.get_open_ticket_for_user(1, 100)
    assert existing == (ticket_id, 999)

    # Un usuario distinto no debe verse afectado
    assert cog.get_open_ticket_for_user(1, 200) is None


def test_closed_ticket_no_longer_counts_as_open(cog):
    ticket_id = cog.create_ticket_row(guild_id=1, creator_id=100)
    cog.set_ticket_channel(ticket_id, 999)
    cog.set_ticket_description(ticket_id, "Un problema cualquiera")
    cog.set_ticket_closed(ticket_id, "Resuelto")

    assert cog.get_open_ticket_for_user(1, 100) is None
    row = cog.get_ticket(ticket_id)
    assert row[4] == "closed"
    assert row[8] == "Resuelto"


def test_full_ticket_lifecycle(cog):
    ticket_id = cog.create_ticket_row(guild_id=1, creator_id=100)
    cog.set_ticket_channel(ticket_id, 999)

    cog.set_ticket_description(ticket_id, "No puedo acceder a mi rol")
    row = cog.get_ticket(ticket_id)
    assert row[4] == "open"
    assert row[5] == "No puedo acceder a mi rol"

    cog.set_ticket_claimed(ticket_id, moderator_id=555)
    row = cog.get_ticket(ticket_id)
    assert row[4] == "claimed"
    assert row[6] == 555

    cog.set_ticket_awaiting_close(ticket_id, user_id=555)
    row = cog.get_ticket(ticket_id)
    assert row[7] == 555

    cog.set_ticket_closed(ticket_id, "Solucionado por el moderador")
    row = cog.get_ticket(ticket_id)
    assert row[4] == "closed"
    assert row[7] is None
    assert row[8] == "Solucionado por el moderador"


def test_panel_message_upsert(cog):
    cog.save_panel_message(guild_id=1, channel_id=5000, message_id=6000)
    assert cog.get_panel_message(1) == (5000, 6000)

    # Si se recrea el panel (por ejemplo tras borrarlo manualmente), debe
    # actualizar el message_id en vez de duplicar la fila.
    cog.save_panel_message(guild_id=1, channel_id=5000, message_id=7000)
    assert cog.get_panel_message(1) == (5000, 7000)

    conn = sqlite3.connect(cog.db_path)
    count = conn.execute("SELECT COUNT(*) FROM ticket_panel WHERE guild_id = 1").fetchone()[0]
    conn.close()
    assert count == 1


def test_has_staff_role(cog):
    class FakeRole:
        def __init__(self, id_):
            self.id = id_

    class FakeMember:
        def __init__(self, role_ids):
            self.roles = [FakeRole(r) for r in role_ids]

    staff_member = FakeMember([mod.STAFF_ROLE_IDS[0], 999999])
    regular_member = FakeMember([111111, 222222])

    assert cog.has_staff_role(staff_member) is True
    assert cog.has_staff_role(regular_member) is False