import unicodedata

import discord
from discord.ext import commands


# ID del usuario al que el bot reacciona con 🐒 en absolutamente todos sus mensajes.
TROLL_TARGET_USER_ID = 1217622057576300554
ISRAEL_UNICODE_EMOJI = "🇮🇱"
MONKEY_UNICODE_EMOJI = "🐒"
FEMBOY_UNICODE_EMOJI = "🐔"

# IDs reales de los emojis personalizados del servidor.
PERU_MONKEY_EMOJI_ID = 1527109146334204047  # :peru_monkey:
PERUKONG_EMOJI_ID = 1395853256772817057     # :perukong:

# :flag_pe: es un emoji unicode estándar de Discord, no necesita ID.
PERU_FLAG_EMOJI = "🇵🇪"


def _strip_accents(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in normalized if not unicodedata.combining(ch))


class TrollFunctions(commands.Cog):
    """Funciones graciosas y sin ningún sentido, sin propósito serio."""

    def __init__(self, bot):
        self.bot = bot

    def _resolve_custom_emoji(self, emoji_id):
        if not emoji_id:
            return None
        emoji = self.bot.get_emoji(emoji_id)
        # Si el bot todavía no lo tiene cacheado, igual intentamos con el ID crudo
        # usando el formato de etiqueta manual como último recurso.
        return emoji if emoji is not None else f"<:emoji:{emoji_id}>"

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return

        # Reacciona con 🐒 a absolutamente todos los mensajes de este usuario específico.
        if message.author.id == TROLL_TARGET_USER_ID:
            try:
                await message.add_reaction(ISRAEL_UNICODE_EMOJI, MONKEY_UNICODE_EMOJI, FEMBOY_UNICODE_EMOJI)
            except (discord.Forbidden, discord.HTTPException):
                pass

        # Reacciona con el combo peruano si el mensaje contiene "peru" en cualquier parte,
        # sin importar mayúsculas/minúsculas ni tildes (así "Perú" también cuenta).
        normalized_content = _strip_accents(message.content.lower())
        if "peru" in normalized_content:
            reactions = []

            peru_monkey = self._resolve_custom_emoji(PERU_MONKEY_EMOJI_ID)
            if peru_monkey:
                reactions.append(peru_monkey)

            perukong = self._resolve_custom_emoji(PERUKONG_EMOJI_ID)
            if perukong:
                reactions.append(perukong)

            reactions.append(PERU_FLAG_EMOJI)

            for emoji in reactions:
                try:
                    await message.add_reaction(emoji)
                except (discord.Forbidden, discord.HTTPException):
                    pass


async def setup(bot):
    await bot.add_cog(TrollFunctions(bot))