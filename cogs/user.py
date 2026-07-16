import datetime

import discord
from discord import app_commands
from discord.ext import commands

from emojis import error_embed, success_embed


STAFF_ROLE_IDS = [
    1372448974211911770,
    1359359923770757150,
    1361138268829253875,
    1501881069731840050,
    1362456351263035553,
]


class UserCommands(commands.Cog):
    """Comandos que afectan principalmente a quien los ejecuta."""

    def __init__(self, bot):
        self.bot = bot

    def has_staff_role(self, member: discord.Member) -> bool:
        return any(role.id in STAFF_ROLE_IDS for role in member.roles)

    @commands.hybrid_command(
        name="changenickname",
        aliases=["nick", "changenick"],
        description="Cambia tu apodo. El equipo de moderación también puede cambiar el de otro usuario.",
    )
    @app_commands.describe(
        nickname="El nuevo apodo que quieres usar.",
        target_user="Usuario al que cambiarle el apodo (solo disponible para el equipo de moderación).",
    )
    async def changenickname(self, ctx: commands.Context, nickname: str, target_user: discord.Member = None):
        member = target_user or ctx.author

        if target_user is not None and target_user.id != ctx.author.id:
            if not self.has_staff_role(ctx.author):
                await ctx.send(
                    embed=error_embed(self.bot, "Solo el equipo de moderación puede cambiar el apodo de otros usuarios."),
                    ephemeral=True,
                )
                return

        if len(nickname) > 32:
            await ctx.send(
                embed=error_embed(self.bot, "El apodo no puede superar los 32 caracteres."),
                ephemeral=True,
            )
            return

        try:
            await member.edit(nick=nickname, reason=f"Apodo cambiado por {ctx.author} ({ctx.author.id})")
        except discord.Forbidden:
            await ctx.send(
                embed=error_embed(
                    self.bot,
                    "No tengo permisos suficientes o jerarquía para cambiar el apodo de este usuario.",
                ),
                ephemeral=True,
            )
            return
        except discord.HTTPException as error:
            await ctx.send(embed=error_embed(self.bot, f"No se pudo cambiar el apodo: {error}"), ephemeral=True)
            return

        if member.id == ctx.author.id:
            description = f"Tu apodo ahora es **{nickname}**."
        else:
            description = f"El apodo de {member.mention} ahora es **{nickname}**."
        await ctx.send(embed=success_embed(self.bot, description, title="Apodo actualizado"))

    @commands.hybrid_command(
        name="msg",
        description="Envía un mensaje privado a alguien mediante un hilo privado (no por MD).",
    )
    @app_commands.describe(
        target_user="Usuario que recibirá el mensaje.",
        message="El contenido del mensaje.",
    )
    async def msg_prefix(self, ctx: commands.Context, target_user: discord.Member, *, message: str):
        if ctx.interaction is not None:
            await ctx.interaction.response.defer(ephemeral=True)

        if target_user.id == ctx.author.id:
            await ctx.send(embed=error_embed(self.bot, "No puedes enviarte un mensaje privado a ti mismo."), ephemeral=True)
            return
        if target_user.bot:
            await ctx.send(embed=error_embed(self.bot, "No puedes enviarle un mensaje privado a un bot."), ephemeral=True)
            return

        channel = ctx.channel

        # Borrar el mensaje original lo antes posible (solo aplica a &msg; los slash
        # con respuesta ephemeral ya ocultan automáticamente el uso del comando).
        if ctx.interaction is None:
            try:
                await ctx.message.delete()
            except (discord.Forbidden, discord.HTTPException):
                pass

        try:
            thread = await channel.create_thread(
                name="🔒 Mensaje privado",
                type=discord.ChannelType.private_thread,
                auto_archive_duration=60,
                invitable=False,
                reason=f"Mensaje privado de {ctx.author} para {target_user}",
            )
        except (discord.Forbidden, discord.HTTPException):
            error_msg = error_embed(
                self.bot,
                "No pude crear un hilo privado en este canal. Verifica que tenga el permiso de "
                "'Crear hilos privados' y que el servidor los soporte.",
            )
            if ctx.interaction is not None:
                await ctx.send(embed=error_msg, ephemeral=True)
            else:
                try:
                    await channel.send(embed=error_msg, delete_after=8)
                except discord.HTTPException:
                    pass
            return

        try:
            await thread.add_user(ctx.author)
            await thread.add_user(target_user)
        except (discord.Forbidden, discord.HTTPException):
            pass

        embed = discord.Embed(
            title="✉️ Mensaje privado",
            description=message,
            color=discord.Color.gold(),
        )
        embed.set_author(name=str(ctx.author), icon_url=ctx.author.display_avatar.url)
        embed.timestamp = datetime.datetime.now(datetime.timezone.utc)

        try:
            await thread.send(content=target_user.mention, embed=embed)
        except (discord.Forbidden, discord.HTTPException):
            error_msg = error_embed(self.bot, "Creé el hilo pero no pude enviar el mensaje dentro de él.")
            if ctx.interaction is not None:
                await ctx.send(embed=error_msg, ephemeral=True)
            return

        if ctx.interaction is not None:
            await ctx.send(
                embed=success_embed(
                    self.bot,
                    f"Mensaje enviado a {target_user.mention} en un hilo privado: {thread.mention}",
                    title="Enviado",
                ),
                ephemeral=True,
            )


async def setup(bot):
    await bot.add_cog(UserCommands(bot))