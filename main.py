import discord
from discord.ext import commands
from discord.abc import Messageable
import os
import time
from backup_economy import backup_if_needed

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
        title="⏳ Cooldown activo",
        description=f"💤 Tranquilo, vuelve a intentarlo en {format_cooldown_duration(retry_after)}.",
        color=discord.Color.orange()
    )

# --- GLOBAL PREFIX COMMAND ERROR HANDLING ---
@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandOnCooldown):
        await ctx.send(embed=build_cooldown_embed(error.retry_after))
        return
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("Error: No cuenta con los permisos necesarios para ejecutar este comando.")
    elif isinstance(error, commands.MissingRequiredArgument):
        await ctx.send(f"Error: Argumentos insuficientes. Uso correcto: {ctx.prefix}{ctx.command.name} [argumentos]")
    elif isinstance(error, commands.BadArgument):
        await ctx.send("Error: Los argumentos proporcionados no son validos.")
    elif isinstance(error, commands.CommandInvokeError) and isinstance(error.original, discord.Forbidden):
        await ctx.send("Error: El bot no tiene permisos suficientes o jerarquia para realizar esta accion.")
    else:
        print(f"Prefix command error detected: {error}")
        await ctx.send(f"Ocurrio un error imprevisto: {error}")

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
        await interaction.response.send_message("Error: No cuenta con los permisos necesarios para ejecutar este comando.", ephemeral=True)
    elif isinstance(error, discord.app_commands.CommandInvokeError) and isinstance(error.original, discord.Forbidden):
        await interaction.response.send_message("Error: El bot no tiene permisos suficientes o jerarquia para realizar esta accion.", ephemeral=True)
    else:
        print(f"Slash command error detected: {error}")
        if interaction.response.is_done():
            await interaction.followup.send(f"Ocurrio un error de ejecucion: {error}")
        else:
            await interaction.response.send_message(f"Ocurrio un error imprevisto: {error}", ephemeral=True)

# Run the bot with your token from the environment
bot.run(os.environ.get("DISCORD_TOKEN"))