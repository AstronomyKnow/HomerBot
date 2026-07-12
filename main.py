import discord
from discord.ext import commands
from discord.abc import Messageable
import os
import sys
import time
from backup_economy import backup_if_needed
from emojis import error_emoji

class MyBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True
        
        super().__init__(command_prefix='&', intents=intents, case_insensitive=True)
        self.guild_id = 1359359447591419984
        self.start_time = time.time()

    async def setup_hook(self):
        backup_if_needed()
        # Load all modules inside the cogs directory
        for filename in os.listdir('./cogs'):
            if filename.endswith('.py'):
                await self.load_extension(f'cogs.{filename[:-3]}')
                print(f"Module successfully loaded: {filename}")
        
        # Sync slash commands specifically to the assigned guild
        guild = discord.Object(id=self.guild_id)
        self.tree.copy_global_to(guild=guild)
        await self.tree.sync(guild=guild)
        print("Slash commands successfully synced to the target guild!")

    async def on_ready(self):
        print(f"System initialized successfully. Connected as {self.user}")

bot = MyBot()

_original_embed_init = discord.Embed.__init__
def patched_embed_init(self, *args, **kwargs):
    kwargs["color"] = discord.Color.gold()
    return _original_embed_init(self, *args, **kwargs)
discord.Embed.__init__ = patched_embed_init

def format_cooldown_duration(seconds: float) -> str:
    total_seconds = max(0, int(seconds))
    if total_seconds < 60:
        unit = "segundo" if total_seconds == 1 else "segundos"
        return f"{total_seconds} {unit}"
    if total_seconds < 3600:
        minutes = total_seconds // 60
        unit = "minuto" if minutes == 1 else "minutos"
        return f"{minutes} {unit}"
    if total_seconds < 86400:
        hours = total_seconds // 3600
        unit = "hora" if hours == 1 else "horas"
        return f"{hours} {unit}"
    days = total_seconds // 86400
    unit = "día" if days == 1 else "días"
    return f"{days} {unit}"


def build_cooldown_embed(retry_after: float) -> discord.Embed:
    return discord.Embed(
        title=f"{error_emoji(bot)} Cooldown activo",
        description=f"Tranquilo, vuelve a intentarlo en {format_cooldown_duration(retry_after)}.",
        color=discord.Color.orange()
    )

# --- GLOBAL PREFIX COMMAND ERROR HANDLING ---
@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandOnCooldown):
        await ctx.send(embed=build_cooldown_embed(error.retry_after))
        return
    if isinstance(error, commands.MissingPermissions):
        await ctx.send(embed=discord.Embed(title=f"{error_emoji(bot)} Error", description="No cuentas con los permisos necesarios para ejecutar este comando.", color=discord.Color.red()))
    elif isinstance(error, commands.MissingRequiredArgument):
        await ctx.send(embed=discord.Embed(title=f"{error_emoji(bot)} Error", description=f"Argumentos insuficientes. Uso correcto: `{ctx.prefix}{ctx.command.name} [argumentos]`", color=discord.Color.red()))
    elif isinstance(error, commands.BadArgument):
        await ctx.send(embed=discord.Embed(title=f"{error_emoji(bot)} Error", description="Los argumentos proporcionados no son válidos.", color=discord.Color.red()))
    elif isinstance(error, commands.CommandInvokeError) and isinstance(error.original, discord.Forbidden):
        await ctx.send(embed=discord.Embed(title=f"{error_emoji(bot)} Error", description="El bot no tiene permisos suficientes o jerarquía para realizar esta acción.", color=discord.Color.red()))
    else:
        print(f"Prefix command error detected: {error}")
        await ctx.send(embed=discord.Embed(title=f"{error_emoji(bot)} Error inesperado", description=f"Ocurrió un error imprevisto: {error}", color=discord.Color.red()))

# --- GLOBAL SLASH COMMAND ERROR HANDLING ---
@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error):
    if isinstance(error, discord.app_commands.CommandOnCooldown):
        if interaction.response.is_done():
            await interaction.followup.send(embed=build_cooldown_embed(error.retry_after), ephemeral=True)
        else:
            await interaction.response.send_message(embed=build_cooldown_embed(error.retry_after), ephemeral=True)
        return
    if isinstance(error, discord.app_commands.MissingPermissions):
        await interaction.response.send_message(embed=discord.Embed(title=f"{error_emoji(bot)} Error", description="No cuentas con los permisos necesarios para ejecutar este comando.", color=discord.Color.red()), ephemeral=True)
    elif isinstance(error, discord.app_commands.CommandInvokeError) and isinstance(error.original, discord.Forbidden):
        await interaction.response.send_message(embed=discord.Embed(title=f"{error_emoji(bot)} Error", description="El bot no tiene permisos suficientes o jerarquía para realizar esta acción.", color=discord.Color.red()), ephemeral=True)
    else:
        print(f"Slash command error detected: {error}")
        embed = discord.Embed(title=f"{error_emoji(bot)} Error inesperado", description=f"Ocurrió un error de ejecución: {error}", color=discord.Color.red())
        if interaction.response.is_done():
            await interaction.followup.send(embed=embed)
        else:
            await interaction.response.send_message(embed=embed, ephemeral=True)

# Run the bot with your token from the environment
TOKEN = os.environ.get("DISCORD_TOKEN")
if not TOKEN:
    print("ERROR: DISCORD_TOKEN no está configurado. Define la variable de entorno antes de arrancar el bot.")
    sys.exit(1)

bot.run(TOKEN)