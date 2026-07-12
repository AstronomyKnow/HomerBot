import asyncio
import datetime
import io
import sqlite3
from pathlib import Path

import discord
from discord.ext import commands

from backup_economy import resolve_database_paths


TICKETS_CHANNEL_ID = 1382823475437240482
STAFF_ROLE_IDS = [
    1372448974211911770,
    1359359923770757150,
    1361138268829253875,
    1501881069731840050,
    1362456351263035553,
]
LOG_CHANNEL_ID = 1393450057189167234


# --- UI COMPONENTS ---

class CreateTicketButton(discord.ui.Button):
    def __init__(self, cog: "Tickets"):
        super().__init__(
            label="Crear ticket",
            emoji="🎫",
            style=discord.ButtonStyle.primary,
            custom_id="tickets:create",
        )
        self.cog = cog

    async def callback(self, interaction: discord.Interaction):
        await self.cog.create_ticket(interaction)


class TicketPanelView(discord.ui.View):
    def __init__(self, cog: "Tickets"):
        super().__init__(timeout=None)
        self.add_item(CreateTicketButton(cog))


class ClaimButton(discord.ui.Button):
    def __init__(self, cog: "Tickets", ticket_id: int, claimed: bool = False):
        super().__init__(
            label="Reclamado" if claimed else "Reclamar",
            emoji="🙋",
            style=discord.ButtonStyle.success,
            custom_id=f"tickets:claim:{ticket_id}",
            disabled=claimed,
        )
        self.cog = cog
        self.ticket_id = ticket_id

    async def callback(self, interaction: discord.Interaction):
        await self.cog.handle_claim(interaction, self.ticket_id)


class CloseButton(discord.ui.Button):
    def __init__(self, cog: "Tickets", ticket_id: int):
        super().__init__(
            label="Cerrar",
            emoji="🔒",
            style=discord.ButtonStyle.danger,
            custom_id=f"tickets:close:{ticket_id}",
        )
        self.cog = cog
        self.ticket_id = ticket_id

    async def callback(self, interaction: discord.Interaction):
        await self.cog.handle_close_request(interaction, self.ticket_id)


class TicketActionView(discord.ui.View):
    def __init__(self, cog: "Tickets", ticket_id: int, claimed: bool = False):
        super().__init__(timeout=None)
        self.add_item(ClaimButton(cog, ticket_id, claimed=claimed))
        self.add_item(CloseButton(cog, ticket_id))


# --- COG ---

class Tickets(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.tickets_channel_id = TICKETS_CHANNEL_ID
        self.staff_role_ids = STAFF_ROLE_IDS
        self.log_channel_id = LOG_CHANNEL_ID
        self.db_path = str(resolve_database_paths("tickets.db")[0])
        self.initialize_database()
        self.bot.loop.create_task(self.on_startup())

    # --- DATABASE SETUP ---

    def initialize_database(self):
        db_path = Path(self.db_path)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(db_path))
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS tickets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                channel_id INTEGER,
                creator_id INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'awaiting_description',
                description TEXT,
                claimed_by INTEGER,
                closing_user_id INTEGER,
                close_reason TEXT,
                created_at TEXT NOT NULL,
                claimed_at TEXT,
                closed_at TEXT
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS ticket_panel (
                guild_id INTEGER PRIMARY KEY,
                channel_id INTEGER NOT NULL,
                message_id INTEGER NOT NULL
            )
        """)
        conn.commit()
        conn.close()

    def _connect(self):
        return sqlite3.connect(self.db_path)

    # --- DB HELPERS ---

    def create_ticket_row(self, guild_id, creator_id):
        conn = self._connect()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO tickets (guild_id, creator_id, status, created_at) VALUES (?, ?, 'awaiting_description', ?)",
            (guild_id, creator_id, datetime.datetime.now(datetime.timezone.utc).isoformat())
        )
        ticket_id = cursor.lastrowid
        conn.commit()
        conn.close()
        return ticket_id

    def set_ticket_channel(self, ticket_id, channel_id):
        conn = self._connect()
        cursor = conn.cursor()
        cursor.execute("UPDATE tickets SET channel_id = ? WHERE id = ?", (channel_id, ticket_id))
        conn.commit()
        conn.close()

    def get_ticket(self, ticket_id):
        conn = self._connect()
        cursor = conn.cursor()
        cursor.execute(
            """SELECT id, guild_id, channel_id, creator_id, status, description, claimed_by,
                      closing_user_id, close_reason, created_at, claimed_at, closed_at
               FROM tickets WHERE id = ?""",
            (ticket_id,)
        )
        row = cursor.fetchone()
        conn.close()
        return row

    def get_ticket_by_channel(self, channel_id):
        conn = self._connect()
        cursor = conn.cursor()
        cursor.execute(
            """SELECT id, guild_id, channel_id, creator_id, status, description, claimed_by,
                      closing_user_id, close_reason, created_at, claimed_at, closed_at
               FROM tickets WHERE channel_id = ?""",
            (channel_id,)
        )
        row = cursor.fetchone()
        conn.close()
        return row

    def get_open_ticket_for_user(self, guild_id, user_id):
        conn = self._connect()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, channel_id FROM tickets WHERE guild_id = ? AND creator_id = ? AND status != 'closed' ORDER BY id DESC LIMIT 1",
            (guild_id, user_id)
        )
        row = cursor.fetchone()
        conn.close()
        return row

    def set_ticket_description(self, ticket_id, description):
        conn = self._connect()
        cursor = conn.cursor()
        cursor.execute("UPDATE tickets SET description = ?, status = 'open' WHERE id = ?", (description, ticket_id))
        conn.commit()
        conn.close()

    def set_ticket_claimed(self, ticket_id, moderator_id):
        conn = self._connect()
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE tickets SET status = 'claimed', claimed_by = ?, claimed_at = ? WHERE id = ?",
            (moderator_id, datetime.datetime.now(datetime.timezone.utc).isoformat(), ticket_id)
        )
        conn.commit()
        conn.close()

    def set_ticket_awaiting_close(self, ticket_id, user_id):
        conn = self._connect()
        cursor = conn.cursor()
        cursor.execute("UPDATE tickets SET closing_user_id = ? WHERE id = ?", (user_id, ticket_id))
        conn.commit()
        conn.close()

    def set_ticket_closed(self, ticket_id, reason):
        conn = self._connect()
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE tickets SET status = 'closed', close_reason = ?, closed_at = ?, closing_user_id = NULL WHERE id = ?",
            (reason, datetime.datetime.now(datetime.timezone.utc).isoformat(), ticket_id)
        )
        conn.commit()
        conn.close()

    def get_panel_message(self, guild_id):
        conn = self._connect()
        cursor = conn.cursor()
        cursor.execute("SELECT channel_id, message_id FROM ticket_panel WHERE guild_id = ?", (guild_id,))
        row = cursor.fetchone()
        conn.close()
        return row

    def save_panel_message(self, guild_id, channel_id, message_id):
        conn = self._connect()
        cursor = conn.cursor()
        cursor.execute(
            """INSERT INTO ticket_panel (guild_id, channel_id, message_id) VALUES (?, ?, ?)
               ON CONFLICT(guild_id) DO UPDATE SET channel_id = excluded.channel_id, message_id = excluded.message_id""",
            (guild_id, channel_id, message_id)
        )
        conn.commit()
        conn.close()

    def get_open_and_claimed_tickets(self):
        conn = self._connect()
        cursor = conn.cursor()
        cursor.execute("SELECT id, status FROM tickets WHERE status IN ('open', 'claimed')")
        rows = cursor.fetchall()
        conn.close()
        return rows

    # --- STARTUP: restaurar vistas persistentes y el panel ---

    async def on_startup(self):
        await self.bot.wait_until_ready()

        for ticket_id, status in self.get_open_and_claimed_tickets():
            self.bot.add_view(TicketActionView(self, ticket_id, claimed=(status == "claimed")))

        self.bot.add_view(TicketPanelView(self))

        channel = self.bot.get_channel(self.tickets_channel_id)
        if not channel:
            print(f"Advertencia: no se encontró el canal de tickets con ID {self.tickets_channel_id}.")
            return

        guild = channel.guild
        panel = self.get_panel_message(guild.id)
        needs_new_panel = True
        if panel:
            _, message_id = panel
            try:
                await channel.fetch_message(message_id)
                needs_new_panel = False
            except (discord.NotFound, discord.HTTPException):
                needs_new_panel = True

        if needs_new_panel:
            embed = discord.Embed(
                title="🎫 Centro de soporte",
                description=(
                    "¿Tienes un problema, una duda o necesitas reportar algo?\n\n"
                    "Presiona el botón de abajo para abrir un ticket privado. Un miembro de nuestro "
                    "equipo de moderación revisará tu caso y te atenderá lo antes posible."
                ),
                color=discord.Color.gold(),
            )
            embed.set_footer(text="Solo tú y el equipo de moderación podrán ver el canal que se cree.")
            message = await channel.send(embed=embed, view=TicketPanelView(self))
            self.save_panel_message(guild.id, channel.id, message.id)

    # --- HELPERS ---

    def has_staff_role(self, member: discord.Member) -> bool:
        return any(role.id in self.staff_role_ids for role in member.roles)

    async def get_log_channel(self, guild: discord.Guild):
        channel = guild.get_channel(self.log_channel_id)
        return channel or guild.system_channel

    def build_ticket_overwrites(self, guild: discord.Guild, creator: discord.Member):
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            creator: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, attach_files=True),
        }
        for role_id in self.staff_role_ids:
            role = guild.get_role(role_id)
            if role:
                overwrites[role] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)
        return overwrites

    # --- CREACIÓN DE TICKETS ---

    async def create_ticket(self, interaction: discord.Interaction):
        guild = interaction.guild
        creator = interaction.user

        existing = self.get_open_ticket_for_user(guild.id, creator.id)
        if existing:
            _, existing_channel_id = existing
            existing_channel = guild.get_channel(existing_channel_id)
            if existing_channel:
                await interaction.response.send_message(
                    embed=discord.Embed(
                        title="⚠️ Ya tienes un ticket abierto",
                        description=f"Ya tienes un ticket en curso: {existing_channel.mention}",
                        color=discord.Color.red(),
                    ),
                    ephemeral=True,
                )
                return

        await interaction.response.defer(ephemeral=True)

        ticket_id = self.create_ticket_row(guild.id, creator.id)
        overwrites = self.build_ticket_overwrites(guild, creator)
        category = interaction.channel.category if interaction.channel else None

        channel = await guild.create_text_channel(
            name=f"ticket_{ticket_id}",
            category=category,
            overwrites=overwrites,
            reason=f"Ticket creado por {creator} ({creator.id})",
        )
        try:
            await channel.move(end=True, sync_permissions=False)
        except (discord.HTTPException, discord.Forbidden):
            pass

        self.set_ticket_channel(ticket_id, channel.id)

        embed = discord.Embed(
            title=f"🎫 Ticket #{ticket_id} creado correctamente",
            description=(
                f"¡Hola {creator.mention}! Antes de avisar al equipo, cuéntanos en **un solo mensaje** "
                "de qué trata tu ticket. Sé lo más descriptivo posible:\n\n"
                "• Explica tu situación o problema con detalle.\n"
                "• Menciona a los usuarios involucrados, si corresponde.\n"
                "• Si es un error técnico, indica cómo reproducirlo y bajo qué condiciones ocurre.\n\n"
                "⚠️ Este mensaje debe ser **solo texto**: no incluyas imágenes, archivos adjuntos ni stickers."
            ),
            color=discord.Color.gold(),
        )
        await channel.send(content=creator.mention, embed=embed)

        await interaction.followup.send(
            embed=discord.Embed(
                title="✅ Ticket creado",
                description=f"Tu ticket fue creado con éxito: {channel.mention}",
                color=discord.Color.green(),
            ),
            ephemeral=True,
        )

    # --- DESCRIPCIÓN INICIAL DEL TICKET ---

    async def handle_description_message(self, message: discord.Message, ticket_row):
        ticket_id = ticket_row[0]
        channel = message.channel

        if message.attachments or message.stickers or not message.content.strip():
            try:
                await message.delete()
            except (discord.Forbidden, discord.NotFound):
                pass
            warning = await channel.send(
                embed=discord.Embed(
                    title="❌ Mensaje inválido",
                    description="Tu mensaje debe ser **solo texto** (sin imágenes, archivos ni stickers). Por favor, inténtalo de nuevo.",
                    color=discord.Color.red(),
                )
            )
            await asyncio.sleep(8)
            try:
                await warning.delete()
            except (discord.Forbidden, discord.NotFound):
                pass
            return

        description = message.content.strip()
        self.set_ticket_description(ticket_id, description)

        try:
            await channel.purge(limit=None)
        except (discord.Forbidden, discord.HTTPException):
            pass

        creator_id = ticket_row[3]
        creator = channel.guild.get_member(creator_id)
        if creator:
            try:
                await channel.set_permissions(creator, view_channel=True, send_messages=False, read_message_history=True)
            except (discord.Forbidden, discord.HTTPException):
                pass

        embed = discord.Embed(
            title="✅ Ticket creado completamente",
            description=(
                f"**Ticket:** {description}\n\n"
                "⚠️ Por favor, no menciones a ningún miembro del personal hasta que un moderador reclame este ticket."
            ),
            color=discord.Color.gold(),
        )
        embed.set_footer(text=f"Ticket #{ticket_id}")
        await channel.send(embed=embed, view=TicketActionView(self, ticket_id))

    # --- RECLAMAR ---

    async def handle_claim(self, interaction: discord.Interaction, ticket_id: int):
        ticket_row = self.get_ticket(ticket_id)
        if not ticket_row:
            await interaction.response.send_message("⚠️ Este ticket ya no existe.", ephemeral=True)
            return
        (_, guild_id, channel_id, creator_id, status, description, claimed_by,
         closing_user_id, close_reason, created_at, claimed_at, closed_at) = ticket_row

        member = interaction.user
        if member.id == creator_id or not self.has_staff_role(member):
            await interaction.response.send_message(
                embed=discord.Embed(title="❌ No eres un moderador.", color=discord.Color.red()),
                ephemeral=True,
            )
            return

        if status == "claimed":
            claimer = interaction.guild.get_member(claimed_by)
            await interaction.response.send_message(
                embed=discord.Embed(
                    title="⚠️ Ticket ya reclamado",
                    description=f"Este ticket ya fue reclamado por {claimer.mention if claimer else claimed_by}.",
                    color=discord.Color.red(),
                ),
                ephemeral=True,
            )
            return

        self.set_ticket_claimed(ticket_id, member.id)

        creator = interaction.guild.get_member(creator_id)
        if creator:
            try:
                await interaction.channel.set_permissions(creator, view_channel=True, send_messages=True, read_message_history=True)
            except (discord.Forbidden, discord.HTTPException):
                pass

        new_view = TicketActionView(self, ticket_id, claimed=True)
        await interaction.response.edit_message(view=new_view)

        await interaction.channel.send(
            embed=discord.Embed(
                title="🙋 Ticket reclamado",
                description=f"{member.mention} se hará cargo de este ticket a partir de ahora.",
                color=discord.Color.gold(),
            )
        )

    # --- CERRAR ---

    async def handle_close_request(self, interaction: discord.Interaction, ticket_id: int):
        ticket_row = self.get_ticket(ticket_id)
        if not ticket_row:
            await interaction.response.send_message("⚠️ Este ticket ya no existe.", ephemeral=True)
            return
        (_, guild_id, channel_id, creator_id, status, description, claimed_by,
         closing_user_id, close_reason, created_at, claimed_at, closed_at) = ticket_row

        member = interaction.user
        if member.id != creator_id and not self.has_staff_role(member):
            await interaction.response.send_message(
                embed=discord.Embed(title="❌ No tienes permiso para cerrar este ticket.", color=discord.Color.red()),
                ephemeral=True,
            )
            return

        self.set_ticket_awaiting_close(ticket_id, member.id)
        await interaction.response.send_message(
            embed=discord.Embed(
                title="📝 Razón de cierre",
                description=f"{member.mention}, escribe la razón del cierre en tu **siguiente mensaje** en este canal.",
                color=discord.Color.gold(),
            ),
        )

    async def handle_close_reason_message(self, message: discord.Message, ticket_row):
        reason = message.content.strip() or "No especificada"
        await self.finalize_ticket_closure(message.channel, ticket_row, reason, message.author)

    async def finalize_ticket_closure(self, channel: discord.TextChannel, ticket_row, reason, closer):
        (ticket_id, guild_id, channel_id, creator_id, status, description, claimed_by,
         closing_user_id, close_reason, created_at, claimed_at, closed_at) = ticket_row

        transcript_lines = []
        try:
            async for msg in channel.history(limit=None, oldest_first=True):
                author = f"{msg.author} ({msg.author.id})"
                timestamp = msg.created_at.strftime("%Y-%m-%d %H:%M:%S UTC")
                content = msg.content or "[sin contenido de texto]"
                if msg.embeds:
                    content += " [contiene un embed]"
                if msg.attachments:
                    content += f" [adjuntos: {', '.join(a.filename for a in msg.attachments)}]"
                transcript_lines.append(f"[{timestamp}] {author}: {content}")
        except (discord.Forbidden, discord.HTTPException):
            pass

        transcript_text = "\n".join(transcript_lines) or "No se pudo recuperar la conversación."
        transcript_file = discord.File(
            io.BytesIO(transcript_text.encode("utf-8")),
            filename=f"ticket_{ticket_id}_transcript.txt",
        )

        self.set_ticket_closed(ticket_id, reason)

        guild = channel.guild
        creator = guild.get_member(creator_id)
        claimer = guild.get_member(claimed_by) if claimed_by else None

        log_embed = discord.Embed(title=f"🔒 Ticket #{ticket_id} cerrado", color=discord.Color.gold())
        log_embed.add_field(name="Creado por", value=f"{creator.mention if creator else creator_id} ({creator_id})", inline=False)
        log_embed.add_field(name="Reclamado por", value=(claimer.mention if claimer else "Nadie reclamó este ticket"), inline=False)
        log_embed.add_field(name="Cerrado por", value=f"{closer.mention} ({closer.id})", inline=False)
        log_embed.add_field(name="Motivo de cierre", value=reason[:1024], inline=False)
        if description:
            log_embed.add_field(name="Descripción original", value=description[:1024], inline=False)
        log_embed.set_footer(text=f"Canal original: #{channel.name}")
        log_embed.timestamp = datetime.datetime.now(datetime.timezone.utc)

        log_channel = await self.get_log_channel(guild)
        if log_channel:
            try:
                await log_channel.send(embed=log_embed, file=transcript_file)
            except discord.HTTPException:
                pass

        try:
            await channel.send(
                embed=discord.Embed(
                    title="🔒 Cerrando ticket...",
                    description="Este canal se eliminará en unos segundos.",
                    color=discord.Color.gold(),
                )
            )
        except discord.HTTPException:
            pass

        await asyncio.sleep(5)
        try:
            await channel.delete(reason=f"Ticket cerrado por {closer} ({closer.id}). Motivo: {reason}")
        except (discord.Forbidden, discord.HTTPException):
            pass

    # --- LISTENER PRINCIPAL ---

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return

        ticket_row = self.get_ticket_by_channel(message.channel.id)
        if not ticket_row:
            return

        (ticket_id, guild_id, channel_id, creator_id, status, description, claimed_by,
         closing_user_id, close_reason, created_at, claimed_at, closed_at) = ticket_row

        if closing_user_id and message.author.id == closing_user_id:
            await self.handle_close_reason_message(message, ticket_row)
            return

        if status == "awaiting_description" and message.author.id == creator_id:
            await self.handle_description_message(message, ticket_row)


async def setup(bot):
    await bot.add_cog(Tickets(bot))