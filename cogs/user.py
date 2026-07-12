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


async def setup(bot):
    await bot.add_cog(UserCommands(bot))