"""
Emojis personalizados del bot.

Provee funciones helper que devuelven el emoji correctamente formateado
(<a:nombre:id> si es animado, <:nombre:id> si no) usando el objeto real
cacheado por discord.py cuando está disponible, para no depender de
adivinar el nombre exacto del emoji.
"""

MONEY_EMOJI_ANIMATED_ID = 1525892936280178888
MONEY_EMOJI_STATIC_ID = 1525890385971253289
BAN_EMOJI_ID = 1525898489651920946
MUTE_EMOJI_ID = 1525898460958556310
ERROR_EMOJI_ID = 1525900386240565389
SUCCESS_EMOJI_ID = 1525901152196231352


def _format_cached_emoji(bot, emoji_id: int, animated_guess: bool = False) -> str:
    emoji = bot.get_emoji(emoji_id)
    if emoji is not None:
        return str(emoji)
    # Si el bot todavía no cacheó el emoji (por ejemplo, justo tras un
    # restart), devolvemos una etiqueta manual como último recurso.
    return f"<{'a' if animated_guess else ''}:emoji:{emoji_id}>"


def money_emoji(bot) -> str:
    """Devuelve el emoji de dinero, priorizando siempre la versión animada."""
    emoji = bot.get_emoji(MONEY_EMOJI_ANIMATED_ID)
    if emoji is not None and emoji.animated:
        return str(emoji)

    emoji = bot.get_emoji(MONEY_EMOJI_STATIC_ID)
    if emoji is not None:
        return str(emoji)

    # Ninguno de los dos está cacheado todavía: asumimos animado por defecto.
    return f"<a:emoji:{MONEY_EMOJI_ANIMATED_ID}>"


def format_money(bot, amount) -> str:
    """Formatea una cantidad de dinero como '500 <emoji>' en vez de '$500'."""
    try:
        amount_text = f"{int(amount):,}"
    except (TypeError, ValueError):
        amount_text = str(amount)
    return f"{amount_text} {money_emoji(bot)}"


def ban_emoji(bot) -> str:
    return _format_cached_emoji(bot, BAN_EMOJI_ID)


def mute_emoji(bot) -> str:
    return _format_cached_emoji(bot, MUTE_EMOJI_ID)


def error_emoji(bot) -> str:
    return _format_cached_emoji(bot, ERROR_EMOJI_ID)


def success_emoji(bot) -> str:
    return _format_cached_emoji(bot, SUCCESS_EMOJI_ID)


def error_embed(bot, description: str, title: str = None):
    import discord
    return discord.Embed(
        title=f"{error_emoji(bot)} {title or 'Ha ocurrido un error'}",
        description=description,
        color=discord.Color.red(),
    )


def success_embed(bot, description: str, title: str = None):
    import discord
    return discord.Embed(
        title=f"{success_emoji(bot)} {title or 'Listo'}",
        description=description,
        color=discord.Color.green(),
    )