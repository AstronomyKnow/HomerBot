import discord
from discord import app_commands
from discord.ext import commands
import asyncio
import datetime
import time
import os
import psutil

class Moderation(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.log_channel_id = 1393450057189167234
        self.allowed_say_roles = [1372448974211911770, 1359359923770757150, 1361138268829253875]

    # --- INTERNAL UTILITY METHODS ---
    def has_say_role(self, user: discord.Member) -> bool:
        return any(role.id in self.allowed_say_roles for role in user.roles)

    async def send_log(self, guild, action, target, moderator, reason, duration=None):
        try:
            channel = guild.get_channel(self.log_channel_id)
            if not channel: 
                print(f"Log channel with ID {self.log_channel_id} not found.")
                return
            
            embed = discord.Embed(title=f"Registro de Moderacion: {action}", color=discord.Color.dark_red())
            embed.add_field(name="Usuario afectado", value=str(target), inline=False)
            embed.add_field(name="Moderador responsable", value=str(moderator), inline=False)
            if duration:
                embed.add_field(name="Duracion asignada", value=str(duration), inline=False)
            embed.add_field(name="Motivo", value=reason, inline=False)
            embed.timestamp = datetime.datetime.now(datetime.timezone.utc)
            await channel.send(embed=embed)
        except Exception as e:
            print(f"Failed to send moderation log to Discord: {e}")

    def parse_duration(self, duration_input):
        if duration_input is None:
            raise ValueError("La duración no puede estar vacía.")

        text = str(duration_input).strip().lower()
        if not text:
            raise ValueError("La duración no puede estar vacía.")

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

    async def execute_clean(self, channel, amount: int):
        if amount <= 0:
            return "Error: La cantidad de mensajes debe ser mayor a cero."
        deleted = await channel.purge(limit=amount)
        alert_message = await channel.send(f"Borrando {len(deleted)} mensajes...")
        await asyncio.sleep(2)
        await alert_message.delete()
        return None

    # --- SLASH COMMANDS (/) ---

    @app_commands.command(name="kick", description="Expulsa a un miembro del servidor.")
    @app_commands.rename(user_input="miembro", reason="motivo")
    @discord.app_commands.checks.has_permissions(kick_members=True)
    async def kick_slash(self, interaction: discord.Interaction, user_input: str, reason: str = "No especificada"):
        clean_id = user_input.replace("<@", "").replace(">", "").replace("!", "")
        member = interaction.guild.get_member(int(clean_id)) if user_input != "@everyone" else "@everyone"
        
        if member == "@everyone" or not isinstance(member, discord.Member):
            await interaction.response.send_message("Error: Debe mencionar a un miembro valido del servidor.")
            return
        
        await member.kick(reason=reason)
        await self.send_log(interaction.guild, "Expulsion", member, interaction.user, reason)
        await interaction.response.send_message(f"El usuario {member} ha sido expulsado correctamente.")

    @app_commands.command(name="ban", description="Banea a un miembro del servidor.")
    @app_commands.rename(user_input="miembro", duration_input="tiempo", reason="motivo")
    @discord.app_commands.checks.has_permissions(ban_members=True)
    async def ban_slash(self, interaction: discord.Interaction, user_input: str, duration_input: str, reason: str = "No especificada"):
        clean_id = user_input.replace("<@", "").replace(">", "").replace("!", "")
        member = interaction.guild.get_member(int(clean_id)) if user_input != "@everyone" else "@everyone"
        
        if member == "@everyone" or not isinstance(member, discord.Member):
            await interaction.response.send_message("Error: Debe mencionar a un miembro valido del servidor.")
            return

        try:
            duration_seconds, duration_text = self.parse_duration(duration_input)
        except ValueError as error:
            await interaction.response.send_message(f"Error: {error}")
            return

        await interaction.guild.ban(member, reason=reason)
        await self.send_log(interaction.guild, "Baneo Temporal", member, interaction.user, reason, f"{duration_text}")
        await interaction.response.send_message(f"El usuario {member} ha sido baneado por {duration_text}.")
        
        async def automatic_unban():
            await asyncio.sleep(duration_seconds)
            try:
                await interaction.guild.unban(member, reason="Expiracion del periodo de baneo establecido.")
                await self.send_log(interaction.guild, "Desbaneo Automatico", member, self.bot.user, "Periodo de sancion concluido.")
            except (discord.NotFound, discord.Forbidden):
                print(f"Automatic unban failed or user already unbanned: {member.id}")
        asyncio.create_task(automatic_unban())

    @app_commands.command(name="unban", description="Remueve el baneo de un usuario mediante su ID.")
    @app_commands.rename(user_id="id_usuario", reason="motivo")
    @discord.app_commands.checks.has_permissions(ban_members=True)
    async def unban_slash(self, interaction: discord.Interaction, user_id: str, reason: str = "No especificada"):
        target_user = await self.bot.fetch_user(int(user_id))
        await interaction.guild.unban(target_user, reason=reason)
        await self.send_log(interaction.guild, "Desbaneo", target_user, interaction.user, reason)
        await interaction.response.send_message(f"El baneo aplicado a la ID {user_id} ha sido revocado.")

    @app_commands.command(name="mute", description="Silencia a un miembro (Timeout).")
    @app_commands.rename(user_input="miembro", duration_input="tiempo", reason="motivo")
    @discord.app_commands.checks.has_permissions(moderate_members=True)
    async def mute_slash(self, interaction: discord.Interaction, user_input: str, duration_input: str, reason: str = "No especificada"):
        clean_id = user_input.replace("<@", "").replace(">", "").replace("!", "")
        member = interaction.guild.get_member(int(clean_id)) if user_input != "@everyone" else "@everyone"
        
        if member == "@everyone" or not isinstance(member, discord.Member):
            await interaction.response.send_message("Error: Debe mencionar a un miembro valido del servidor.")
            return

        try:
            duration_seconds, duration_text = self.parse_duration(duration_input)
        except ValueError as error:
            await interaction.response.send_message(f"Error: {error}")
            return

        if duration_seconds > 28 * 24 * 60 * 60:
            await interaction.response.send_message("Error: El timeout máximo permitido por Discord es de 28 días.")
            return

        timeout_duration = datetime.timedelta(seconds=duration_seconds)
        await member.timeout(timeout_duration, reason=reason)
        await self.send_log(interaction.guild, "Aislamiento (Mute)", member, interaction.user, reason, f"{duration_text}")
        await interaction.response.send_message(f"El usuario {member} ha sido silenciado por {duration_text}.")

    @app_commands.command(name="unmute", description="Quita el silencio a un miembro del servidor.")
    @app_commands.rename(member="miembro", reason="motivo")
    @discord.app_commands.checks.has_permissions(moderate_members=True)
    async def unmute_slash(self, interaction: discord.Interaction, member: discord.Member, reason: str = "No especificada"):
        await member.timeout(None, reason=reason)
        await self.send_log(interaction.guild, "Fin de Aislamiento (Unmute)", member, interaction.user, reason)
        await interaction.response.send_message(f"Se ha revocado la restriccion de silencio aplicada al usuario {member}.")

    @app_commands.command(name="say", description="Replica un texto a traves del bot.")
    @app_commands.rename(text_content="mensaje")
    async def say_slash(self, interaction: discord.Interaction, text_content: str, embed: bool = False):
        if not self.has_say_role(interaction.user):
            await interaction.response.send_message("Error: No cuenta con los roles requeridos para utilizar este comando.", ephemeral=True)
            return
        if embed:
            sent_embed = discord.Embed(description=text_content, color=discord.Color.yellow())
            sent_embed.set_author(name=interaction.user.display_name, icon_url=interaction.user.display_avatar.url)
            await interaction.response.send_message(embed=sent_embed)
            return
        await interaction.response.send_message(text_content)

    @app_commands.command(name="clean", description="Elimina una cantidad especifica de mensajes.")
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
        clean_id = user_input.replace("<@", "").replace(">", "").replace("!", "")
        member = ctx.guild.get_member(int(clean_id)) if user_input != "@everyone" else "@everyone"
        
        if member == "@everyone" or not isinstance(member, discord.Member):
            await ctx.send("Error: Debe mencionar a un miembro valido del servidor.")
            return
        await member.kick(reason=reason)
        await self.send_log(ctx.guild, "Expulsion", member, ctx.author, reason)
        await ctx.send(f"El usuario {member} ha sido expulsado correctamente.")

    @commands.command(name="ban")
    @commands.has_permissions(ban_members=True)
    async def ban_prefix(self, ctx, user_input: str, duration_input: str, *, reason: str = "No especificada"):
        clean_id = user_input.replace("<@", "").replace(">", "").replace("!", "")
        member = ctx.guild.get_member(int(clean_id)) if user_input != "@everyone" else "@everyone"
        
        if member == "@everyone" or not isinstance(member, discord.Member):
            await ctx.send("Error: Debe mencionar a un miembro valido del servidor.")
            return

        try:
            duration_seconds, duration_text = self.parse_duration(duration_input)
        except ValueError as error:
            await ctx.send(f"Error: {error}")
            return
            
        await ctx.guild.ban(member, reason=reason)
        await self.send_log(ctx.guild, "Baneo Temporal", member, ctx.author, reason, f"{duration_text}")
        await ctx.send(f"El usuario {member} ha sido baneado por un periodo de {duration_text}.")
        
        async def automatic_unban():
            await asyncio.sleep(duration_seconds)
            try:
                await ctx.guild.unban(member, reason="Expiracion del periodo de baneo establecido.")
                await self.send_log(ctx.guild, "Desbaneo Automatico", member, self.bot.user, "Periodo de sancion concluido.")
            except (discord.NotFound, discord.Forbidden):
                print(f"Automatic unban failed or user already unbanned: {member.id}")
        asyncio.create_task(automatic_unban())

    @commands.command(name="unban")
    @commands.has_permissions(ban_members=True)
    async def unban_prefix(self, ctx, user_id: str, *, reason: str = "No especificada"):
        target_user = await self.bot.fetch_user(int(user_id))
        await ctx.guild.unban(target_user, reason=reason)
        await self.send_log(ctx.guild, "Desbaneo", target_user, ctx.author, reason)
        await ctx.send(f"El baneo aplicado a la ID {user_id} ha sido revocado.")

    @commands.command(name="mute")
    @commands.has_permissions(moderate_members=True)
    async def mute_prefix(self, ctx, user_input: str, duration_input: str, *, reason: str = "No especificada"):
        clean_id = user_input.replace("<@", "").replace(">", "").replace("!", "")
        member = ctx.guild.get_member(int(clean_id)) if user_input != "@everyone" else "@everyone"
        
        if member == "@everyone" or not isinstance(member, discord.Member):
            await ctx.send("Error: Debe mencionar a un miembro valido del servidor.")
            return

        try:
            duration_seconds, duration_text = self.parse_duration(duration_input)
        except ValueError as error:
            await ctx.send(f"Error: {error}")
            return
            
        if duration_seconds > 28 * 24 * 60 * 60:
            await ctx.send("Error: El timeout máximo permitido por Discord es de 28 días.")
            return

        timeout_duration = datetime.timedelta(seconds=duration_seconds)
        await member.timeout(timeout_duration, reason=reason)
        await self.send_log(ctx.guild, "Aislamiento (Mute)", member, ctx.author, reason, f"{duration_text}")
        await ctx.send(f"El usuario {member} ha sido silenciado por un periodo de {duration_text}.")

    @commands.command(name="unmute")
    @commands.has_permissions(moderate_members=True)
    async def unmute_prefix(self, ctx, member: discord.Member, *, reason: str = "No especificada"):
        await member.timeout(None, reason=reason)
        await self.send_log(ctx.guild, "Fin de Aislamiento (Unmute)", member, ctx.author, reason)
        await ctx.send(f"Se ha revocado la restriccion de silencio aplicada al usuario {member}.")

    @commands.command(name="say")
    async def say_prefix(self, ctx, *, text_content: str):
        if not self.has_say_role(ctx.author):
            await ctx.send("Error: No cuenta con los roles requeridos para utilizar este comando.")
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

        embed = discord.Embed(title="Diagnostico de Estado del Sistema", color=discord.Color.blue())
        embed.add_field(name="Latencia de la API", value=f"{api_latency_ms} ms", inline=True)
        embed.add_field(name="Tiempo de actividad", value=uptime_string, inline=True)
        embed.add_field(name="Uso de memoria", value=f"{memory_usage_mb:.2f} MB", inline=True)
        embed.add_field(name="Servidores conectados", value=str(len(self.bot.guilds)), inline=True)
        embed.timestamp = datetime.datetime.now(datetime.timezone.utc)
        await ctx.send(embed=embed)

# --- REQUIRED SETUP FUNCTION ---
async def setup(bot):
    await bot.add_cog(Moderation(bot))