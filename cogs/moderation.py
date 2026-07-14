import asyncio
import datetime
import os
import sqlite3
import time
import shutil
from pathlib import Path

import discord
import psutil
from discord import app_commands
from discord.ext import commands

from backup_economy import resolve_database_paths
from emojis import ban_emoji, mute_emoji, error_emoji, success_emoji


class Moderation(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.log_channel_id = 1393450057189167234
        self.allowed_say_roles = [1372448974211911770, 1359359923770757150, 1361138268829253875]
        self.allowed_info_roles = [
            1372448974211911770,
            1359359923770757150,
            1361138268829253875,
            1501881069731840050,
            1362456351263035553,
        ]
        self.db_path = str(resolve_database_paths("moderation.db")[0])
        self.initialize_database()
        self.bot.loop.create_task(self.restore_sanctions())

    # --- INTERNAL UTILITY METHODS ---
    def has_say_role(self, user: discord.Member) -> bool:
        return any(role.id in self.allowed_say_roles for role in user.roles)

    def has_info_role(self, user: discord.Member) -> bool:
        return any(role.id in self.allowed_info_roles for role in user.roles)

    def initialize_database(self):
        db_path = Path(self.db_path)
        db_path.parent.mkdir(parents=True, exist_ok=True)

        legacy_path = Path.cwd() / "moderation.db"
        if not db_path.exists() and legacy_path.exists():
            shutil.copy2(legacy_path, db_path)

        backup_path = db_path.with_suffix(".db.backup")
        if not db_path.exists() and backup_path.exists():
            shutil.copy2(backup_path, db_path)

        conn = sqlite3.connect(str(db_path))
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS moderation_actions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                action TEXT NOT NULL,
                moderator_id INTEGER NOT NULL,
                reason TEXT,
                duration_seconds INTEGER,
                expires_at TEXT,
                active INTEGER DEFAULT 1,
                created_at TEXT NOT NULL
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS user_warnings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                moderator_id INTEGER NOT NULL,
                reason TEXT,
                created_at TEXT NOT NULL
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS member_first_join (
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                first_joined_at TEXT NOT NULL,
                PRIMARY KEY (guild_id, user_id)
            )
        """)
        conn.commit()
        conn.close()

    async def get_log_channel(self, guild):
        try:
            channel = guild.get_channel(self.log_channel_id)
            if channel:
                return channel
        except Exception:
            pass
        return guild.system_channel

    def choose_log_icon(self, action: str) -> str:
        lowered = action.lower()
        if "desbaneo" in lowered or "fin de aislamiento" in lowered:
            return success_emoji(self.bot)
        if "baneo" in lowered:
            return ban_emoji(self.bot)
        if "aislamiento" in lowered or "mute" in lowered:
            return mute_emoji(self.bot)
        return success_emoji(self.bot)

    async def send_log(self, guild, action, target, moderator, reason, duration=None, extra=None):
        try:
            channel = await self.get_log_channel(guild)
            if not channel:
                return

            icon = self.choose_log_icon(action)
            embed = discord.Embed(title=f"{icon} Registro de Moderación: {action}", color=discord.Color.dark_red())
            embed.add_field(name="Usuario afectado", value=str(target), inline=False)
            embed.add_field(name="Moderador responsable", value=str(moderator), inline=False)
            if duration:
                embed.add_field(name="Duración", value=str(duration), inline=False)
            embed.add_field(name="Motivo", value=reason or "No especificado", inline=False)
            if extra:
                embed.add_field(name="Detalle", value=str(extra), inline=False)
            embed.timestamp = datetime.datetime.now(datetime.timezone.utc)
            await channel.send(embed=embed)
        except Exception as e:
            print(f"Failed to send moderation log to Discord: {e}")

    async def send_embed_reply(self, destination, title, description, color=discord.Color.gold()):
        embed = discord.Embed(title=title, description=description, color=color)
        embed.timestamp = datetime.datetime.now(datetime.timezone.utc)
        await destination.send(embed=embed)

    def parse_duration(self, duration_input, allow_permanent=False):
        if duration_input is None:
            raise ValueError("La duración no puede estar vacía.")

        text = str(duration_input).strip().lower()
        if not text:
            raise ValueError("La duración no puede estar vacía.")

        if allow_permanent and text == "p":
            return None, "Permanente"

        if text.isdigit():
            minutes = int(text)
            if minutes <= 0:
                raise ValueError("La duración debe ser mayor a cero.")
            return minutes * 60, f"{minutes}m"

        if text.endswith("ms"):
            value = text[:-2]
            if not value.isdigit():
                raise ValueError("Formato inválido. Ejemplo: 5ms")
            months = int(value)
            if months <= 0:
                raise ValueError("La duración debe ser mayor a cero.")
            return months * 30 * 24 * 60 * 60, f"{months}ms"

        if text.endswith(("s", "m", "h", "d", "a")):
            value = text[:-1]
            if not value.isdigit():
                raise ValueError("Formato inválido. Usa ejemplos como 5s, 5m, 5h, 5d, 5ms o 5a")
            amount = int(value)
            if amount <= 0:
                raise ValueError("La duración debe ser mayor a cero.")

            multipliers = {
                "s": 1,
                "m": 60,
                "h": 60 * 60,
                "d": 24 * 60 * 60,
                "a": 365 * 24 * 60 * 60,
            }
            return amount * multipliers[text[-1]], text

        raise ValueError("Formato inválido. Usa ejemplos como 5s, 5m, 5h, 5d, 5ms o 5a")

    def get_member_from_input(self, guild, user_input):
        if not user_input or user_input == "@everyone":
            return None
        clean_id = user_input.replace("<@", "").replace(">", "").replace("!", "")
        try:
            return guild.get_member(int(clean_id))
        except ValueError:
            return None

    def store_action(self, guild_id, user_id, action, moderator_id, reason, duration_seconds=None, expires_at=None):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO moderation_actions (guild_id, user_id, action, moderator_id, reason, duration_seconds, expires_at, active, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?)
            """,
            (guild_id, user_id, action, moderator_id, reason, duration_seconds, expires_at, datetime.datetime.now(datetime.timezone.utc).isoformat())
        )
        conn.commit()
        conn.close()

    def deactivate_action(self, guild_id, user_id, action):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE moderation_actions SET active = 0 WHERE guild_id = ? AND user_id = ? AND action = ? AND active = 1",
            (guild_id, user_id, action)
        )
        conn.commit()
        conn.close()

    async def notify_sanction(self, member, action, moderator, reason, duration_text):
        if not member:
            return
        if action == "Baneo":
            icon = ban_emoji(self.bot)
        elif action == "Mute":
            icon = mute_emoji(self.bot)
        else:
            icon = "📢"
        embed = discord.Embed(title=f"{icon} Has recibido una sanción", color=discord.Color.yellow())
        embed.add_field(name="Sanción", value=action, inline=False)
        embed.add_field(name="Duración", value=duration_text, inline=False)
        embed.add_field(name="Razón", value=reason or "No especificado", inline=False)
        embed.add_field(name="Moderador responsable", value=str(moderator), inline=False)
        embed.timestamp = datetime.datetime.now(datetime.timezone.utc)
        try:
            dm_channel = member.dm_channel or await member.create_dm()
            await dm_channel.send(embed=embed)
        except (discord.Forbidden, discord.HTTPException) as error:
            # Discord no permite forzar un MD si el usuario tiene los mensajes
            # directos cerrados, bloqueó al bot, o ya no comparte servidores con él.
            # No hay forma de garantizar la entrega en esos casos; avisamos al staff.
            print(f"No se pudo enviar el MD de sanción a {member}: {error}")
            try:
                log_channel = await self.get_log_channel(member.guild)
                if log_channel:
                    warning_embed = discord.Embed(
                        title=f"{error_emoji(self.bot)} No se pudo notificar por MD",
                        description=(
                            f"No fue posible enviarle a {member.mention} ({member}) la notificación "
                            "de su sanción. Puede tener los MD cerrados, haber bloqueado al bot, "
                            "o ya no compartir servidores con él."
                        ),
                        color=discord.Color.red(),
                    )
                    await log_channel.send(embed=warning_embed)
            except Exception:
                pass

    async def schedule_action_expiration(self, guild, user_id, action, duration_seconds):
        if duration_seconds is None or duration_seconds <= 0:
            return
        await asyncio.sleep(duration_seconds)
        try:
            if action == "ban":
                user = await self.bot.fetch_user(user_id)
                await guild.unban(user, reason="Periodo de sanción concluido.")
                self.deactivate_action(guild.id, user_id, action)
                await self.send_log(guild, "Desbaneo automático", user, self.bot.user, "Periodo de sanción concluido.")
            elif action == "mute":
                member = guild.get_member(user_id)
                if member:
                    await member.timeout(None, reason="Periodo de sanción concluido.")
                self.deactivate_action(guild.id, user_id, action)
                if member:
                    await self.send_log(guild, "Fin de aislamiento automático", member, self.bot.user, "Periodo de sanción concluido.")
        except (discord.NotFound, discord.Forbidden):
            self.deactivate_action(guild.id, user_id, action)

    async def restore_sanctions(self):
        await self.bot.wait_until_ready()
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT guild_id, user_id, action, moderator_id, reason, duration_seconds, expires_at FROM moderation_actions WHERE active = 1")
        rows = cursor.fetchall()
        conn.close()

        now = datetime.datetime.now(datetime.timezone.utc)
        for guild_id, user_id, action, moderator_id, reason, duration_seconds, expires_at in rows:
            guild = self.bot.get_guild(guild_id)
            if not guild:
                continue
            if expires_at:
                expires_dt = datetime.datetime.fromisoformat(expires_at)
                if expires_dt <= now:
                    self.deactivate_action(guild.id, user_id, action)
                    continue
                delay = max(0, (expires_dt - now).total_seconds())
                asyncio.create_task(self.schedule_action_expiration(guild, user_id, action, delay))
                if action == "ban":
                    try:
                        await guild.ban(discord.Object(id=user_id), reason=reason or "Sanción restaurada")
                    except discord.HTTPException:
                        pass
                elif action == "mute":
                    member = guild.get_member(user_id)
                    if member:
                        timeout_duration = datetime.timedelta(seconds=max(0, int((expires_dt - now).total_seconds())))
                        await member.timeout(timeout_duration, reason=reason or "Sanción restaurada")
            else:
                if action == "ban":
                    try:
                        await guild.ban(discord.Object(id=user_id), reason=reason or "Sanción restaurada")
                    except discord.HTTPException:
                        pass

    def warn_user(self, guild_id, user_id, moderator_id, reason, amount=1):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        for _ in range(amount):
            cursor.execute(
                "INSERT INTO user_warnings (guild_id, user_id, moderator_id, reason, created_at) VALUES (?, ?, ?, ?, ?)",
                (guild_id, user_id, moderator_id, reason, datetime.datetime.now(datetime.timezone.utc).isoformat())
            )
        conn.commit()
        conn.close()

    def get_user_warnings(self, guild_id, user_id):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT id, moderator_id, reason, created_at FROM user_warnings WHERE guild_id = ? AND user_id = ? ORDER BY id DESC", (guild_id, user_id))
        rows = cursor.fetchall()
        conn.close()
        return rows

    def delete_user_warning(self, guild_id, user_id, warning_id):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute(
            "DELETE FROM user_warnings WHERE id = ? AND guild_id = ? AND user_id = ?",
            (warning_id, guild_id, user_id)
        )
        deleted = cursor.rowcount > 0
        conn.commit()
        conn.close()
        return deleted

    def get_active_sanctions(self, guild_id, user_id):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT action, moderator_id, reason, expires_at FROM moderation_actions WHERE guild_id = ? AND user_id = ? AND active = 1 ORDER BY id DESC",
            (guild_id, user_id)
        )
        rows = cursor.fetchall()
        conn.close()
        return rows

    def is_first_join(self, guild_id, user_id):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT 1 FROM member_first_join WHERE guild_id = ? AND user_id = ?", (guild_id, user_id))
        already_joined = cursor.fetchone() is not None
        if not already_joined:
            cursor.execute(
                "INSERT INTO member_first_join (guild_id, user_id, first_joined_at) VALUES (?, ?, ?)",
                (guild_id, user_id, datetime.datetime.now(datetime.timezone.utc).isoformat())
            )
            conn.commit()
        conn.close()
        return not already_joined

    async def execute_clean(self, channel, amount: int):
        if amount <= 0:
            return "Error: La cantidad de mensajes debe ser mayor a cero."
        deleted = await channel.purge(limit=amount)
        alert_message = await channel.send(f"Borrando {len(deleted)} mensajes...")
        await asyncio.sleep(2)
        await alert_message.delete()
        return None

    async def build_info_embed(self, guild: discord.Guild, member: discord.Member) -> discord.Embed:
        embed = discord.Embed(title=f"Información de {member}", color=discord.Color.yellow())
        embed.set_thumbnail(url=member.display_avatar.url)

        try:
            fetched_user = await self.bot.fetch_user(member.id)
            if fetched_user.banner:
                embed.set_image(url=fetched_user.banner.url)
        except (discord.NotFound, discord.HTTPException):
            pass

        embed.add_field(name="ID", value=str(member.id), inline=True)
        embed.add_field(name="Mención", value=member.mention, inline=True)
        embed.add_field(name="¿Es bot?", value="Sí" if member.bot else "No", inline=True)

        embed.add_field(name="Nombre de usuario", value=str(member), inline=True)
        global_name = getattr(member, "global_name", None)
        if global_name:
            embed.add_field(name="Nombre global", value=global_name, inline=True)
        if member.nick:
            embed.add_field(name="Apodo en el servidor", value=member.nick, inline=True)

        created_r = discord.utils.format_dt(member.created_at, style="R")
        created_f = discord.utils.format_dt(member.created_at, style="F")
        embed.add_field(name="Cuenta creada", value=f"{created_f} ({created_r})", inline=False)

        if member.joined_at:
            joined_r = discord.utils.format_dt(member.joined_at, style="R")
            joined_f = discord.utils.format_dt(member.joined_at, style="F")
            embed.add_field(name="Se unió al servidor", value=f"{joined_f} ({joined_r})", inline=False)

        if member.premium_since:
            embed.add_field(
                name="Impulsando el servidor desde",
                value=discord.utils.format_dt(member.premium_since, style="F"),
                inline=False,
            )

        status_map = {
            discord.Status.online: "🟢 En línea",
            discord.Status.idle: "🌙 Ausente",
            discord.Status.dnd: "⛔ No molestar",
            discord.Status.offline: "⚫ Desconectado/Invisible",
        }
        embed.add_field(name="Estado", value=status_map.get(member.status, "Desconocido"), inline=True)

        activity = getattr(member, "activity", None)
        if activity is not None:
            embed.add_field(name="Actividad", value=getattr(activity, "name", str(activity)), inline=True)

        roles = [role.mention for role in reversed(member.roles) if role.name != "@everyone"]
        embed.add_field(
            name=f"Roles ({len(roles)})",
            value=", ".join(roles)[:1024] if roles else "Ninguno",
            inline=False,
        )

        timeout_until = getattr(member, "timed_out_until", None) or getattr(member, "communication_disabled_until", None)
        if timeout_until and timeout_until > datetime.datetime.now(datetime.timezone.utc):
            embed.add_field(
                name="Silenciado (timeout) hasta",
                value=discord.utils.format_dt(timeout_until, style="F"),
                inline=False,
            )

        if getattr(member, "pending", False):
            embed.add_field(name="Verificación pendiente", value="Sí", inline=True)

        embed.add_field(
            name="Administrador",
            value="Sí" if member.guild_permissions.administrator else "No",
            inline=True,
        )

        warnings = self.get_user_warnings(guild.id, member.id)
        embed.add_field(name="Advertencias registradas", value=str(len(warnings)), inline=True)

        active_sanctions = self.get_active_sanctions(guild.id, member.id)
        if active_sanctions:
            lines = []
            action_labels = {"ban": "Baneo", "mute": "Mute", "kick": "Expulsión"}
            for action, moderator_id, reason, expires_at in active_sanctions:
                mod = self.bot.get_user(moderator_id) or moderator_id
                if expires_at:
                    expiry_dt = datetime.datetime.fromisoformat(expires_at)
                    expiry = f" (expira {discord.utils.format_dt(expiry_dt, style='R')})"
                else:
                    expiry = " (permanente)"
                label = action_labels.get(action, action)
                lines.append(f"• {label} por {mod} — {reason}{expiry}")
            embed.add_field(name="Sanciones activas", value="\n".join(lines)[:1024], inline=False)
        else:
            embed.add_field(name="Sanciones activas", value="Ninguna", inline=False)

        embed.timestamp = datetime.datetime.now(datetime.timezone.utc)
        return embed

    # --- SLASH COMMANDS (/) ---

    @app_commands.command(name="kick", description="Expulsa a un miembro del servidor.")
    @app_commands.rename(user_input="miembro", reason="motivo")
    @discord.app_commands.checks.has_permissions(kick_members=True)
    async def kick_slash(self, interaction: discord.Interaction, user_input: str, reason: str = "No especificada"):
        member = self.get_member_from_input(interaction.guild, user_input)
        if not isinstance(member, discord.Member):
            await interaction.response.send_message(embed=discord.Embed(title=f"{error_emoji(self.bot)} Error", description="Debe mencionar a un miembro válido del servidor.", color=discord.Color.red()))
            return

        await self.notify_sanction(member, "Expulsión", interaction.user, reason, "No aplica")
        await member.kick(reason=reason)
        self.store_action(interaction.guild.id, member.id, "kick", interaction.user.id, reason)
        await self.send_log(interaction.guild, "Expulsión", member, interaction.user, reason)
        await interaction.response.send_message(embed=discord.Embed(title=f"{success_emoji(self.bot)} Acción ejecutada", description=f"El usuario {member} fue expulsado.\nMotivo: {reason}", color=discord.Color.green()))

    @app_commands.command(name="ban", description="Banea a un miembro del servidor. Usa 'p' en tiempo para un baneo permanente.")
    @app_commands.rename(user_input="miembro", duration_input="tiempo", reason="motivo")
    @discord.app_commands.checks.has_permissions(ban_members=True)
    async def ban_slash(self, interaction: discord.Interaction, user_input: str, duration_input: str, reason: str = "No especificada"):
        member = self.get_member_from_input(interaction.guild, user_input)
        if not isinstance(member, discord.Member):
            await interaction.response.send_message(embed=discord.Embed(title=f"{error_emoji(self.bot)} Error", description="Debe mencionar a un miembro válido del servidor.", color=discord.Color.red()))
            return

        try:
            duration_seconds, duration_text = self.parse_duration(duration_input, allow_permanent=True)
        except ValueError as error:
            await interaction.response.send_message(embed=discord.Embed(title=f"{error_emoji(self.bot)} Error", description=str(error), color=discord.Color.red()))
            return

        await self.notify_sanction(member, "Baneo", interaction.user, reason, duration_text)
        await interaction.guild.ban(member, reason=reason)
        expires_at = None
        if duration_seconds is not None:
            expires_at = (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=duration_seconds)).isoformat()
        self.store_action(interaction.guild.id, member.id, "ban", interaction.user.id, reason, duration_seconds, expires_at)
        action_label = "Baneo permanente" if duration_seconds is None else "Baneo temporal"
        await self.send_log(interaction.guild, action_label, member, interaction.user, reason, duration_text)
        description = (
            f"El usuario {member} fue baneado permanentemente.\nMotivo: {reason}"
            if duration_seconds is None
            else f"El usuario {member} fue baneado por {duration_text}.\nMotivo: {reason}"
        )
        embed = discord.Embed(title=f"{ban_emoji(self.bot)} Acción ejecutada", description=description, color=discord.Color.green())
        await interaction.response.send_message(embed=embed)
        if duration_seconds is not None:
            asyncio.create_task(self.schedule_action_expiration(interaction.guild, member.id, "ban", duration_seconds))

    @app_commands.command(name="unban", description="Remueve el baneo de un usuario mediante su ID.")
    @app_commands.rename(user_id="id_usuario", reason="motivo")
    @discord.app_commands.checks.has_permissions(ban_members=True)
    async def unban_slash(self, interaction: discord.Interaction, user_id: str, reason: str = "No especificada"):
        target_user = await self.bot.fetch_user(int(user_id))
        await interaction.guild.unban(target_user, reason=reason)
        self.deactivate_action(interaction.guild.id, target_user.id, "ban")
        await self.send_log(interaction.guild, "Desbaneo", target_user, interaction.user, reason)
        await interaction.response.send_message(embed=discord.Embed(title=f"{success_emoji(self.bot)} Acción ejecutada", description=f"El baneo aplicado a la ID {user_id} fue revocado.\nMotivo: {reason}", color=discord.Color.green()))

    @app_commands.command(name="mute", description="Silencia a un miembro (Timeout).")
    @app_commands.rename(user_input="miembro", duration_input="tiempo", reason="motivo")
    @discord.app_commands.checks.has_permissions(moderate_members=True)
    async def mute_slash(self, interaction: discord.Interaction, user_input: str, duration_input: str, reason: str = "No especificada"):
        member = self.get_member_from_input(interaction.guild, user_input)
        if not isinstance(member, discord.Member):
            await interaction.response.send_message(embed=discord.Embed(title=f"{error_emoji(self.bot)} Error", description="Debe mencionar a un miembro válido del servidor.", color=discord.Color.red()))
            return

        try:
            duration_seconds, duration_text = self.parse_duration(duration_input)
        except ValueError as error:
            await interaction.response.send_message(embed=discord.Embed(title=f"{error_emoji(self.bot)} Error", description=str(error), color=discord.Color.red()))
            return

        if duration_seconds > 28 * 24 * 60 * 60:
            await interaction.response.send_message(embed=discord.Embed(title=f"{error_emoji(self.bot)} Error", description="El timeout máximo permitido por Discord es de 28 días.", color=discord.Color.red()))
            return

        timeout_duration = datetime.timedelta(seconds=duration_seconds)
        await member.timeout(timeout_duration, reason=reason)
        expires_at = (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=duration_seconds)).isoformat()
        self.store_action(interaction.guild.id, member.id, "mute", interaction.user.id, reason, duration_seconds, expires_at)
        await self.send_log(interaction.guild, "Aislamiento (mute)", member, interaction.user, reason, f"{duration_text}")
        await self.notify_sanction(member, "Mute", interaction.user, reason, duration_text)
        embed = discord.Embed(title=f"{mute_emoji(self.bot)} Acción ejecutada", description=f"El usuario {member} fue silenciado por {duration_text}.\nMotivo: {reason}", color=discord.Color.green())
        await interaction.response.send_message(embed=embed)
        asyncio.create_task(self.schedule_action_expiration(interaction.guild, member.id, "mute", duration_seconds))

    @app_commands.command(name="unmute", description="Quita el silencio a un miembro del servidor.")
    @app_commands.rename(member="miembro", reason="motivo")
    @discord.app_commands.checks.has_permissions(moderate_members=True)
    async def unmute_slash(self, interaction: discord.Interaction, member: discord.Member, reason: str = "No especificada"):
        await member.timeout(None, reason=reason)
        self.deactivate_action(interaction.guild.id, member.id, "mute")
        await self.send_log(interaction.guild, "Fin de aislamiento (unmute)", member, interaction.user, reason)
        await interaction.response.send_message(embed=discord.Embed(title=f"{success_emoji(self.bot)} Acción ejecutada", description=f"Se ha revocado la restricción de silencio para {member}.\nMotivo: {reason}", color=discord.Color.green()))

    @app_commands.command(name="warn", description="Agrega advertencias a un usuario y muestra su total.")
    @app_commands.rename(user_input="usuario", warning_count="cantidad", reason="motivo")
    @discord.app_commands.checks.has_permissions(moderate_members=True)
    async def warn_slash(self, interaction: discord.Interaction, user_input: discord.Member, warning_count: int, reason: str = "No especificada"):
        if warning_count <= 0:
            await interaction.response.send_message(embed=discord.Embed(title=f"{error_emoji(self.bot)} Error", description="La cantidad debe ser mayor a cero.", color=discord.Color.red()))
            return
        self.warn_user(interaction.guild.id, user_input.id, interaction.user.id, reason, warning_count)
        warnings = self.get_user_warnings(interaction.guild.id, user_input.id)
        total = len(warnings)
        await self.send_log(interaction.guild, "Advertencia", user_input, interaction.user, reason, extra=f"Total actual: {total}")
        embed = discord.Embed(title=f"{success_emoji(self.bot)} Advertencia registrada", description=f"Se añadieron {warning_count} advertencia(s) a {user_input.mention}.\nMotivo: {reason}", color=discord.Color.orange())
        embed.add_field(name="Total actual", value=str(total), inline=False)
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="unwarn", description="Quita una advertencia específica de un usuario.")
    @app_commands.rename(user_input="usuario", warning_number="numero", reason="razon")
    @discord.app_commands.checks.has_permissions(moderate_members=True)
    async def unwarn_slash(self, interaction: discord.Interaction, user_input: discord.Member, warning_number: int, reason: str = "No especificada"):
        warnings = self.get_user_warnings(interaction.guild.id, user_input.id)
        if not warnings:
            await interaction.response.send_message(
                embed=discord.Embed(title=f"{error_emoji(self.bot)} Error", description=f"{user_input.mention} no tiene advertencias registradas.", color=discord.Color.red())
            )
            return
        if warning_number <= 0 or warning_number > len(warnings):
            await interaction.response.send_message(
                embed=discord.Embed(title=f"{error_emoji(self.bot)} Error", description=f"Número de advertencia inválido. {user_input.mention} tiene {len(warnings)} advertencia(s) (usa `/warns` para verlas numeradas).", color=discord.Color.red())
            )
            return

        warning_id, _, removed_reason, _ = warnings[warning_number - 1]
        self.delete_user_warning(interaction.guild.id, user_input.id, warning_id)
        remaining = len(warnings) - 1
        await self.send_log(interaction.guild, "Advertencia revocada", user_input, interaction.user, reason, extra=f"Advertencia #{warning_number} eliminada (motivo original: {removed_reason}). Total restante: {remaining}")
        embed = discord.Embed(
            title=f"{success_emoji(self.bot)} Advertencia eliminada",
            description=f"Se eliminó la advertencia #{warning_number} de {user_input.mention}.\nMotivo original: {removed_reason}\nMotivo de la revocación: {reason}",
            color=discord.Color.orange(),
        )
        embed.add_field(name="Total restante", value=str(remaining), inline=False)
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="warns", description="Muestra las advertencias de un usuario.")
    @app_commands.rename(user_input="usuario")
    async def warns_slash(self, interaction: discord.Interaction, user_input: discord.Member):
        warnings = self.get_user_warnings(interaction.guild.id, user_input.id)
        embed = discord.Embed(title=f"⚠️ Warns de {user_input.display_name}", color=discord.Color.orange())
        embed.add_field(name="Total", value=str(len(warnings)), inline=False)
        if warnings:
            history = "\n".join(f"• {idx + 1}. {row[2]} (por {self.bot.get_user(row[1]) or row[1]} el {row[3]})" for idx, row in enumerate(warnings))
            embed.add_field(name="Historial", value=history[:1024], inline=False)
        else:
            embed.add_field(name="Historial", value="Sin advertencias registradas.", inline=False)
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="say", description="Replica un texto a través del bot.")
    @app_commands.rename(text_content="mensaje")
    async def say_slash(self, interaction: discord.Interaction, text_content: str, embed: bool = False):
        if not self.has_say_role(interaction.user):
            await interaction.response.send_message(embed=discord.Embed(title=f"{error_emoji(self.bot)} Error", description="No cuenta con los roles requeridos para utilizar este comando.", color=discord.Color.red()), ephemeral=True)
            return
        if embed:
            sent_embed = discord.Embed(description=text_content, color=discord.Color.yellow())
            sent_embed.set_author(name=interaction.user.display_name, icon_url=interaction.user.display_avatar.url)
            await interaction.response.send_message(embed=sent_embed)
            return
        await interaction.response.send_message(text_content)

    @app_commands.command(name="info", description="Muestra toda la información disponible de un usuario.")
    @app_commands.rename(user_input="usuario")
    async def info_slash(self, interaction: discord.Interaction, user_input: discord.Member = None):
        if not self.has_info_role(interaction.user):
            await interaction.response.send_message(
                embed=discord.Embed(title=f"{error_emoji(self.bot)} Error", description="No cuentas con los roles requeridos para utilizar este comando.", color=discord.Color.red()),
                ephemeral=True,
            )
            return
        member = user_input or interaction.user
        embed = await self.build_info_embed(interaction.guild, member)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="clean", description="Elimina una cantidad específica de mensajes.")
    @app_commands.rename(amount="mensajes")
    @discord.app_commands.checks.has_permissions(manage_messages=True)
    async def clean_slash(self, interaction: discord.Interaction, amount: int):
        await interaction.response.defer(ephemeral=True)
        execution_result = await self.execute_clean(interaction.channel, amount)
        if execution_result:
            await interaction.followup.send(execution_result)
        else:
            await interaction.followup.send("Limpieza completada correctamente.")

    # --- PREFIX COMMANDS (&) ---

    @commands.command(name="kick")
    @commands.has_permissions(kick_members=True)
    async def kick_prefix(self, ctx, user_input: str, *, reason: str = "No especificada"):
        member = self.get_member_from_input(ctx.guild, user_input)
        if not isinstance(member, discord.Member):
            await ctx.send(embed=discord.Embed(title=f"{error_emoji(self.bot)} Error", description="Debe mencionar a un miembro válido del servidor.", color=discord.Color.red()))
            return
        await self.notify_sanction(member, "Expulsión", ctx.author, reason, "No aplica")
        await member.kick(reason=reason)
        self.store_action(ctx.guild.id, member.id, "kick", ctx.author.id, reason)
        await self.send_log(ctx.guild, "Expulsión", member, ctx.author, reason)
        await ctx.send(embed=discord.Embed(title=f"{success_emoji(self.bot)} Acción ejecutada", description=f"El usuario {member} fue expulsado.\nMotivo: {reason}", color=discord.Color.green()))

    @commands.command(name="ban")
    @commands.has_permissions(ban_members=True)
    async def ban_prefix(self, ctx, user_input: str, duration_input: str, *, reason: str = "No especificada"):
        member = self.get_member_from_input(ctx.guild, user_input)
        if not isinstance(member, discord.Member):
            await ctx.send(embed=discord.Embed(title=f"{error_emoji(self.bot)} Error", description="Debe mencionar a un miembro válido del servidor.", color=discord.Color.red()))
            return

        try:
            duration_seconds, duration_text = self.parse_duration(duration_input, allow_permanent=True)
        except ValueError as error:
            await ctx.send(embed=discord.Embed(title=f"{error_emoji(self.bot)} Error", description=str(error), color=discord.Color.red()))
            return

        await self.notify_sanction(member, "Baneo", ctx.author, reason, duration_text)
        await ctx.guild.ban(member, reason=reason)
        expires_at = None
        if duration_seconds is not None:
            expires_at = (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=duration_seconds)).isoformat()
        self.store_action(ctx.guild.id, member.id, "ban", ctx.author.id, reason, duration_seconds, expires_at)
        action_label = "Baneo permanente" if duration_seconds is None else "Baneo temporal"
        await self.send_log(ctx.guild, action_label, member, ctx.author, reason, duration_text)
        description = (
            f"El usuario {member} fue baneado permanentemente.\nMotivo: {reason}"
            if duration_seconds is None
            else f"El usuario {member} fue baneado por {duration_text}.\nMotivo: {reason}"
        )
        await ctx.send(embed=discord.Embed(title=f"{ban_emoji(self.bot)} Acción ejecutada", description=description, color=discord.Color.green()))
        if duration_seconds is not None:
            asyncio.create_task(self.schedule_action_expiration(ctx.guild, member.id, "ban", duration_seconds))

    @commands.command(name="unban")
    @commands.has_permissions(ban_members=True)
    async def unban_prefix(self, ctx, user_id: str, *, reason: str = "No especificada"):
        target_user = await self.bot.fetch_user(int(user_id))
        await ctx.guild.unban(target_user, reason=reason)
        self.deactivate_action(ctx.guild.id, target_user.id, "ban")
        await self.send_log(ctx.guild, "Desbaneo", target_user, ctx.author, reason)
        await ctx.send(embed=discord.Embed(title=f"{success_emoji(self.bot)} Acción ejecutada", description=f"El baneo aplicado a la ID {user_id} fue revocado.\nMotivo: {reason}", color=discord.Color.green()))

    @commands.command(name="mute")
    @commands.has_permissions(moderate_members=True)
    async def mute_prefix(self, ctx, user_input: str, duration_input: str, *, reason: str = "No especificada"):
        member = self.get_member_from_input(ctx.guild, user_input)
        if not isinstance(member, discord.Member):
            await ctx.send(embed=discord.Embed(title=f"{error_emoji(self.bot)} Error", description="Debe mencionar a un miembro válido del servidor.", color=discord.Color.red()))
            return

        try:
            duration_seconds, duration_text = self.parse_duration(duration_input)
        except ValueError as error:
            await ctx.send(embed=discord.Embed(title=f"{error_emoji(self.bot)} Error", description=str(error), color=discord.Color.red()))
            return

        if duration_seconds > 28 * 24 * 60 * 60:
            await ctx.send(embed=discord.Embed(title=f"{error_emoji(self.bot)} Error", description="El timeout máximo permitido por Discord es de 28 días.", color=discord.Color.red()))
            return

        timeout_duration = datetime.timedelta(seconds=duration_seconds)
        await member.timeout(timeout_duration, reason=reason)
        expires_at = (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=duration_seconds)).isoformat()
        self.store_action(ctx.guild.id, member.id, "mute", ctx.author.id, reason, duration_seconds, expires_at)
        await self.send_log(ctx.guild, "Aislamiento (mute)", member, ctx.author, reason, f"{duration_text}")
        await self.notify_sanction(member, "Mute", ctx.author, reason, duration_text)
        await ctx.send(embed=discord.Embed(title=f"{mute_emoji(self.bot)} Acción ejecutada", description=f"El usuario {member} fue silenciado por {duration_text}.\nMotivo: {reason}", color=discord.Color.green()))
        asyncio.create_task(self.schedule_action_expiration(ctx.guild, member.id, "mute", duration_seconds))

    @commands.command(name="unmute")
    @commands.has_permissions(moderate_members=True)
    async def unmute_prefix(self, ctx, member: discord.Member, *, reason: str = "No especificada"):
        await member.timeout(None, reason=reason)
        self.deactivate_action(ctx.guild.id, member.id, "mute")
        await self.send_log(ctx.guild, "Fin de aislamiento (unmute)", member, ctx.author, reason)
        await ctx.send(embed=discord.Embed(title=f"{success_emoji(self.bot)} Acción ejecutada", description=f"Se ha revocado la restricción de silencio para {member}.\nMotivo: {reason}", color=discord.Color.green()))

    @commands.command(name="warn")
    @commands.has_permissions(moderate_members=True)
    async def warn_prefix(self, ctx, user_input: str, warning_count: int, *, reason: str = "No especificada"):
        member = self.get_member_from_input(ctx.guild, user_input)
        if not isinstance(member, discord.Member):
            await ctx.send(embed=discord.Embed(title=f"{error_emoji(self.bot)} Error", description="Debe mencionar a un miembro válido del servidor.", color=discord.Color.red()))
            return
        if warning_count <= 0:
            await ctx.send(embed=discord.Embed(title=f"{error_emoji(self.bot)} Error", description="La cantidad debe ser mayor a cero.", color=discord.Color.red()))
            return
        self.warn_user(ctx.guild.id, member.id, ctx.author.id, reason, warning_count)
        warnings = self.get_user_warnings(ctx.guild.id, member.id)
        embed = discord.Embed(title=f"{success_emoji(self.bot)} Advertencia registrada", description=f"Se añadieron {warning_count} advertencia(s) a {member.mention}.\nMotivo: {reason}", color=discord.Color.orange())
        embed.add_field(name="Total actual", value=str(len(warnings)), inline=False)
        await ctx.send(embed=embed)

    @commands.command(name="unwarn")
    @commands.has_permissions(moderate_members=True)
    async def unwarn_prefix(self, ctx, user_input: str, warning_number: int, *, reason: str = "No especificada"):
        member = self.get_member_from_input(ctx.guild, user_input)
        if not isinstance(member, discord.Member):
            await ctx.send(embed=discord.Embed(title=f"{error_emoji(self.bot)} Error", description="Debe mencionar a un miembro válido del servidor.", color=discord.Color.red()))
            return

        warnings = self.get_user_warnings(ctx.guild.id, member.id)
        if not warnings:
            await ctx.send(embed=discord.Embed(title=f"{error_emoji(self.bot)} Error", description=f"{member.mention} no tiene advertencias registradas.", color=discord.Color.red()))
            return
        if warning_number <= 0 or warning_number > len(warnings):
            await ctx.send(embed=discord.Embed(title=f"{error_emoji(self.bot)} Error", description=f"Número de advertencia inválido. {member.mention} tiene {len(warnings)} advertencia(s) (usa `&warns` para verlas numeradas).", color=discord.Color.red()))
            return

        warning_id, _, removed_reason, _ = warnings[warning_number - 1]
        self.delete_user_warning(ctx.guild.id, member.id, warning_id)
        remaining = len(warnings) - 1
        await self.send_log(ctx.guild, "Advertencia revocada", member, ctx.author, reason, extra=f"Advertencia #{warning_number} eliminada (motivo original: {removed_reason}). Total restante: {remaining}")
        embed = discord.Embed(
            title=f"{success_emoji(self.bot)} Advertencia eliminada",
            description=f"Se eliminó la advertencia #{warning_number} de {member.mention}.\nMotivo original: {removed_reason}\nMotivo de la revocación: {reason}",
            color=discord.Color.orange(),
        )
        embed.add_field(name="Total restante", value=str(remaining), inline=False)
        await ctx.send(embed=embed)

    @commands.command(name="warns")
    async def warns_prefix(self, ctx, user_input: str = None):
        target = self.get_member_from_input(ctx.guild, user_input) if user_input else ctx.author
        if not isinstance(target, discord.Member):
            await ctx.send(embed=discord.Embed(title=f"{error_emoji(self.bot)} Error", description="Debe mencionar un miembro válido del servidor.", color=discord.Color.red()))
            return
        warnings = self.get_user_warnings(ctx.guild.id, target.id)
        embed = discord.Embed(title=f"⚠️ Warns de {target.display_name}", color=discord.Color.orange())
        embed.add_field(name="Total", value=str(len(warnings)), inline=False)
        if warnings:
            history = "\n".join(f"• {idx + 1}. {row[2]} (por {self.bot.get_user(row[1]) or row[1]} el {row[3]})" for idx, row in enumerate(warnings))
            embed.add_field(name="Historial", value=history[:1024], inline=False)
        else:
            embed.add_field(name="Historial", value="Sin advertencias registradas.", inline=False)
        await ctx.send(embed=embed)

    @commands.command(name="info")
    async def info_prefix(self, ctx, user_input: str = None):
        if not self.has_info_role(ctx.author):
            await ctx.send(embed=discord.Embed(title=f"{error_emoji(self.bot)} Error", description="No cuentas con los roles requeridos para utilizar este comando.", color=discord.Color.red()))
            return

        member = self.get_member_from_input(ctx.guild, user_input) if user_input else ctx.author
        if not isinstance(member, discord.Member):
            await ctx.send(embed=discord.Embed(title=f"{error_emoji(self.bot)} Error", description="Debe mencionar a un miembro válido del servidor.", color=discord.Color.red()))
            return

        embed = await self.build_info_embed(ctx.guild, member)
        try:
            dm_channel = ctx.author.dm_channel or await ctx.author.create_dm()
            await dm_channel.send(embed=embed)
            try:
                await ctx.message.add_reaction(success_emoji(self.bot))
            except discord.Forbidden:
                pass
        except (discord.Forbidden, discord.HTTPException):
            await ctx.send(embed=discord.Embed(
                title=f"{error_emoji(self.bot)} Error",
                description="No pude enviarte la información por MD. Verifica que tengas los mensajes directos habilitados para este servidor.",
                color=discord.Color.red(),
            ))

    @commands.command(name="say")
    async def say_prefix(self, ctx, *, text_content: str):
        if not self.has_say_role(ctx.author):
            await ctx.send(embed=discord.Embed(title=f"{error_emoji(self.bot)} Error", description="No cuenta con los roles requeridos para utilizar este comando.", color=discord.Color.red()))
            return

        embed_requested = text_content.lower().endswith(" embed_true")
        if embed_requested:
            text_content = text_content[:-11].rstrip()

        try:
            await ctx.message.delete()
        except discord.Forbidden:
            print("Failed to delete message: Missing Manage Messages permission.")

        if embed_requested:
            sent_embed = discord.Embed(description=text_content, color=discord.Color.yellow())
            sent_embed.set_author(name=ctx.author.display_name, icon_url=ctx.author.display_avatar.url)
            await ctx.send(embed=sent_embed)
            return
        await ctx.send(text_content)

    @commands.command(name="clean")
    @commands.has_permissions(manage_messages=True)
    async def clean_prefix(self, ctx, amount: int):
        try:
            await ctx.message.delete()
        except discord.Forbidden:
            print("Failed to delete command execution message.")
        await self.execute_clean(ctx.channel, amount)

    @commands.command(name="ping")
    async def ping_prefix(self, ctx):
        if hasattr(self.bot, 'start_time'):
            uptime_seconds = int(time.time() - self.bot.start_time)
            uptime_string = str(datetime.timedelta(seconds=uptime_seconds))
        else:
            uptime_string = "Desconocido"

        current_process = psutil.Process(os.getpid())
        memory_usage_mb = current_process.memory_info().rss / 1024 / 1024
        api_latency_ms = round(self.bot.latency * 1000)

        embed = discord.Embed(title="Diagnóstico de Estado del Sistema", color=discord.Color.blue())
        embed.add_field(name="Versión", value="1.0, La Actualización Ilegal", inline=True)
        embed.add_field(name="Latencia de la API", value=f"{api_latency_ms} ms", inline=True)
        embed.add_field(name="Tiempo de actividad", value=uptime_string, inline=True)
        embed.add_field(name="Uso de memoria", value=f"{memory_usage_mb:.2f} MB", inline=True)
        embed.add_field(name="Servidores conectados", value=str(len(self.bot.guilds)), inline=True)
        embed.timestamp = datetime.datetime.now(datetime.timezone.utc)
        await ctx.send(embed=embed)

    async def send_welcome_dm(self, member):
        try:
            embed = discord.Embed(
                title=f"👋 ¡Bienvenido a {member.guild.name}!",
                description=f"Nos alegra tenerte por aquí, {member.mention}. ¡Esperamos que disfrutes tu estadía!",
                color=discord.Color.yellow(),
            )
            if member.guild.icon:
                embed.set_thumbnail(url=member.guild.icon.url)
            embed.timestamp = datetime.datetime.now(datetime.timezone.utc)
            dm_channel = member.dm_channel or await member.create_dm()
            await dm_channel.send(embed=embed)
        except (discord.Forbidden, discord.HTTPException) as error:
            print(f"No se pudo enviar el MD de bienvenida a {member}: {error}")

    async def send_farewell_dm(self, member):
        try:
            embed = discord.Embed(
                title=f"👋 Has salido de {member.guild.name}",
                description="Gracias por haber sido parte de la comunidad. ¡Esperamos verte de nuevo pronto!",
                color=discord.Color.yellow(),
            )
            embed.timestamp = datetime.datetime.now(datetime.timezone.utc)
            dm_channel = member.dm_channel or await member.create_dm()
            await dm_channel.send(embed=embed)
        except (discord.Forbidden, discord.HTTPException) as error:
            # Si el servidor que abandonó era el único en común con el bot,
            # Discord ya no permite enviarle mensajes directos; no hay forma
            # de evitar esto desde la API.
            print(f"No se pudo enviar el MD de despedida a {member}: {error}")

    @commands.Cog.listener()
    async def on_member_join(self, member):
        if self.is_first_join(member.guild.id, member.id):
            await self.send_welcome_dm(member)

        channel = await self.get_log_channel(member.guild)
        if not channel:
            return
        embed = discord.Embed(title="👋 Nuevo miembro", description=f"Bienvenido {member.mention} a {member.guild.name}", color=discord.Color.green())
        embed.add_field(name="Usuario", value=str(member), inline=False)
        embed.add_field(name="ID", value=str(member.id), inline=False)
        embed.set_thumbnail(url=member.display_avatar.url)
        await channel.send(embed=embed)

    @commands.Cog.listener()
    async def on_member_remove(self, member):
        await self.send_farewell_dm(member)

        channel = await self.get_log_channel(member.guild)
        if not channel:
            return
        embed = discord.Embed(title="👋 Salida de usuario", description=f"{member.mention} abandonó {member.guild.name}", color=discord.Color.red())
        embed.add_field(name="Usuario", value=str(member), inline=False)
        embed.add_field(name="ID", value=str(member.id), inline=False)
        embed.set_thumbnail(url=member.display_avatar.url)
        await channel.send(embed=embed)


async def setup(bot):
    await bot.add_cog(Moderation(bot))