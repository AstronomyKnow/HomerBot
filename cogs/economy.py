import discord
from discord import app_commands
from discord.ext import commands, tasks
import sqlite3
import random
import datetime
import json
import asyncio
import shutil
from pathlib import Path

from backup_economy import resolve_database_paths
from emojis import format_money, error_emoji, success_emoji

FREE_ROLE_ID = 1526033793037766749
PRISON_ROLE_ID = 1526031208885129367
PRISON_CHANNEL_ID = 1526030677752156281
LOG_CHANNEL_ID = 1393450057189167234

STAFF_ROLE_IDS = [
    1372448974211911770,
    1359359923770757150,
    1361138268829253875,
    1501881069731840050,
    1362456351263035553,
]

SCHEDULE_CYCLE = ["celda", "comida", "celda", "libre"]
SCHEDULE_LABELS = {
    "celda": "🔒 Horario de Celda",
    "comida": "🍽️ Horario de Comida",
    "libre": "🏃 Tiempo Libre",
}

MARKET_CYCLE_SECONDS = 24 * 3600
MARKET_OPEN_SECONDS = 6 * 3600

BRIBE_COST = 5000
HITMAN_COST = 100000
LADRON_PRICE = 12000
BLACK_MARKET_JAIL_CHANCE = 0.10
BLACK_MARKET_JAIL_SECONDS = 24 * 3600


def current_schedule_index() -> int:
    now = datetime.datetime.now(datetime.timezone.utc)
    return int(now.timestamp() // 3600) % len(SCHEDULE_CYCLE)


def current_schedule() -> str:
    return SCHEDULE_CYCLE[current_schedule_index()]


def seconds_left_in_schedule() -> int:
    now = datetime.datetime.now(datetime.timezone.utc)
    return 3600 - (int(now.timestamp()) % 3600)


def is_market_open() -> bool:
    now = datetime.datetime.now(datetime.timezone.utc)
    position = int(now.timestamp()) % MARKET_CYCLE_SECONDS
    return position < MARKET_OPEN_SECONDS


def market_seconds_remaining():
    now = datetime.datetime.now(datetime.timezone.utc)
    position = int(now.timestamp()) % MARKET_CYCLE_SECONDS
    if position < MARKET_OPEN_SECONDS:
        return True, MARKET_OPEN_SECONDS - position
    return False, MARKET_CYCLE_SECONDS - position


def format_prison_duration(seconds) -> str:
    seconds = max(0, int(seconds))
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, _ = divmod(rem, 60)
    parts = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    if minutes and not days:
        parts.append(f"{minutes}m")
    return " ".join(parts) if parts else "menos de 1 minuto"


def parse_duration_to_seconds(duration_input: str):
    text = str(duration_input).strip().lower()
    if not text:
        return None
    if text.isdigit():
        return int(text) * 60
    unit = text[-1]
    value_text = text[:-1]
    if not value_text.isdigit():
        return None
    value = int(value_text)
    if value <= 0:
        return None
    multipliers = {"m": 60, "h": 3600, "d": 86400, "a": 31536000}
    if unit not in multipliers:
        return None
    return value * multipliers[unit]


# --- CRYPTO MINIGAME UI INTERFACE ---
class CryptoView(discord.ui.View):
    def __init__(self, economy_cog, user_id: int, investment: int, trend: bool):
        super().__init__(timeout=30)
        self.economy = economy_cog
        self.user_id = user_id
        self.investment = investment
        self.trend = trend
        self.action = None

    @discord.ui.button(label="Comprar (Long)", style=discord.ButtonStyle.green)
    async def buy_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id: 
            await interaction.response.send_message(embed=self.economy.error_embed("Esta no es tu sesión de inversión."), ephemeral=True)
            return
        self.action = "buy"
        await self.process_result(interaction)

    @discord.ui.button(label="Vender (Short)", style=discord.ButtonStyle.red)
    async def sell_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id: 
            await interaction.response.send_message(embed=self.economy.error_embed("Esta no es tu sesión de inversión."), ephemeral=True)
            return
        self.action = "sell"
        await self.process_result(interaction)

    async def process_result(self, interaction: discord.Interaction):
        for child in self.children:
            child.disabled = True
        await interaction.message.edit(view=self)
        
        await interaction.response.send_message(embed=discord.Embed(
            title="⏳ Procesando transacción",
            description="Procesando tu operación en la blockchain (5 segundos)...",
            color=discord.Color.gold(),
        ))
        await asyncio.sleep(5)
        
        user_data = self.economy.get_user_data(self.user_id)
        if user_data["wallet"] < self.investment:
            await interaction.followup.send(embed=self.economy.error_embed("Ya no dispones de los fondos suficientes en tu billetera."))
            return

        market_went_up = random.random() < 0.70 if self.trend else random.random() < 0.30
        win = False
        
        if self.action == "buy" and market_went_up: win = True
        elif self.action == "sell" and not market_went_up: win = True
        
        if win:
            self.economy.update_balances(self.user_id, wallet_change=self.investment, bank_change=0)
            await interaction.followup.send(embed=self.economy.success_embed(
                f"El mercado se movió a tu favor y ganaste **{self.economy.money(self.investment)}** en efectivo.",
                title="Operación exitosa",
            ))
        else:
            self.economy.update_balances(self.user_id, wallet_change=-self.investment, bank_change=0)
            await interaction.followup.send(embed=self.economy.error_embed(
                f"El mercado se movió en tu contra y perdiste los **{self.economy.money(self.investment)}** invertidos.",
                title="Liquidado",
            ))
        self.stop()


# --- MAIN ECONOMY COG ---
class Economy(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.db_path = str(resolve_database_paths("economy.db")[0])
        self.staff_roles = [1362456351263035553, 1361138268829253875, 1359359923770757150, 1372448974211911770]
        
        self.prices = {
            "seguro": 15000,
            "empresa": 75000,
            "empleado": 10000,
            "acciones_yt": 5000,
            "acciones_ms": 4500,
            "mega_yt": 750000,
            "mega_ms": 650000
        }
        
        self.passive_interval_seconds = 30 * 60
        self.passive_last_run = datetime.datetime.now(datetime.timezone.utc)
        self.topbar_channel_id = 1524131320962220084
        self.audit_channel_id = 1524421682377392338
        self.allowed_channel_id = 1375975206719586485
        self.topbar_message_id = None
        self.initialize_database()
        self.prison_db_path = str(resolve_database_paths("prison.db")[0])
        self.initialize_prison_database()
        self.passive_engine.start()
        self.topbar_engine.start()
        self.release_engine.start()

    def cog_unload(self):
        self.passive_engine.cancel()
        self.topbar_engine.cancel()
        self.release_engine.cancel()

    # --- DATABASE UTILITIES ---
    def initialize_database(self):
        try:
            db_path = Path(self.db_path)
            db_path.parent.mkdir(parents=True, exist_ok=True)

            legacy_path = Path.cwd() / "economy.db"
            if not db_path.exists() and legacy_path.exists():
                shutil.copy2(legacy_path, db_path)

            backup_path = db_path.with_suffix(".db.backup")
            if not db_path.exists() and backup_path.exists():
                shutil.copy2(backup_path, db_path)

            conn = sqlite3.connect(str(db_path))
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    wallet INTEGER DEFAULT 0,
                    bank INTEGER DEFAULT 0,
                    last_daily TEXT,
                    pending_wallet INTEGER DEFAULT 0,
                    pending_bank INTEGER DEFAULT 0,
                    fine INTEGER DEFAULT 0
                )
            """)
            
            migration_columns = [
                ("insurance", "INTEGER DEFAULT 0"),
                ("company", "INTEGER DEFAULT 0"),
                ("employees", "INTEGER DEFAULT 0"),
                ("robbery_employees", "INTEGER DEFAULT 0"),
                ("last_salary_pay", "TEXT"),
                ("stocks", "TEXT DEFAULT '{}'"),
                ("mega_companies", "TEXT DEFAULT '[]'"),
                ("pending_wallet", "INTEGER DEFAULT 0"),
                ("pending_bank", "INTEGER DEFAULT 0"),
                ("fine", "INTEGER DEFAULT 0"),
                ("fine_count", "INTEGER DEFAULT 0"),
                ("fine_since", "TEXT"),
                ("fine_penalized", "INTEGER DEFAULT 0"),
                ("salary_penalty_processed", "INTEGER DEFAULT 0")
            ]
            
            for column_name, column_type in migration_columns:
                try:
                    cursor.execute(f"ALTER TABLE users ADD COLUMN {column_name} {column_type}")
                except sqlite3.OperationalError:
                    pass

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS recent_thefts (
                    thief_id INTEGER,
                    victim_id INTEGER,
                    amount_stolen INTEGER,
                    timestamp TEXT,
                    resolved INTEGER DEFAULT 0
                )
            """)
            conn.commit()
            conn.close()
            print("Advanced Economy database verified and fully migrated.")
        except Exception as e:
            print(f"CRITICAL: Failed to initialize economy database: {e}")

    def get_user_data(self, user_id: int):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("""
            SELECT wallet, bank, last_daily, insurance, company, employees, 
                   robbery_employees, last_salary_pay, stocks, mega_companies,
                   pending_wallet, pending_bank, fine
            FROM users WHERE user_id = ?
        """, (user_id,))
        row = cursor.fetchone()
        
        if not row:
            cursor.execute("""
                INSERT INTO users (user_id, wallet, bank, last_daily, insurance, company, employees, robbery_employees, last_salary_pay, stocks, mega_companies, pending_wallet, pending_bank, fine) 
                VALUES (?, 0, 0, NULL, 0, 0, 0, 0, NULL, '{}', '[]', 0, 0, 0)
            """, (user_id,))
            conn.commit()
            conn.close()
            return {
                "wallet": 0, "bank": 0, "last_daily": None, "insurance": 0, "company": 0,
                "employees": 0, "robbery_employees": 0, "last_salary_pay": None,
                "stocks": {}, "mega_companies": [], "pending_wallet": 0, "pending_bank": 0, "fine": 0
            }
            
        conn.close()
        employees = row[5]
        robbery_employees = row[6]
        last_pay_str = row[7]
        
        if (employees > 0 or robbery_employees > 0) and last_pay_str:
            last_pay_dt = datetime.datetime.fromisoformat(last_pay_str)
            if datetime.datetime.now() - last_pay_dt > datetime.timedelta(hours=24):
                conn = sqlite3.connect(self.db_path)
                cursor = conn.cursor()
                cursor.execute("UPDATE users SET employees = 0, robbery_employees = 0 WHERE user_id = ?", (user_id,))
                conn.commit()
                conn.close()
                employees = 0
                robbery_employees = 0
                print(f"User {user_id} failed to pay salaries on time. Employees left the enterprise.")

        return {
            "wallet": row[0], "bank": row[1], "last_daily": row[2], "insurance": row[3],
            "company": row[4], "employees": employees, "robbery_employees": robbery_employees,
            "last_salary_pay": last_pay_str, "stocks": json.loads(row[8]), "mega_companies": json.loads(row[9]),
            "pending_wallet": row[10], "pending_bank": row[11], "fine": row[12]
        }

    def update_balances(self, user_id: int, wallet_change: int, bank_change: int, reason: str = "Ajuste de saldo",
                        details: str = None, actor_id: int = None, target_user_id: int = None):
        data = self.get_user_data(user_id)
        before_wallet = data["wallet"]
        before_bank = data["bank"]
        new_wallet = max(0, before_wallet + wallet_change)
        new_bank = max(0, before_bank + bank_change)
        
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("UPDATE users SET wallet = ?, bank = ? WHERE user_id = ?", (new_wallet, new_bank, user_id))
        conn.commit()
        conn.close()

        if wallet_change != 0 or bank_change != 0:
            asyncio.create_task(self.log_balance_change(
                user_id,
                wallet_change,
                bank_change,
                before_wallet,
                before_bank,
                new_wallet,
                new_bank,
                reason=reason,
                details=details,
                actor_id=actor_id,
                target_user_id=target_user_id
            ))

    def update_asset(self, user_id: int, column_name: str, value):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute(f"UPDATE users SET {column_name} = ? WHERE user_id = ?", (value, user_id))
        conn.commit()
        conn.close()

    def get_fine_tracking(self, user_id: int):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT fine_count, fine_since, fine_penalized FROM users WHERE user_id = ?", (user_id,))
        row = cursor.fetchone()
        conn.close()
        if not row:
            return {"fine_count": 0, "fine_since": None, "fine_penalized": 0}
        return {"fine_count": row[0] or 0, "fine_since": row[1], "fine_penalized": row[2] or 0}

    def apply_fine(self, user_id: int, amount: int):
        if amount <= 0:
            return 0
        data = self.get_user_data(user_id)
        self.update_asset(user_id, "fine", data["fine"] + amount)

        tracking = self.get_fine_tracking(user_id)
        self.update_asset(user_id, "fine_count", tracking["fine_count"] + 1)
        self.update_asset(user_id, "fine_penalized", 0)
        if not tracking["fine_since"]:
            self.update_asset(user_id, "fine_since", datetime.datetime.now(datetime.timezone.utc).isoformat())

        asyncio.create_task(self.log_economy_event(
            "⚖️ Multa impuesta",
            "Se aplicó una multa a un usuario de economía.",
            user_id=user_id,
            fields=[("💸 Monto", self.money(amount)), ("🧾 Multa nueva", self.money(data['fine'] + amount))],
            color=discord.Color.red()
        ))
        return amount

    def pay_fine(self, user_id: int, amount: int):
        data = self.get_user_data(user_id)
        if data["fine"] <= 0:
            return 0

        payable = min(amount, data["fine"])
        if payable <= 0:
            return 0

        wallet_available = data["wallet"]
        bank_available = data["bank"]
        total_available = wallet_available + bank_available
        if total_available <= 0:
            return 0

        payable = min(payable, total_available)
        wallet_to_pay = min(wallet_available, payable)
        bank_to_pay = payable - wallet_to_pay

        self.update_balances(user_id, wallet_change=-wallet_to_pay, bank_change=-bank_to_pay, reason="Pago de multa", details=f"Pagó {self.money(payable)} de una multa pendiente.")
        new_fine = data["fine"] - payable
        self.update_asset(user_id, "fine", new_fine)
        if new_fine <= 0:
            self.update_asset(user_id, "fine_count", 0)
            self.update_asset(user_id, "fine_since", None)
            self.update_asset(user_id, "fine_penalized", 0)
        return payable

    def accumulate_pending_income(self, user_id: int, wallet_change: int = 0, bank_change: int = 0):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE users SET pending_wallet = max(0, pending_wallet + ?), pending_bank = max(0, pending_bank + ?) WHERE user_id = ?",
            (wallet_change, bank_change, user_id)
        )
        conn.commit()
        conn.close()

    def collect_pending_income(self, user_id: int):
        data = self.get_user_data(user_id)
        wallet_pending = data["pending_wallet"]
        bank_pending = data["pending_bank"]

        if wallet_pending == 0 and bank_pending == 0:
            return 0, 0

        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE users SET wallet = wallet + ?, bank = bank + ?, pending_wallet = 0, pending_bank = 0 WHERE user_id = ?",
            (wallet_pending, bank_pending, user_id)
        )
        conn.commit()
        conn.close()
        if wallet_pending != 0 or bank_pending != 0:
            asyncio.create_task(self.log_economy_event(
                "💸 Ingreso pasivo cobrado",
                "Se cobraron fondos pendientes de ingresos AFK o pasivos.",
                user_id=user_id,
                fields=[("💵 Billetera cobrada", self.money(wallet_pending)), ("🏦 Banco cobrado", self.money(bank_pending))],
                color=discord.Color.green()
            ))
        return wallet_pending, bank_pending

    def parse_balance_scope(self, value):
        if value is None:
            return "wallet"
        text = str(value).strip().lower()
        if text in {"1", "billetera", "wallet", "cartera"}:
            return "wallet"
        if text in {"2", "banco", "bank"}:
            return "bank"
        if text in {"3", "ambos", "both", "todo"}:
            return "both"
        return None

    def scope_label(self, scope):
        if scope == "bank":
            return "el banco"
        if scope == "both":
            return "la billetera y el banco"
        return "la billetera"

    def is_staff(self, user: discord.Member) -> bool:
        return any(role.id in self.staff_roles for role in user.roles)

    def money(self, amount) -> str:
        return format_money(self.bot, amount)

    def error_embed(self, description: str, title: str = None) -> discord.Embed:
        return discord.Embed(
            title=f"{error_emoji(self.bot)} {title or 'Ha ocurrido un error'}",
            description=description,
            color=discord.Color.red(),
        )

    def success_embed(self, description: str, title: str = None) -> discord.Embed:
        return discord.Embed(
            title=f"{success_emoji(self.bot)} {title or 'Listo'}",
            description=description,
            color=discord.Color.green(),
        )

    def format_duration(self, seconds: float) -> str:
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

    def format_cooldown_message(self, seconds: float) -> str:
        return f"💤 Tranquilo, vuelve a intentarlo en {self.format_duration(seconds)}."

    def is_allowed_channel(self, ctx) -> bool:
        return getattr(ctx.channel, "id", None) == self.allowed_channel_id

    async def enforce_channel(self, ctx):
        if self.is_allowed_channel(ctx):
            return True
        await ctx.send(
            embed=self.error_embed(
                "Este sistema de economía solo está disponible en el canal designado. Ve a https://discord.com/channels/1359359447591419984/1375975206719586485",
                title="Canal incorrecto",
            ),
            ephemeral=True,
        )
        return False

    async def enforce_channel_interaction(self, interaction):
        if interaction.channel_id == self.allowed_channel_id:
            return True
        embed = self.error_embed(
            "Este sistema de economía solo está disponible en el canal designado. Ve a https://discord.com/channels/1359359447591419984/1375975206719586485",
            title="Canal incorrecto",
        )
        if interaction.response.is_done():
            await interaction.followup.send(embed=embed, ephemeral=True)
        else:
            await interaction.response.send_message(embed=embed, ephemeral=True)
        return False

    def _user_label(self, user_id: int) -> str:
        user = self.bot.get_user(user_id)
        if user:
            return f"{user.name}#{user.discriminator} ({user.id})"
        return f"Usuario {user_id}"

    async def get_audit_channel(self):
        channel = self.bot.get_channel(self.audit_channel_id)
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(self.audit_channel_id)
            except Exception:
                return None
        return channel

    async def log_economy_event(self, title: str, description: str, *, user_id: int = None, target_user_id: int = None,
                                fields=None, ctx=None, color=None):
        channel = await self.get_audit_channel()
        if channel is None:
            return

        embed = discord.Embed(
            title=title,
            description=description,
            color=color or discord.Color.blurple(),
            timestamp=datetime.datetime.now(datetime.timezone.utc)
        )

        if user_id is not None:
            embed.add_field(name="👤 Usuario afectado", value=self._user_label(user_id), inline=True)
        if target_user_id is not None:
            embed.add_field(name="🎯 Usuario secundario", value=self._user_label(target_user_id), inline=True)
        if ctx is not None:
            server_name = ctx.guild.name if ctx.guild else "DM"
            channel_name = getattr(ctx.channel, "mention", str(ctx.channel))
            command_name = f"{ctx.prefix}{ctx.command.name}" if ctx.prefix and getattr(ctx, "command", None) else f"/{getattr(ctx.command, 'name', 'desconocido')}"
            embed.add_field(name="🗂️ Servidor", value=server_name, inline=True)
            embed.add_field(name="📍 Canal", value=channel_name, inline=True)
            embed.add_field(name="🧾 Comando", value=command_name, inline=True)

        if fields:
            for field_name, field_value in fields:
                embed.add_field(name=field_name, value=field_value, inline=False)

        try:
            await channel.send(embed=embed)
        except Exception as e:
            print(f"No se pudo enviar el log de economía: {e}")

    async def log_balance_change(self, user_id: int, wallet_change: int, bank_change: int, before_wallet: int, before_bank: int,
                                 new_wallet: int, new_bank: int, reason: str = "Ajuste de saldo", details: str = None,
                                 actor_id: int = None, target_user_id: int = None):
        if wallet_change == 0 and bank_change == 0:
            return

        fields = [
            ("🔄 Motivo", reason),
            ("💵 Cambio billetera", f"{'+' if wallet_change >= 0 else '-'}{self.money(abs(wallet_change))}"),
            ("🏦 Cambio banco", f"{'+' if bank_change >= 0 else '-'}{self.money(abs(bank_change))}"),
            ("📊 Balance anterior", f"💵 {self.money(before_wallet)} | 🏦 {self.money(before_bank)}"),
            ("📈 Nuevo balance", f"💵 {self.money(new_wallet)} | 🏦 {self.money(new_bank)}")
        ]
        if details:
            fields.append(("📝 Detalle", details))
        if actor_id is not None:
            fields.append(("⚙️ Actor", self._user_label(actor_id)))
        if target_user_id is not None:
            fields.append(("🎯 Objetivo", self._user_label(target_user_id)))

        await self.log_economy_event(
            "💰 Cambio de saldo económico",
            "Se registró un movimiento financiero en la economía del servidor.",
            user_id=user_id,
            target_user_id=target_user_id,
            fields=fields,
            color=discord.Color.orange()
        )

    async def update_topbar_message(self):
        try:
            channel = self.bot.get_channel(self.topbar_channel_id)
            if channel is None:
                channel = await self.bot.fetch_channel(self.topbar_channel_id)
            if channel is None:
                return

            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute("""
                SELECT user_id, wallet, bank, (wallet + bank) AS total_money
                FROM users
                WHERE (wallet + bank) >= 1
                ORDER BY total_money DESC
            """)
            rows = cursor.fetchall()
            conn.close()

            if not rows:
                description = "🏆 Topbar económica vacía por ahora."
            else:
                lines = []
                for index, (user_id, wallet, bank, total_money) in enumerate(rows, start=1):
                    member = channel.guild.get_member(user_id)
                    name = member.display_name if member else f"Usuario {user_id}"
                    lines.append(f"{index}. {name} — 💵 {self.money(wallet)} | 🏦 {self.money(bank)} | 💰 {self.money(total_money)}")

                description = (
                    "📅 " + datetime.datetime.now().strftime('%d/%m/%Y %H:%M') + "\n\n" + "\n".join(lines)
                )

            embed = discord.Embed(
                title="🏆 Topbar económica",
                description=description[:2000],
                color=discord.Color.gold()
            )
            embed.set_footer(text="Actualizada cada minuto")

            if self.topbar_message_id:
                try:
                    message = await channel.fetch_message(self.topbar_message_id)
                    await message.edit(embed=embed)
                except discord.NotFound:
                    self.topbar_message_id = None
                    message = await channel.send(embed=embed)
                    self.topbar_message_id = message.id
            else:
                message = await channel.send(embed=embed)
                self.topbar_message_id = message.id
        except Exception as e:
            print(f"Error updating economy topbar: {e}")

    # --- BACKGROUND PASSIVE ENGINE ---
    @tasks.loop(minutes=30)
    async def passive_engine(self):
        try:
            self.passive_last_run = datetime.datetime.now(datetime.timezone.utc)
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute("""
                SELECT user_id, employees, stocks, mega_companies, robbery_employees, fine,
                       last_salary_pay, fine_since, fine_penalized, fine_count, salary_penalty_processed
                FROM users
            """)
            all_users = cursor.fetchall()

            pending_jail_events = []
            now_utc = datetime.datetime.now(datetime.timezone.utc)
            now_naive = datetime.datetime.now()

            for row in all_users:
                (user_id, employees, stocks_str, mega_str, rob_emp, fine,
                 last_pay_str, fine_since_str, fine_penalized, fine_count, salary_penalty_processed) = row
                stocks = json.loads(stocks_str)
                mega_companies = json.loads(mega_str)
                
                wallet_yield = 0
                bank_yield = 0
                multiplier = 0.25 if fine > 0 else 1.0
                
                if employees > 0:
                    bank_yield += int(employees * random.randint(150, 400) * multiplier)
                
                for mega in mega_companies:
                    bank_yield += int(random.randint(5000, 15000) * multiplier)
                    
                for stock, count in stocks.items():
                    if count > 0:
                        bank_yield += int(random.randint(-400, 1200) * count * multiplier)
                
                if rob_emp > 0:
                    for _ in range(rob_emp):
                        if random.random() < 0.5:
                            if random.random() < 0.20:
                                wallet_yield += random.randint(300, 600)
                        else:
                            cursor.execute("SELECT user_id, wallet FROM users WHERE user_id != ?", (user_id,))
                            targets = cursor.fetchall()
                            if targets:
                                t_id, t_wallet = random.choice(targets)
                                if t_wallet > 100:
                                    stolen = int(t_wallet * 0.2)
                                    cursor.execute("UPDATE users SET wallet = max(0, wallet - ?) WHERE user_id = ?", (stolen, t_id))
                                    wallet_yield += int(stolen * multiplier)

                    # Riesgo de que atrapen al usuario por tener Empleados de Robo activos
                    if random.random() < 0.05:
                        pending_jail_events.append(
                            (user_id, 5 * 86400, "Uno de tus Empleados de Robo fue descubierto y te delató.")
                        )
                
                if wallet_yield != 0 or bank_yield != 0:
                    cursor.execute(
                        "UPDATE users SET pending_wallet = max(0, pending_wallet + ?), pending_bank = max(0, pending_bank + ?) WHERE user_id = ?",
                        (wallet_yield, bank_yield, user_id)
                    )

                # Nómina impaga: además de despedir empleados, hay riesgo de ir a prisión
                if (employees > 0 or rob_emp > 0) and last_pay_str and not salary_penalty_processed:
                    last_pay_dt = datetime.datetime.fromisoformat(last_pay_str)
                    if now_naive - last_pay_dt > datetime.timedelta(hours=24):
                        cursor.execute(
                            "UPDATE users SET employees = 0, robbery_employees = 0, salary_penalty_processed = 1 WHERE user_id = ?",
                            (user_id,)
                        )
                        if random.random() < 0.25:
                            pending_jail_events.append(
                                (user_id, 2 * 86400, "No le pagaste a tus empleados a tiempo y decidieron denunciarte.")
                            )

                # Multas vencidas por más de 1 hora sin pagar
                if fine > 0 and fine_since_str and not fine_penalized:
                    fine_since_dt = datetime.datetime.fromisoformat(fine_since_str)
                    if now_utc - fine_since_dt >= datetime.timedelta(hours=1):
                        cursor.execute("UPDATE users SET fine_penalized = 1 WHERE user_id = ?", (user_id,))
                        days = max(1, fine_count)
                        pending_jail_events.append(
                            (user_id, days * 86400, f"No pagaste tu(s) multa(s) a tiempo ({fine_count} acumulada(s)).")
                        )
            
            conn.commit()
            conn.close()

            if pending_jail_events:
                for user_id, seconds, reason in pending_jail_events:
                    guild, member = self.find_member(user_id)
                    if guild and member:
                        try:
                            await self.jail_member(member, guild, seconds, reason)
                        except Exception as jail_error:
                            print(f"Error al encarcelar a {user_id} desde passive_engine: {jail_error}")
        except Exception as e:
            print(f"Error inside passive income engine loop: {e}")

    @passive_engine.before_loop
    async def before_passive_engine(self):
        await self.bot.wait_until_ready()

    @tasks.loop(minutes=1)
    async def topbar_engine(self):
        await self.update_topbar_message()

    @topbar_engine.before_loop
    async def before_topbar_engine(self):
        await self.bot.wait_until_ready()

    @commands.Cog.listener()
    async def on_command(self, ctx):
        if not ctx.command or ctx.command.cog is not self:
            return
        await self.log_economy_event(
            "🧾 Comando de economía ejecutado",
            "Se registró la ejecución de un comando del sistema de economía.",
            user_id=ctx.author.id,
            fields=[("🧪 Comando", ctx.command.name), ("📝 Mensaje", ctx.message.content)],
            ctx=ctx,
            color=discord.Color.teal()
        )

    @commands.Cog.listener()
    async def on_app_command_completion(self, interaction, command):
        if getattr(command, "cog", None) is not self:
            return
        await self.log_economy_event(
            "🧾 Comando de economía ejecutado",
            "Se registró la ejecución de un comando de economía vía interacción.",
            user_id=interaction.user.id,
            fields=[("🧪 Comando", f"/{command.name}")],
            color=discord.Color.teal()
        )

    # --- HYBRID COMMANDS SYSTEM (PREFIX & SLASH) ---

    @commands.hybrid_command(name="ecohelp", description="Muestra cómo funciona absolutamente todo: economía, prisión y mercado negro.")
    async def ecohelp(self, ctx: commands.Context):
        if not await self.enforce_channel(ctx):
            return

        # --- EMBED 1: ECONOMÍA LEGAL ---
        embed_economy = discord.Embed(
            title="📖 Manual — Economía Legal",
            description="Todos los comandos responden tanto a `/` como al prefijo `&`.",
            color=discord.Color.gold(),
        )
        embed_economy.add_field(
            name="💳 Conceptos básicos",
            value=(
                "**Billetera**: dinero líquido, se puede robar.\n"
                "**Banco**: dinero guardado, solo un 10% es robable.\n"
                "**Multa**: deuda pendiente. Mientras tengas una multa activa (`fine > 0`), "
                "todos tus ingresos pasivos y de `&work`/`&crime` se reducen a un **25%**.\n"
                f"**Bono diario**: `&daily` da {self.money(500)}, una vez cada 24h."
            ),
            inline=False,
        )
        embed_economy.add_field(
            name="🛠️ &work (cooldown 30s)",
            value=(
                "Genera 100–300 de dinero legal. +75% si tienes Empresa. "
                "Se reduce a 25% si tienes una multa pendiente.\n"
                "**Si estás en prisión**, `&work` genera 80–220 de **dinero ilegal** en su lugar (única forma de conseguirlo)."
            ),
            inline=False,
        )
        embed_economy.add_field(
            name="🚨 &crime (cooldown 3m)",
            value=(
                "**20%** de éxito: ganas 800–1,500 (25% si tienes multa).\n"
                "**80%** de fallo: multa de 400–900, y además un **10%** de ir a prisión por **1 día**."
            ),
            inline=False,
        )
        embed_economy.add_field(
            name="🥷 &steal <usuario> (cooldown 1h)",
            value=(
                "Roba la billetera completa + 10% del banco de la víctima.\n"
                "**50%** de fallar: dentro de ese fallo, 50% recibes una multa de 10,000 y 50% no pasa nada.\n"
                "Si la víctima tiene **Seguro**: 34% te bloquea el robo, 33% lo mitiga a la mitad, 33% no hace nada.\n"
                "Si tienes un **contrato de sicario** activo contra la víctima, el robo es 100% garantizado e ignora el seguro.\n"
                "**Consecuencias por monto robado**: ≥500,000 → 12h de prisión · ≥1,000,000 → 1 día · ≥100,000,000 → 100 días."
            ),
            inline=False,
        )
        embed_economy.add_field(
            name="⚖️ &sue <usuario>",
            value=(
                "Demanda a quien te robó recientemente (requiere un robo sin resolver). "
                "**50%** ganas: recibes 110% de lo robado, y además un **75%** de que el ladrón vaya a prisión por **1 día**. "
                "**50%** pierdes: no recibes nada."
            ),
            inline=False,
        )
        embed_economy.add_field(
            name="🏢 Negocios y tienda (&shop / &buy)",
            value=(
                f"🛡️ seguro - {self.money(self.prices['seguro'])}: mitiga robos.\n"
                f"🏢 empresa - {self.money(self.prices['empresa'])}: +75% en `&work` permanente.\n"
                f"👥 empleado - {self.money(self.prices['empleado'])}: ingreso pasivo, requiere Empresa, máx 20.\n"
                f"📈 acciones_yt/ms - {self.money(self.prices['acciones_yt'])}/{self.money(self.prices['acciones_ms'])}: dividendos volátiles.\n"
                f"🏛️ mega_yt/ms - {self.money(self.prices['mega_yt'])}/{self.money(self.prices['mega_ms'])}: monopolios de alto ingreso.\n"
                "⚠️ Si no pagas `&salarypay` cada 24h, tus empleados se van solos, y hay un **25%** de ir a prisión por **2 días**."
            ),
            inline=False,
        )
        embed_economy.add_field(
            name="🕰️ Ingresos pasivos (cada 30 min)",
            value=(
                "Empleados, acciones y megacorporaciones generan dinero pendiente automáticamente; cóbralo con `&collect`.\n"
                "Si tienes **Empleados de Robo** (comprados en el Mercado Negro), cada ciclo también pueden generarte dinero extra "
                "robando a otros usuarios al azar, pero hay un **5%** de que te atrapen y vayas a prisión por **5 días**."
            ),
            inline=False,
        )
        embed_economy.add_field(
            name="💰 Multas sin pagar",
            value="Si pasa **1 hora** desde tu primera multa sin pagarla, vas a prisión: **1 día por cada multa acumulada** en ese lapso.",
            inline=False,
        )

        # --- EMBED 2: PRISIÓN ---
        embed_prison = discord.Embed(
            title="⛓️ Manual — Prisión (La Actualización Ilegal)",
            description="Todos estos comandos solo funcionan en el canal designado de la prisión.",
            color=discord.Color.dark_gray(),
        )
        embed_prison.add_field(
            name="🕰️ Horarios (cambian cada hora, ciclo de 4h)",
            value=(
                "🔒 **Celda** → 🍽️ **Comida** → 🔒 **Celda** → 🏃 **Libre** → se repite.\n"
                "Usa `&horario` para ver el horario actual y cuánto falta para el cambio."
            ),
            inline=False,
        )
        embed_prison.add_field(
            name="🔒 Horario de Celda: &cellescape (1 vez por hora)",
            value=(
                "**50%**: avanzas tu túnel un **10%**. Al llegar a **100%**, escapas automáticamente.\n"
                "**50%** restante: dentro de eso, **50%** te atrapan (+24h) y **50%** no pasa nada."
            ),
            inline=False,
        )
        embed_prison.add_field(
            name="🍽️ Horario de Comida: &bribe (1 vez por hora)",
            value=(
                f"Cuesta **{BRIBE_COST:,} 🕵️ (ilegal)**.\n"
                "**50%**: te aceptan el soborno y tu condena restante se reduce a la **mitad**.\n"
                "**50%**: te rechazan, pierdes el dinero y se añaden **24 horas** a tu condena."
            ),
            inline=False,
        )
        embed_prison.add_field(
            name="🏃 Tiempo Libre: &escape y &steal (1 vez por horario)",
            value=(
                "`&escape`: **25%** de fuga exitosa. **75%** te atrapan (+24h).\n"
                "`&steal <usuario>` (solo contra otros presos): roba el 100% del dinero ilegal de la víctima. "
                "**50%** de fallar; dentro de eso, **50%** te atrapan (+12h) y **50%** no pasa nada."
            ),
            inline=False,
        )
        embed_prison.add_field(
            name="📄 &penalty [usuario]",
            value="Muestra cuánto tiempo de condena te queda. Solo el equipo de moderación puede consultar a otros usuarios.",
            inline=False,
        )
        embed_prison.add_field(
            name="🚔 ¿Cómo se llega a prisión?",
            value=(
                "• Perder una demanda (`&sue`) contra ti: 75% de 1 día extra.\n"
                "• Robar ≥500k/1M/100M: 12h / 1 día / 100 días.\n"
                "• No pagar una multa en 1h: 1 día por multa acumulada.\n"
                "• No pagar la nómina: 25% de 2 días.\n"
                "• Ser atrapado en `&crime`: 10% de 1 día.\n"
                "• Empleados de Robo descubiertos: 5% por ciclo, 5 días.\n"
                "• Comprar en el Mercado Negro: 10% de 1 día.\n"
                "• Que un sicario contratado en tu contra falle y te delaten: 35% del intento, 50% de esa mitad → 3 días.\n"
                "• Un moderador te envía con `&sendprison`."
            ),
            inline=False,
        )
        embed_prison.add_field(
            name="🔓 ¿Cómo se sale?",
            value="Cumpliendo la condena, completando el túnel al 100%, un `&escape` exitoso, un `&bribe` exitoso, o que un moderador use `&setfree`.",
            inline=False,
        )

        # --- EMBED 3: MERCADO NEGRO ---
        open_hours = MARKET_OPEN_SECONDS // 3600
        cycle_hours = MARKET_CYCLE_SECONDS // 3600
        embed_market = discord.Embed(
            title="🕶️ Manual — Mercado Negro",
            description=f"Abre cada **{cycle_hours} horas** y permanece abierto por **{open_hours} horas**, luego se cierra. Usa `&blackmarket` para ver el estado y `&buyblackmarket <item>` para comprar.",
            color=discord.Color.dark_purple() if hasattr(discord.Color, "dark_purple") else discord.Color.purple(),
        )
        embed_market.add_field(
            name=f"🥷 ladron - {self.money(LADRON_PRICE)}",
            value="Empleado de Robo: genera ingresos criminales pasivos. Incompatible con tener Empresa. Máx 5.",
            inline=False,
        )
        embed_market.add_field(
            name=f"🔫 sicario - {self.money(HITMAN_COST)}",
            value="Úsalo con `&kill <usuario>`. **65%** de éxito: garantiza tu próximo `&steal` contra esa persona, ignorando su seguro. "
                  "**35%** de fallo: 50% no pasa nada, 50% te descubren como autor intelectual → **3 días** de prisión.",
            inline=False,
        )
        embed_market.add_field(
            name="⚠️ Riesgo de cada compra",
            value=f"**{int(BLACK_MARKET_JAIL_CHANCE * 100)}%** de probabilidad de ir a prisión por **1 día** por cada compra que hagas.",
            inline=False,
        )

        # --- EMBED 4: TODOS LOS COMANDOS ---
        embed_commands = discord.Embed(title="📜 Lista completa de comandos", color=discord.Color.blue())
        embed_commands.add_field(
            name="💳 Economía básica",
            value="`&balance [usuario]` `&daily` `&deposit` `&withdraw` `&give` `&top` `&pay` `&collect` `&collectiontime`",
            inline=False,
        )
        embed_commands.add_field(
            name="🛠️ Ingresos y negocios",
            value="`&work` `&crime` `&crypto [monto]` `&shop` `&buy <item>` `&salarypay`",
            inline=False,
        )
        embed_commands.add_field(
            name="🥷 Interacción criminal",
            value="`&steal <usuario>` `&sue <usuario>`",
            inline=False,
        )
        embed_commands.add_field(
            name="⛓️ Prisión (canal exclusivo)",
            value="`&horario` `&cellescape` `&bribe` `&escape` `&penalty [usuario]` `&blackmarket` `&buyblackmarket <item>` `&kill <usuario>`",
            inline=False,
        )
        if self.is_staff(ctx.author):
            embed_commands.add_field(
                name="⚙️ Herramientas administrativas",
                value="`&addmoney <usuario> <monto>` `&removemoney <usuario> <monto>` `&sendprison <usuario> <tiempo> [razón]` `&setfree <usuario> [razón]`",
                inline=False,
            )

        await ctx.send(embeds=[embed_economy, embed_prison, embed_market, embed_commands])

    @commands.hybrid_command(name="balance", aliases=["bal"], description="Muestra tu dinero actual en la billetera y el banco.")
    @app_commands.describe(target_user="El usuario del cual deseas revisar las finanzas.")
    async def balance_slash(self, ctx: commands.Context, target_user: discord.Member = None):
        user = target_user or ctx.author

        if self.is_in_prison(user):
            if user.id != ctx.author.id and not self.is_staff(ctx.author):
                await ctx.send(embed=self.error_embed("No tienes permiso para ver el balance de otros miembros."), ephemeral=True)
                return
            if not await self.enforce_prison_channel(ctx):
                return
            illegal_data = self.get_illegal_data(user.id)
            embed = discord.Embed(title=f"Balance de {user.display_name} (en prisión)", color=discord.Color.dark_gray())
            embed.set_thumbnail(url=user.display_avatar.url)
            embed.add_field(name="🕵️ Dinero ilegal", value=f"{illegal_data['balance']:,} 🕵️ (ilegal)", inline=True)
            embed.add_field(name="🔫 Sicarios disponibles", value=str(illegal_data["hitmen"]), inline=True)
            embed.set_footer(text="Mientras estás en prisión, tu balance legal (billetera/banco/multas) no se muestra ni se ve afectado.")
            embed.timestamp = datetime.datetime.now(datetime.timezone.utc)
            await ctx.send(embed=embed)
            return

        if not await self.enforce_channel(ctx):
            return
        if target_user and target_user != ctx.author:
            if not self.is_staff(ctx.author):
                await ctx.send(embed=self.error_embed("No tienes permiso para ver el balance de otros miembros."), ephemeral=True)
                return
                
        user_data = self.get_user_data(user.id)
        
        embed = discord.Embed(title=f"Balance de {user.display_name}", color=discord.Color.green())
        embed.set_thumbnail(url=user.display_avatar.url)
        embed.add_field(name="💵 Billetera", value=self.money(user_data['wallet']), inline=True)
        embed.add_field(name="🏦 Banco", value=self.money(user_data['bank']), inline=True)
        embed.add_field(name="⚠️ Multa pendiente", value=self.money(user_data['fine']), inline=True)
        embed.add_field(name="⏳ Por cobrar", value=f"💵 {self.money(user_data['pending_wallet'])} / 🏦 {self.money(user_data['pending_bank'])}", inline=False)
        embed.add_field(name="💰 Total", value=self.money(user_data['wallet'] + user_data['bank']), inline=False)
        embed.timestamp = datetime.datetime.now(datetime.timezone.utc)
        
        await ctx.send(embed=embed)

    @commands.hybrid_command(name="addmoney", description="Añade dinero a la billetera, banco o ambos de un usuario específico.")
    @app_commands.describe(target_user="Usuario beneficiado", amount="Monto de dinero", scope_input="billetera, banco o ambos")
    async def add_money_slash(self, ctx: commands.Context, target_user: discord.Member, amount: int, scope_input: str = "billetera"):
        if not await self.enforce_channel(ctx):
            return
        if not self.is_staff(ctx.author):
            await ctx.send(embed=self.error_embed("No tienes permiso para usar este comando."), ephemeral=True)
            return
        if amount <= 0:
            await ctx.send(embed=self.error_embed("La cantidad debe ser mayor a cero."), ephemeral=True)
            return

        scope = self.parse_balance_scope(scope_input)
        if scope is None:
            await ctx.send(embed=self.error_embed("Scope inválido. Usa: billetera, banco, ambos, 1, 2 o 3."), ephemeral=True)
            return

        wallet_change = amount if scope in ("wallet", "both") else 0
        bank_change = amount if scope in ("bank", "both") else 0
        self.update_balances(target_user.id, wallet_change=wallet_change, bank_change=bank_change)
        await ctx.send(embed=self.success_embed(f"Se han añadido **{self.money(amount)}** a {self.scope_label(scope)} de {target_user.mention}."))

    @commands.hybrid_command(name="removemoney", description="Quita dinero de la billetera, banco o ambos de un usuario específico.")
    @app_commands.describe(target_user="Usuario afectado", amount="Monto de dinero", scope_input="billetera, banco o ambos")
    async def remove_money_slash(self, ctx: commands.Context, target_user: discord.Member, amount: int, scope_input: str = "billetera"):
        if not await self.enforce_channel(ctx):
            return
        if not self.is_staff(ctx.author):
            await ctx.send(embed=self.error_embed("No tienes permiso para usar este comando."), ephemeral=True)
            return
        if amount <= 0:
            await ctx.send(embed=self.error_embed("La cantidad debe ser mayor a cero."), ephemeral=True)
            return

        scope = self.parse_balance_scope(scope_input)
        if scope is None:
            await ctx.send(embed=self.error_embed("Scope inválido. Usa: billetera, banco, ambos, 1, 2 o 3."), ephemeral=True)
            return

        wallet_change = -amount if scope in ("wallet", "both") else 0
        bank_change = -amount if scope in ("bank", "both") else 0
        self.update_balances(target_user.id, wallet_change=wallet_change, bank_change=bank_change)
        await ctx.send(embed=self.success_embed(f"Se han retirado **{self.money(amount)}** de {self.scope_label(scope)} de {target_user.mention}."))

    @commands.hybrid_command(name="shop", description="Muestra el catálogo de la tienda de economía.")
    async def shop_prefix(self, ctx: commands.Context):
        if not await self.enforce_channel(ctx):
            return
        embed = discord.Embed(title="🏪 Tienda del Servidor", description="Utiliza `&buy <item>` o `/buy` para adquirir mejoras financieras.", color=discord.Color.gold())
        embed.add_field(name=f"🛡️ seguro - {self.money(self.prices['seguro'])}", value="Mitiga pérdidas de dinero si sufres un robo con éxito.", inline=False)
        embed.add_field(name=f"🏢 empresa - {self.money(self.prices['empresa'])}", value="Añade permanentemente un **75% más de ingresos** al usar `&work`.", inline=False)
        embed.add_field(name=f"👥 empleado - {self.money(self.prices['empleado'])}", value="Genera ingresos pasivos recurrentes. Requiere Empresa. Máx 20.", inline=False)
        embed.add_field(name=f"📈 acciones_yt / acciones_ms - {self.money(self.prices['acciones_yt'])} / {self.money(self.prices['acciones_ms'])}", value="Generan dividendos pasivos volátiles inyectados en el banco (pueden dar pérdidas).", inline=False)
        embed.add_field(name=f"🏛️ mega_yt / mega_ms - {self.money(self.prices['mega_yt'])} / {self.money(self.prices['mega_ms'])}", value="Adquiere monopolios absolutos para recibir masivas inyecciones de dinero.", inline=False)
        embed.add_field(name="🕶️ ¿Buscas algo más turbio?", value="El **Mercado Negro** vende Empleados de Robo (ladrón) y Sicarios. Usa `&blackmarket` para verlo (canal de la prisión, con horario limitado).", inline=False)
        await ctx.send(embed=embed)

    @commands.hybrid_command(name="buy", description="Compra un artículo o inversión de la tienda.")
    @app_commands.describe(item_name="El nombre exacto del artículo que deseas comprar.")
    async def buy_prefix(self, ctx: commands.Context, item_name: str):
        if not await self.enforce_channel(ctx):
            return
        user_id = ctx.author.id
        data = self.get_user_data(user_id)
        item_key = item_name.lower()
        
        if item_key not in self.prices:
            await ctx.send(embed=self.error_embed("Ese artículo no se encuentra disponible en la tienda. Revisa `&shop`."))
            return
            
        cost = self.prices[item_key]
        if data["wallet"] < cost:
            await ctx.send(embed=self.error_embed(f"Fondos insuficientes en tu billetera. Necesitas **{self.money(cost)}**."))
            return

        if item_key == "seguro":
            if data["insurance"] == 1:
                await ctx.send(embed=self.error_embed("Ya tienes contratado un Seguro de Banco."))
                return
            self.update_asset(user_id, "insurance", 1)
            
        elif item_key == "empresa":
            if data["company"] == 1:
                await ctx.send(embed=self.error_embed("Ya eres propietario de una Empresa."))
                return
            if data["robbery_employees"] > 0:
                await ctx.send(embed=self.error_embed("No puedes fundar una empresa legal si tienes contratados Empleados de Robo."))
                return
            self.update_asset(user_id, "company", 1)
            
        elif item_key == "empleado":
            if data["company"] == 0:
                await ctx.send(embed=self.error_embed("Necesitas comprar primero una Empresa legal para poder contratar empleados."))
                return
            if data["employees"] >= 20:
                await ctx.send(embed=self.error_embed("Has alcanzado el límite máximo permitido de 20 empleados."))
                return
            self.update_asset(user_id, "employees", data["employees"] + 1)
            self.update_asset(user_id, "last_salary_pay", datetime.datetime.now().isoformat())
            self.update_asset(user_id, "salary_penalty_processed", 0)
            
        elif item_key in ["acciones_yt", "acciones_ms"]:
            stock_id = item_key.replace("acciones_", "")
            current_stocks = data["stocks"]
            current_stocks[stock_id] = current_stocks.get(stock_id, 0) + 1
            self.update_asset(user_id, "stocks", json.dumps(current_stocks))
            
        elif item_key in ["mega_yt", "mega_ms"]:
            mega_id = item_key.replace("mega_", "")
            current_megas = data["mega_companies"]
            if mega_id in current_megas:
                await ctx.send(embed=self.error_embed(f"Ya eres el dueño de la multinacional {mega_id.upper()}."))
                return
            current_megas.append(mega_id)
            self.update_asset(user_id, "mega_companies", json.dumps(current_megas))

        self.update_balances(user_id, wallet_change=-cost, bank_change=0)
        await ctx.send(embed=self.success_embed(f"¡Has comprado **{item_name}** con éxito por **{self.money(cost)}**!", title="Compra realizada"))

    @commands.hybrid_command(name="salarypay", description="Paga los salarios de tus empleados contratados.")
    async def salary_pay_prefix(self, ctx: commands.Context):
        if not await self.enforce_channel(ctx):
            return
        user_id = ctx.author.id
        data = self.get_user_data(user_id)
        
        if data["employees"] == 0 and data["robbery_employees"] == 0:
            await ctx.send(embed=self.error_embed("No posees empleados contratados que requieran nómina."))
            return
            
        cost_per_employee = 800 if data["employees"] > 0 else 1200
        total_count = data["employees"] if data["employees"] > 0 else data["robbery_employees"]
        total_salary_cost = total_count * cost_per_employee
        
        if data["wallet"] < total_salary_cost:
            await ctx.send(embed=self.error_embed(f"Dinero insuficiente en tu billetera. Necesitas **{self.money(total_salary_cost)}**."))
            return
            
        self.update_balances(user_id, wallet_change=-total_salary_cost, bank_change=0, reason="Nómina", details=f"Pagaste {self.money(total_salary_cost)} de salarios a tus empleados.")
        self.update_asset(user_id, "last_salary_pay", datetime.datetime.now().isoformat())
        self.update_asset(user_id, "salary_penalty_processed", 0)
        await ctx.send(embed=self.success_embed(f"Nómina pagada por **{self.money(total_salary_cost)}**. Contrato renovado por 24 horas.", title="Nómina pagada"))

    @commands.hybrid_command(name="work", description="Trabaja para conseguir dinero legal (o ilegal, si estás en prisión).")
    @commands.cooldown(1, 30, commands.BucketType.user)
    async def work_prefix(self, ctx: commands.Context):
        if self.is_in_prison(ctx.author):
            if not await self.enforce_prison_channel(ctx):
                return
            illegal_earnings = random.randint(80, 220)
            self.update_illegal_balance(ctx.author.id, illegal_earnings)
            await ctx.send(embed=self.success_embed(
                f"Hiciste trabajos forzados dentro de la prisión y conseguiste **{illegal_earnings:,} 🕵️ (ilegal)**.",
                title="Trabajo en prisión completado"
            ))
            return

        if not await self.enforce_channel(ctx):
            return
        user_id = ctx.author.id
        data = self.get_user_data(user_id)
        base_earnings = random.randint(100, 300)
        
        if data["company"] == 1:
            base_earnings = int(base_earnings * 1.75)

        if data["fine"] > 0:
            base_earnings = int(base_earnings * 0.25)
            
        self.update_balances(user_id, wallet_change=base_earnings, bank_change=0, reason="Trabajo", details=f"Ganaste {self.money(base_earnings)} trabajando.")
        await ctx.send(embed=self.success_embed(f"¡Trabajaste duro y ganaste **{self.money(base_earnings)}**!", title="Turno completado"))

    @commands.hybrid_command(name="crime", description="Comete un crimen ilegal para conseguir dinero rápido.")
    @commands.cooldown(1, 180, commands.BucketType.user)
    async def crime_prefix(self, ctx: commands.Context):
        if not await self.enforce_channel(ctx):
            return
        user_id = ctx.author.id
        data = self.get_user_data(user_id)
        
        if random.random() < 0.20:
            payout = random.randint(800, 1500)
            if data["fine"] > 0:
                payout = int(payout * 0.25)
            self.update_balances(user_id, wallet_change=payout, bank_change=0, reason="Crimen", details=f"Tu crimen salió bien y ganaste {self.money(payout)}.")
            await ctx.send(embed=self.success_embed(f"¡El atraco fue un éxito! Obtuviste **{self.money(payout)}**.", title="Golpe exitoso"))
        else:
            penalty = random.randint(400, 900)
            self.apply_fine(user_id, penalty)
            await ctx.send(embed=self.error_embed(f"¡Te atraparon cometiendo el crimen! Te impusieron una multa de **{self.money(penalty)}**. Usa `&pay` para pagarla.", title="Atrapado"))

            if random.random() < 0.10:
                jail_seconds = 24 * 3600
                await self.jail_member(ctx.author, ctx.guild, jail_seconds, "Te atraparon cometiendo un crimen y decidieron encarcelarte.")
                await ctx.send(embed=self.error_embed(f"Además, fuiste enviado a prisión por **{self.format_duration(jail_seconds)}**.", title="Sentencia adicional"))

    @commands.hybrid_command(name="steal", description="Intenta desvalijar la cartera de alguien.")
    @commands.cooldown(1, 3600, commands.BucketType.user)
    @app_commands.describe(target_member="El usuario al que intentas robar.")
    async def steal_prefix(self, ctx: commands.Context, target_member: discord.Member):
        if self.is_in_prison(ctx.author):
            await self.handle_prison_steal(ctx, target_member)
            return

        if not await self.enforce_channel(ctx):
            return
        if target_member == ctx.author:
            await ctx.send(embed=self.error_embed("No puedes robarte a ti mismo."))
            return
            
        thief_id = ctx.author.id
        victim_id = target_member.id
        victim_data = self.get_user_data(victim_id)
        
        total_stealable = victim_data["wallet"] + int(victim_data["bank"] * 0.10)
        if total_stealable <= 100:
            await ctx.send(embed=self.error_embed("Este objetivo no tiene suficiente dinero para valer la pena."))
            return

        has_contract = self.consume_contract(thief_id, victim_id)

        if not has_contract and random.random() < 0.50:
            if random.random() < 0.50:
                self.apply_fine(thief_id, 10000)
                await ctx.send(embed=self.error_embed(f"Intentaste abrir la mochila de {target_member.name} pero fallaste y además te impusieron una multa de **{self.money(10000)}**. Usa `&pay` para saldarla.", title="Robo fallido"))
            else:
                await ctx.send(embed=self.error_embed(f"Intentaste abrir la mochila de {target_member.name} pero fallaste y escapaste corriendo.", title="Robo fallido"))
            return
            
        wallet_stolen = victim_data["wallet"]
        bank_stolen = int(victim_data["bank"] * 0.10)
        final_stolen_amount = wallet_stolen + bank_stolen
        
        if not has_contract and victim_data["insurance"] == 1:
            insurance_roll = random.random()
            if insurance_roll < 0.34:
                await ctx.send(embed=self.error_embed(f"Intentaste robar a {target_member.name}, pero su **Seguro de Banco** bloqueó todo acceso a sus fondos.", title="Robo bloqueado"))
                return
            elif insurance_roll < 0.67:
                final_stolen_amount = int(final_stolen_amount * 0.5)
                wallet_stolen = int(wallet_stolen * 0.5)
                bank_stolen = int(bank_stolen * 0.5)
                await ctx.send(embed=self.error_embed(f"El **Seguro de Banco** de {target_member.name} mitigó parcialmente el impacto.", title="Robo mitigado"))

        self.update_balances(victim_id, wallet_change=-wallet_stolen, bank_change=-bank_stolen, reason="Robo", details=f"Se le retiraron fondos tras un intento de robo.", target_user_id=thief_id)
        self.update_balances(thief_id, wallet_change=final_stolen_amount, bank_change=0, reason="Robo", details=f"Robaste {self.money(final_stolen_amount)} a {target_member.name}.", actor_id=thief_id, target_user_id=victim_id)
        
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("INSERT INTO recent_thefts (thief_id, victim_id, amount_stolen, timestamp, resolved) VALUES (?, ?, ?, ?, 0)",
                       (thief_id, victim_id, final_stolen_amount, datetime.datetime.now().isoformat()))
        conn.commit()
        conn.close()

        contract_note = " Tu sicario garantizó el golpe." if has_contract else ""
        await ctx.send(embed=self.success_embed(f"¡Robo completado con éxito! Le quitaste **{self.money(final_stolen_amount)}** a {target_member.mention}.{contract_note}", title="Robo exitoso"))

        jail_seconds = None
        if final_stolen_amount >= 100_000_000:
            jail_seconds = 100 * 86400
        elif final_stolen_amount >= 1_000_000:
            jail_seconds = 1 * 86400
        elif final_stolen_amount >= 500_000:
            jail_seconds = 12 * 3600
        if jail_seconds:
            await self.jail_member(
                ctx.author, ctx.guild, jail_seconds,
                f"Robaste {self.money(final_stolen_amount)}, una cantidad demasiado grande para pasar desapercibida."
            )
            await ctx.send(embed=self.error_embed(
                f"El robo fue tan grande que las autoridades te encontraron. Fuiste enviado a prisión por **{self.format_duration(jail_seconds)}**.",
                title="Autoridades alertadas"
            ))

    @commands.hybrid_command(name="sue", description="Demanda legalmente a un usuario que te robó dinero hace poco.")
    @app_commands.describe(target_thief="El presunto ladrón al que vas a demandar.")
    async def sue_prefix(self, ctx: commands.Context, target_thief: discord.Member):
        if not await self.enforce_channel(ctx):
            return
        victim_id = ctx.author.id
        thief_id = target_thief.id
        
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT amount_stolen, rowid FROM recent_thefts WHERE thief_id = ? AND victim_id = ? AND resolved = 0 ORDER BY timestamp DESC LIMIT 1", (thief_id, victim_id))
        row = cursor.fetchone()
        
        if not row:
            conn.close()
            await ctx.send(embed=self.error_embed(f"No tienes registros de robos recientes sin resolver contra {target_thief.name}."))
            return
            
        stolen_amount, record_rowid = row
        cursor.execute("UPDATE recent_thefts SET resolved = 1 WHERE rowid = ?", (record_rowid,))
        conn.commit()
        conn.close()
        
        if random.random() < 0.50:
            compensation = int(stolen_amount * 1.10)
            self.update_balances(thief_id, wallet_change=-compensation, bank_change=0, reason="Demanda", details=f"Pagaste {self.money(compensation)} por una demanda judicial.")
            self.update_balances(victim_id, wallet_change=compensation, bank_change=0, reason="Demanda", details=f"Recibiste {self.money(compensation)} por una demanda judicial.")
            await ctx.send(embed=self.success_embed(f"¡Anulaste las defensas de {target_thief.mention} en la corte! Recibiste **{self.money(compensation)}** por el robo y daños.", title="Demanda ganada"))

            if random.random() < 0.75:
                jail_seconds = 24 * 3600
                await self.jail_member(target_thief, ctx.guild, jail_seconds, f"Perdiste una demanda judicial por robar a {ctx.author.display_name}.")
                await ctx.send(embed=self.error_embed(f"Además, el juez ordenó que {target_thief.mention} fuera enviado a prisión por **{self.format_duration(jail_seconds)}**.", title="Sentencia adicional"))
        else:
            await ctx.send(embed=self.error_embed(f"Perdiste el juicio contra {target_thief.name} por falta de pruebas.", title="Demanda perdida"))

    @commands.hybrid_command(name="give", description="Transfiere una cantidad de efectivo a otro miembro.")
    @app_commands.describe(target_member="Usuario que recibe el dinero", amount="Monto a dar")
    async def give_prefix(self, ctx: commands.Context, target_member: discord.Member, amount: int):
        if not await self.enforce_channel(ctx):
            return
        if target_member == ctx.author:
            await ctx.send(embed=self.error_embed("No puedes transferir fondos a ti mismo."))
            return
        if amount <= 0:
            await ctx.send(embed=self.error_embed("La cantidad debe ser superior a cero."))
            return
            
        sender_data = self.get_user_data(ctx.author.id)
        if sender_data["wallet"] < amount:
            await ctx.send(embed=self.error_embed("No cuentas con suficiente efectivo disponible en tu billetera."))
            return
            
        self.update_balances(ctx.author.id, wallet_change=-amount, bank_change=0, reason="Transferencia", details=f"Transferiste {self.money(amount)} a {target_member.name}.", actor_id=ctx.author.id, target_user_id=target_member.id)
        self.update_balances(target_member.id, wallet_change=amount, bank_change=0, reason="Transferencia", details=f"Recibiste {self.money(amount)} de {ctx.author.name}.", actor_id=ctx.author.id, target_user_id=target_member.id)
        await ctx.send(embed=self.success_embed(f"Has transferido **{self.money(amount)}** a la billetera de {target_member.mention}.", title="Transferencia completada"))

    @commands.hybrid_command(name="crypto", description="Invierte en un broker simulado de criptomonedas.")
    @app_commands.describe(investment="Cantidad de efectivo que quieres arriesgar.")
    async def crypto_prefix(self, ctx: commands.Context, investment: int = 500):
        if not await self.enforce_channel(ctx):
            return
        user_id = ctx.author.id
        user_data = self.get_user_data(user_id)
        
        if investment <= 0:
            await ctx.send(embed=self.error_embed("La cantidad a invertir debe ser mayor a cero."))
            return
        if user_data["wallet"] < investment:
            await ctx.send(embed=self.error_embed("No tienes suficiente dinero en tu billetera."))
            return
            
        trend_positive = random.choice([True, False])
        trend_text = "📈 POSITIVA" if trend_positive else "📉 NEGATIVA"
        
        embed = discord.Embed(title="🛸 Broker de Criptomonedas", description=f"Análisis en tiempo real:\nTendencia estimada: **{trend_text}**\nMonto en juego: **{self.money(investment)}**\n\n¿Qué acción deseas ejecutar?", color=discord.Color.purple())
        
        view = CryptoView(self, user_id, investment, trend_positive)
        await ctx.send(embed=embed, view=view)

    @commands.hybrid_command(name="collect", description="Cobra el dinero pasivo acumulado por tus negocios e inversiones.")
    async def collect_prefix(self, ctx: commands.Context):
        if not await self.enforce_channel(ctx):
            return
        user_id = ctx.author.id
        wallet_collected, bank_collected = self.collect_pending_income(user_id)

        if wallet_collected == 0 and bank_collected == 0:
            embed = discord.Embed(
                title="💤 Nada por cobrar",
                description="No tienes dinero pasivo acumulado por cobrar en este momento.",
                color=discord.Color.blurple()
            )
            await ctx.send(embed=embed)
            return

        embed = self.success_embed("Tu dinero pasivo ha sido transferido a tus cuentas.", title="Cobro completado")
        embed.add_field(name="💵 Billetera", value=self.money(wallet_collected), inline=True)
        embed.add_field(name="🏦 Banco", value=self.money(bank_collected), inline=True)
        embed.timestamp = datetime.datetime.now(datetime.timezone.utc)
        await ctx.send(embed=embed)

    @commands.hybrid_command(name="collectiontime", description="Muestra cuándo y cuánto generarán tus activos AFK.")
    async def collection_time_prefix(self, ctx: commands.Context):
        if not await self.enforce_channel(ctx):
            return
        user_id = ctx.author.id
        data = self.get_user_data(user_id)

        if not data["employees"] and not data["robbery_employees"] and not data["mega_companies"] and not data["stocks"]:
            embed = discord.Embed(
                title="🧊 Sin activos AFK",
                description="No tienes activos que generen dinero AFK en este momento.",
                color=discord.Color.blurple()
            )
            await ctx.send(embed=embed)
            return

        if self.passive_last_run is None:
            next_cycle_seconds = self.passive_interval_seconds
        else:
            next_cycle_at = self.passive_last_run + datetime.timedelta(seconds=self.passive_interval_seconds)
            next_cycle_seconds = max(0, int((next_cycle_at - datetime.datetime.now(datetime.timezone.utc)).total_seconds()))

        summary_lines = []
        if data["employees"] > 0:
            min_income = data["employees"] * 150
            max_income = data["employees"] * 400
            summary_lines.append(f"👥 Empleados legales: entre **{self.money(min_income)}** y **{self.money(max_income)}** por ciclo")

        if data["robbery_employees"] > 0:
            summary_lines.append(f"🥷 Empleados de robo: ingresos variables, con posibilidad de generar efectivo adicional por ciclo")

        if data["mega_companies"]:
            mega_total = len(data["mega_companies"])
            summary_lines.append(f"🏛️ Megacorporaciones: **{self.money(mega_total * 5000)}** a **{self.money(mega_total * 15000)}** por ciclo")

        if data["stocks"]:
            for stock_name, count in data["stocks"].items():
                if count > 0:
                    stock_label = "YouTube" if stock_name == "yt" else "Microsoft" if stock_name == "ms" else stock_name.upper()
                    summary_lines.append(f"📈 {stock_label}: **{count}** unidad(es) con rendimiento variable por ciclo")

        embed = discord.Embed(
            title="⏳ Estado de ingresos AFK",
            description="Aquí tienes un resumen de cuándo y cuánto generarán tus activos pasivos.",
            color=discord.Color.gold()
        )
        embed.add_field(name="⏰ Próxima generación", value=self.format_duration(next_cycle_seconds), inline=False)
        embed.add_field(name="📦 Activos que generan dinero", value="\n".join(summary_lines), inline=False)
        embed.add_field(name="💸 Pendiente por cobrar", value=f"💵 {self.money(data['pending_wallet'])} / 🏦 {self.money(data['pending_bank'])}", inline=False)
        embed.timestamp = datetime.datetime.now(datetime.timezone.utc)
        await ctx.send(embed=embed)

    @commands.hybrid_command(name="pay", description="Paga parte o la totalidad de tu multa pendiente.")
    @app_commands.describe(amount_input="Cantidad a pagar o escribe 'all'.")
    async def pay_prefix(self, ctx: commands.Context, amount_input: str = "all"):
        if not await self.enforce_channel(ctx):
            return
        user_id = ctx.author.id
        data = self.get_user_data(user_id)
        if data["fine"] <= 0:
            await ctx.send(embed=self.success_embed("No tienes multas pendientes."))
            return

        if amount_input.lower() == "all":
            amount = data["fine"]
        else:
            try:
                amount = int(amount_input)
            except ValueError:
                await ctx.send(embed=self.error_embed("Cantidad inválida."))
                return

        if amount <= 0:
            await ctx.send(embed=self.error_embed("La cantidad debe ser mayor a cero."))
            return

        paid = self.pay_fine(user_id, amount)
        if paid <= 0:
            await ctx.send(embed=self.error_embed("No tienes fondos suficientes para pagar esa cantidad."))
            return

        remaining = max(0, data["fine"] - paid)
        embed = self.success_embed(f"Pagaste **{self.money(paid)}** de tu multa pendiente.", title="Pago de multa")
        embed.add_field(name="Restante", value=self.money(remaining), inline=False)
        await ctx.send(embed=embed)

    @commands.hybrid_command(name="daily", description="Reclama tu recompensa financiera diaria.")
    async def daily_prefix(self, ctx: commands.Context):
        if not await self.enforce_channel(ctx):
            return
        user_id = ctx.author.id
        user_data = self.get_user_data(user_id)
        current_date_str = datetime.date.today().isoformat()
        
        if user_data["last_daily"] == current_date_str:
            await ctx.send(embed=self.error_embed("Ya has reclamado tu recompensa diaria hoy."))
            return
            
        daily_reward = 500
        self.update_balances(user_id, wallet_change=daily_reward, bank_change=0, reason="Daily", details=f"Reclamaste el bono diario de {self.money(daily_reward)}.")
        self.update_asset(user_id, "last_daily", current_date_str)
        await ctx.send(embed=self.success_embed(f"¡Has reclamado tus **{self.money(daily_reward)}** del bono diario!", title="Bono diario reclamado"))

    @commands.hybrid_command(name="deposit", aliases=["dep"], description="Deposita dinero de tu billetera al banco.")
    @app_commands.describe(amount_input="Cantidad de dinero numérico o escribe 'all'.")
    async def deposit_prefix(self, ctx: commands.Context, amount_input: str):
        if not await self.enforce_channel(ctx):
            return
        user_id = ctx.author.id
        user_data = self.get_user_data(user_id)
        wallet_balance = user_data["wallet"]
        
        if amount_input.lower() == "all":
            amount = wallet_balance
        else:
            try:
                amount = int(amount_input)
            except ValueError:
                await ctx.send(embed=self.error_embed("Cantidad inválida."))
                return
                
        if amount <= 0 or amount > wallet_balance:
            await ctx.send(embed=self.error_embed("Cantidad inválida o fondos insuficientes."))
            return
            
        self.update_balances(user_id, wallet_change=-amount, bank_change=amount, reason="Depósito", details=f"Depositaste {self.money(amount)} en el banco.")
        await ctx.send(embed=self.success_embed(f"Depositados **{self.money(amount)}** en el banco.", title="Depósito realizado"))

    @commands.hybrid_command(name="withdraw", aliases=["with"], description="Retira dinero de tu cuenta bancaria a tu billetera.")
    @app_commands.describe(amount_input="Cantidad de dinero numérico o escribe 'all'.")
    async def withdraw_prefix(self, ctx: commands.Context, amount_input: str):
        if not await self.enforce_channel(ctx):
            return
        user_id = ctx.author.id
        user_data = self.get_user_data(user_id)
        bank_balance = user_data["bank"]
        
        if amount_input.lower() == "all":
            amount = bank_balance
        else:
            try:
                amount = int(amount_input)
            except ValueError:
                await ctx.send(embed=self.error_embed("Cantidad inválida."))
                return
                
        if amount <= 0 or amount > bank_balance:
            await ctx.send(embed=self.error_embed("Cantidad inválida o fondos insuficientes."))
            return
            
        self.update_balances(user_id, wallet_change=amount, bank_change=-amount, reason="Retiro", details=f"Retiraste {self.money(amount)} del banco.")
        await ctx.send(embed=self.success_embed(f"Retirados **{self.money(amount)}** de tu cuenta bancaria.", title="Retiro realizado"))

    # --- TOP COMMAND (LIMIT 100) ---
    @commands.hybrid_command(name="top", aliases=["leaderboard", "ricos"], description="Muestra la lista de los usuarios con más dinero.")
    async def top_rich(self, ctx: commands.Context):
        if not await self.enforce_channel(ctx):
            return
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("""
            SELECT user_id, wallet, bank, (wallet + bank) AS total_money 
            FROM users 
            ORDER BY total_money DESC 
            LIMIT 100
        """)
        top_users = cursor.fetchall()
        conn.close()

        if not top_users:
            await ctx.send(embed=self.error_embed("La base de datos de economía está vacía actualmente.", title="Sin datos"))
            return

        author_rank = "Fuera del Top 100"
        author_total = 0
        
        for index, row in enumerate(top_users):
            u_id, wallet, bank, total = row
            if u_id == ctx.author.id:
                author_rank = f"#{index + 1}"
                author_total = total
                break
        
        if author_rank == "Fuera del Top 100":
            author_data = self.get_user_data(ctx.author.id)
            author_total = author_data["wallet"] + author_data["bank"]

        embed = discord.Embed(
            title="🏆 Tablón de Clasificación: Los Más Ricos",
            description="Aquí se muestran los usuarios con las mayores fortunas del servidor.\n\u200b",
            color=discord.Color.gold()
        )
        
        leaderboard_text = ""
        for index, row in enumerate(top_users[:10]):
            u_id, wallet, bank, total = row
            
            member = ctx.guild.get_member(u_id)
            if member:
                name = member.display_name
            else:
                name = f"Usuario Antiguo ({u_id})"
            
            if index == 0:
                medal = "🥇"
            elif index == 1:
                medal = "🥈"
            elif index == 2:
                medal = "🥉"
            else:
                medal = f"**#{index + 1}**"
                
            leaderboard_text += f"{medal} **{name}** — {self.money(total)} *(💵 {self.money(wallet)} / 🏦 {self.money(bank)})*\n"

        embed.description += leaderboard_text
        embed.add_field(name="📍 Tu posición", value=f"{author_rank} — Fortuna actual: {self.money(author_total)}", inline=False)

        embed.set_footer(
            text=f"Tu posición: {author_rank}",
            icon_url=ctx.author.display_avatar.url
        )
        embed.timestamp = datetime.datetime.now(datetime.timezone.utc)

        await ctx.send(embed=embed)

    # =========================================================
    # --- SISTEMA DE PRISIÓN (fusionado desde el antiguo cogs/prison.py) ---
    # =========================================================

    # --- BASE DE DATOS ---

    def initialize_prison_database(self):
        db_path = Path(self.prison_db_path)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(db_path))
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS prisoners (
                user_id INTEGER PRIMARY KEY,
                guild_id INTEGER NOT NULL,
                release_at TEXT NOT NULL,
                tunnel_progress INTEGER NOT NULL DEFAULT 0,
                jailed_at TEXT NOT NULL
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS illegal_money (
                user_id INTEGER PRIMARY KEY,
                balance INTEGER NOT NULL DEFAULT 0,
                hitmen INTEGER NOT NULL DEFAULT 0
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS hitman_contracts (
                thief_id INTEGER NOT NULL,
                victim_id INTEGER NOT NULL,
                guild_id INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY (thief_id, victim_id)
            )
        """)
        conn.commit()
        conn.close()

    def _prison_connect(self):
        return sqlite3.connect(self.prison_db_path)

    # --- PRISIONEROS ---

    def get_prisoner(self, user_id):
        conn = self._prison_connect()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT user_id, guild_id, release_at, tunnel_progress, jailed_at FROM prisoners WHERE user_id = ?",
            (user_id,)
        )
        row = cursor.fetchone()
        conn.close()
        return row

    def is_in_prison(self, member: discord.Member) -> bool:
        return any(role.id == PRISON_ROLE_ID for role in member.roles)

    def _upsert_prisoner(self, user_id, guild_id, release_at_iso, tunnel_progress=None, jailed_at_iso=None):
        conn = self._prison_connect()
        cursor = conn.cursor()
        existing = cursor.execute(
            "SELECT tunnel_progress, jailed_at FROM prisoners WHERE user_id = ?", (user_id,)
        ).fetchone()
        if existing:
            tp = tunnel_progress if tunnel_progress is not None else existing[0]
            ja = jailed_at_iso if jailed_at_iso is not None else existing[1]
            cursor.execute(
                "UPDATE prisoners SET guild_id = ?, release_at = ?, tunnel_progress = ?, jailed_at = ? WHERE user_id = ?",
                (guild_id, release_at_iso, tp, ja, user_id)
            )
        else:
            cursor.execute(
                "INSERT INTO prisoners (user_id, guild_id, release_at, tunnel_progress, jailed_at) VALUES (?, ?, ?, ?, ?)",
                (user_id, guild_id, release_at_iso, tunnel_progress or 0,
                 jailed_at_iso or datetime.datetime.now(datetime.timezone.utc).isoformat())
            )
        conn.commit()
        conn.close()

    def _remove_prisoner_row(self, user_id):
        conn = self._prison_connect()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM prisoners WHERE user_id = ?", (user_id,))
        conn.commit()
        conn.close()

    def set_tunnel_progress(self, user_id, value):
        conn = self._prison_connect()
        cursor = conn.cursor()
        cursor.execute("UPDATE prisoners SET tunnel_progress = ? WHERE user_id = ?", (value, user_id))
        conn.commit()
        conn.close()

    # --- DINERO ILEGAL ---

    def get_illegal_data(self, user_id):
        conn = self._prison_connect()
        cursor = conn.cursor()
        cursor.execute("SELECT balance, hitmen FROM illegal_money WHERE user_id = ?", (user_id,))
        row = cursor.fetchone()
        if not row:
            cursor.execute("INSERT INTO illegal_money (user_id, balance, hitmen) VALUES (?, 0, 0)", (user_id,))
            conn.commit()
            conn.close()
            return {"balance": 0, "hitmen": 0}
        conn.close()
        return {"balance": row[0], "hitmen": row[1]}

    def update_illegal_balance(self, user_id, change):
        self.get_illegal_data(user_id)
        conn = self._prison_connect()
        cursor = conn.cursor()
        cursor.execute("UPDATE illegal_money SET balance = max(0, balance + ?) WHERE user_id = ?", (change, user_id))
        conn.commit()
        conn.close()

    def add_hitman(self, user_id, count=1):
        self.get_illegal_data(user_id)
        conn = self._prison_connect()
        cursor = conn.cursor()
        cursor.execute("UPDATE illegal_money SET hitmen = hitmen + ? WHERE user_id = ?", (count, user_id))
        conn.commit()
        conn.close()

    def consume_hitman(self, user_id) -> bool:
        data = self.get_illegal_data(user_id)
        if data["hitmen"] <= 0:
            return False
        conn = self._prison_connect()
        cursor = conn.cursor()
        cursor.execute("UPDATE illegal_money SET hitmen = hitmen - 1 WHERE user_id = ?", (user_id,))
        conn.commit()
        conn.close()
        return True

    # --- CONTRATOS DE SICARIO ---

    def create_contract(self, thief_id, victim_id, guild_id):
        conn = self._prison_connect()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT OR REPLACE INTO hitman_contracts (thief_id, victim_id, guild_id, created_at) VALUES (?, ?, ?, ?)",
            (thief_id, victim_id, guild_id, datetime.datetime.now(datetime.timezone.utc).isoformat())
        )
        conn.commit()
        conn.close()

    def consume_contract(self, thief_id, victim_id) -> bool:
        conn = self._prison_connect()
        cursor = conn.cursor()
        cursor.execute("SELECT 1 FROM hitman_contracts WHERE thief_id = ? AND victim_id = ?", (thief_id, victim_id))
        exists = cursor.fetchone() is not None
        if exists:
            cursor.execute("DELETE FROM hitman_contracts WHERE thief_id = ? AND victim_id = ?", (thief_id, victim_id))
            conn.commit()
        conn.close()
        return exists

    def has_staff_role(self, member: discord.Member) -> bool:
        return any(role.id in STAFF_ROLE_IDS for role in member.roles)

    async def enforce_prison_channel(self, ctx) -> bool:
        if ctx.channel.id == PRISON_CHANNEL_ID:
            return True
        channel = ctx.guild.get_channel(PRISON_CHANNEL_ID) if ctx.guild else None
        location = channel.mention if channel else "el canal designado de la prisión"
        await ctx.send(embed=self.error_embed(f"Este comando solo se puede usar en {location}."), ephemeral=True)
        return False

    async def log_prison_action(self, guild, action, target, moderator, reason, duration_text=None):
        channel = guild.get_channel(LOG_CHANNEL_ID)
        if not channel:
            return
        embed = discord.Embed(title=f"⛓️ Registro de Prisión: {action}", color=discord.Color.dark_red())
        embed.add_field(name="Usuario", value=f"{target.mention} ({target.id})", inline=False)
        embed.add_field(name="Responsable", value=f"{moderator.mention} ({moderator.id})", inline=False)
        embed.add_field(name="Razón", value=reason or "No especificada", inline=False)
        if duration_text:
            embed.add_field(name="Duración", value=duration_text, inline=False)
        embed.timestamp = datetime.datetime.now(datetime.timezone.utc)
        try:
            await channel.send(embed=embed)
        except discord.HTTPException:
            pass

    # --- SENTENCIAS ---

    async def jail_member(self, member: discord.Member, guild: discord.Guild, duration_seconds: int, reason: str, notify: bool = True):
        now = datetime.datetime.now(datetime.timezone.utc)
        existing = self.get_prisoner(member.id)
        if existing:
            current_release = datetime.datetime.fromisoformat(existing[2])
            base = max(current_release, now)
            new_release = base + datetime.timedelta(seconds=duration_seconds)
            self._upsert_prisoner(member.id, guild.id, new_release.isoformat())
        else:
            new_release = now + datetime.timedelta(seconds=duration_seconds)
            self._upsert_prisoner(member.id, guild.id, new_release.isoformat(), tunnel_progress=0, jailed_at_iso=now.isoformat())
            free_role = guild.get_role(FREE_ROLE_ID)
            prison_role = guild.get_role(PRISON_ROLE_ID)
            try:
                if free_role and free_role in member.roles:
                    await member.remove_roles(free_role, reason="Encarcelado")
                if prison_role and prison_role not in member.roles:
                    await member.add_roles(prison_role, reason="Encarcelado")
            except (discord.Forbidden, discord.HTTPException):
                pass

        if notify:
            try:
                embed = discord.Embed(
                    title=f"{error_emoji(self.bot)} Has ido a prisión",
                    description=(
                        f"{reason}\n\n"
                        f"Tiempo añadido: **{format_prison_duration(duration_seconds)}**\n"
                        f"Sales: {discord.utils.format_dt(new_release, style='R')}"
                    ),
                    color=discord.Color.red(),
                )
                dm_channel = member.dm_channel or await member.create_dm()
                await dm_channel.send(embed=embed)
            except (discord.Forbidden, discord.HTTPException):
                pass

    async def release_member(self, member: discord.Member, guild: discord.Guild, reason: str):
        self._remove_prisoner_row(member.id)

        # Al salir de prisión, todo el dinero ilegal acumulado se convierte
        # en dinero legal y se deposita directamente en la billetera.
        illegal_data = self.get_illegal_data(member.id)
        converted_amount = illegal_data["balance"]
        if converted_amount > 0:
            self.update_illegal_balance(member.id, -converted_amount)
            self.update_balances(
                member.id, wallet_change=converted_amount, bank_change=0,
                reason="Salida de prisión",
                details=f"Convertiste {self.money(converted_amount)} de dinero ilegal a legal al salir de prisión."
            )

        free_role = guild.get_role(FREE_ROLE_ID)
        prison_role = guild.get_role(PRISON_ROLE_ID)
        try:
            if prison_role and prison_role in member.roles:
                await member.remove_roles(prison_role, reason="Liberado")
            if free_role and free_role not in member.roles:
                await member.add_roles(free_role, reason="Liberado")
        except (discord.Forbidden, discord.HTTPException):
            pass
        try:
            description = reason
            if converted_amount > 0:
                description += f"\n\n💵 Se convirtieron **{self.money(converted_amount)}** de dinero ilegal a tu billetera legal."
            embed = discord.Embed(title=f"{success_emoji(self.bot)} Has sido liberado", description=description, color=discord.Color.green())
            dm_channel = member.dm_channel or await member.create_dm()
            await dm_channel.send(embed=embed)
        except (discord.Forbidden, discord.HTTPException):
            pass

    def find_member(self, user_id):
        for guild in self.bot.guilds:
            member = guild.get_member(user_id)
            if member:
                return guild, member
        return None, None

    @tasks.loop(minutes=1)
    async def release_engine(self):
        try:
            conn = self._prison_connect()
            cursor = conn.cursor()
            now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
            cursor.execute("SELECT user_id FROM prisoners WHERE release_at <= ?", (now_iso,))
            due = [row[0] for row in cursor.fetchall()]
            conn.close()

            for user_id in due:
                guild, member = self.find_member(user_id)
                if guild and member:
                    await self.release_member(member, guild, "Has cumplido tu condena y has sido liberado.")
                else:
                    self._remove_prisoner_row(user_id)
        except Exception as e:
            print(f"Error en release_engine: {e}")

    @release_engine.before_loop
    async def before_release_engine(self):
        await self.bot.wait_until_ready()

    # --- COMANDOS DE PRISIÓN ---

    @commands.hybrid_command(name="horario", aliases=["schedule"], description="Muestra el horario actual de la prisión.")
    async def horario(self, ctx: commands.Context):
        if not await self.enforce_prison_channel(ctx):
            return
        schedule = current_schedule()
        remaining = seconds_left_in_schedule()
        embed = discord.Embed(title="🕰️ Horario de la Prisión", color=discord.Color.orange())
        embed.add_field(name="Horario actual", value=SCHEDULE_LABELS[schedule], inline=False)
        embed.add_field(name="Cambia en", value=format_prison_duration(remaining), inline=False)
        embed.add_field(
            name="Ciclo",
            value="Celda ➜ Comida ➜ Celda ➜ Tiempo Libre (se repite, 1 hora cada uno)",
            inline=False,
        )
        await ctx.send(embed=embed)

    @commands.hybrid_command(name="cellescape", description="Intenta abrir un túnel de escape durante el horario de celda.")
    @commands.cooldown(1, 3600, commands.BucketType.user)
    async def cellescape(self, ctx: commands.Context):
        member = ctx.author
        if not await self.enforce_prison_channel(ctx):
            ctx.command.reset_cooldown(ctx)
            return
        if not self.is_in_prison(member):
            ctx.command.reset_cooldown(ctx)
            await ctx.send(embed=self.error_embed("No estás en prisión."))
            return
        if current_schedule() != "celda":
            ctx.command.reset_cooldown(ctx)
            await ctx.send(embed=self.error_embed(
                f"Este comando solo se puede usar durante el {SCHEDULE_LABELS['celda']}. "
                f"Horario actual: {SCHEDULE_LABELS[current_schedule()]}."
            ))
            return

        prisoner = self.get_prisoner(member.id)
        tunnel_progress = prisoner[3] if prisoner else 0

        if random.random() < 0.50:
            tunnel_progress = min(100, tunnel_progress + 10)
            self.set_tunnel_progress(member.id, tunnel_progress)
            if tunnel_progress >= 100:
                await self.release_member(member, ctx.guild, "¡Completaste el túnel y escapaste de la prisión!")
                await ctx.send(embed=self.success_embed(
                    "¡Completaste el túnel al 100% y escapaste de la prisión!", title="¡Fuga exitosa!"
                ))
                return
            await ctx.send(embed=self.success_embed(
                f"Avanzaste en tu túnel de escape. Progreso actual: **{tunnel_progress}%**.", title="Túnel excavado"
            ))
        else:
            if random.random() < 0.50:
                await self.jail_member(member, ctx.guild, 24 * 3600, "Te atraparon cavando un túnel de escape.")
                await ctx.send(embed=self.error_embed(
                    "Te atraparon cavando el túnel. Se añadieron **24 horas** a tu condena.", title="Atrapado"
                ))
            else:
                await ctx.send(embed=self.error_embed(
                    "Lo intentaste, pero no lograste avanzar esta vez. Nadie se dio cuenta.", title="Intento fallido"
                ))

    @commands.hybrid_command(name="bribe", description="Intenta sobornar a los guardias durante el horario de comida.")
    @commands.cooldown(1, 3600, commands.BucketType.user)
    async def bribe(self, ctx: commands.Context):
        member = ctx.author
        if not await self.enforce_prison_channel(ctx):
            ctx.command.reset_cooldown(ctx)
            return
        if not self.is_in_prison(member):
            ctx.command.reset_cooldown(ctx)
            await ctx.send(embed=self.error_embed("No estás en prisión."))
            return
        if current_schedule() != "comida":
            ctx.command.reset_cooldown(ctx)
            await ctx.send(embed=self.error_embed(
                f"Este comando solo se puede usar durante el {SCHEDULE_LABELS['comida']}. "
                f"Horario actual: {SCHEDULE_LABELS[current_schedule()]}."
            ))
            return

        illegal_data = self.get_illegal_data(member.id)
        if illegal_data["balance"] < BRIBE_COST:
            ctx.command.reset_cooldown(ctx)
            await ctx.send(embed=self.error_embed(
                f"Necesitas **{BRIBE_COST:,} 🕵️ (ilegal)** para intentar sobornar a los guardias."
            ))
            return

        self.update_illegal_balance(member.id, -BRIBE_COST)

        if random.random() < 0.50:
            await self.jail_member(member, ctx.guild, 24 * 3600, "Los guardias rechazaron tu soborno y te delataron.")
            await ctx.send(embed=self.error_embed(
                "Los guardias rechazaron tu soborno. Se añadieron **24 horas** a tu condena y perdiste el dinero.",
                title="Soborno rechazado"
            ))
        else:
            prisoner = self.get_prisoner(member.id)
            release_at = datetime.datetime.fromisoformat(prisoner[2])
            now = datetime.datetime.now(datetime.timezone.utc)
            remaining = (release_at - now).total_seconds()
            new_remaining = max(0, remaining * 0.5)
            new_release = now + datetime.timedelta(seconds=new_remaining)
            self._upsert_prisoner(member.id, ctx.guild.id, new_release.isoformat())
            await ctx.send(embed=self.success_embed(
                f"¡Los guardias aceptaron tu soborno! Tu condena restante se redujo un **50%**. "
                f"Sales {discord.utils.format_dt(new_release, style='R')}.",
                title="Soborno aceptado"
            ))

    @commands.hybrid_command(name="escape", description="Intenta escapar de la prisión durante el tiempo libre.")
    @commands.cooldown(1, 3600, commands.BucketType.user)
    async def escape(self, ctx: commands.Context):
        member = ctx.author
        if not await self.enforce_prison_channel(ctx):
            ctx.command.reset_cooldown(ctx)
            return
        if not self.is_in_prison(member):
            ctx.command.reset_cooldown(ctx)
            await ctx.send(embed=self.error_embed("No estás en prisión."))
            return
        if current_schedule() != "libre":
            ctx.command.reset_cooldown(ctx)
            await ctx.send(embed=self.error_embed(
                f"Este comando solo se puede usar durante el {SCHEDULE_LABELS['libre']}. "
                f"Horario actual: {SCHEDULE_LABELS[current_schedule()]}."
            ))
            return

        if random.random() < 0.25:
            await self.release_member(member, ctx.guild, "¡Aprovechaste el tiempo libre para escapar de la prisión!")
            await ctx.send(embed=self.success_embed(
                "¡Aprovechaste un descuido durante el tiempo libre y escapaste de la prisión!", title="¡Fuga exitosa!"
            ))
        else:
            await self.jail_member(member, ctx.guild, 24 * 3600, "Te atraparon intentando escapar durante el tiempo libre.")
            await ctx.send(embed=self.error_embed(
                "Te atraparon en el intento. Se añadió **1 día** a tu condena.", title="Atrapado"
            ))

    @commands.hybrid_command(name="penalty", description="Muestra cuánto tiempo de condena te queda (o a otro usuario, solo staff).")
    @app_commands.describe(target_user="Usuario a consultar (solo el equipo de moderación puede consultar a otros).")
    async def penalty(self, ctx: commands.Context, target_user: discord.Member = None):
        if not await self.enforce_prison_channel(ctx):
            return
        if target_user is not None and target_user.id != ctx.author.id:
            if not self.has_staff_role(ctx.author):
                await ctx.send(embed=self.error_embed("Solo el equipo de moderación puede consultar la condena de otros usuarios."))
                return
        member = target_user or ctx.author
        prisoner = self.get_prisoner(member.id)
        if not prisoner:
            description = "No estás cumpliendo ninguna condena." if member.id == ctx.author.id else f"{member.mention} no está cumpliendo ninguna condena."
            await ctx.send(embed=self.success_embed(description, title="Sin condena"))
            return

        release_at = datetime.datetime.fromisoformat(prisoner[2])
        now = datetime.datetime.now(datetime.timezone.utc)
        remaining = max(0, (release_at - now).total_seconds())

        embed = discord.Embed(title=f"⛓️ Condena de {member.display_name}", color=discord.Color.orange())
        embed.add_field(name="Tiempo restante", value=format_prison_duration(remaining), inline=False)
        embed.add_field(name="Sale", value=discord.utils.format_dt(release_at, style="R"), inline=False)
        embed.add_field(name="Progreso del túnel de escape", value=f"{prisoner[3]}%", inline=False)
        await ctx.send(embed=embed)

    async def handle_prison_steal(self, ctx, target_member: discord.Member):
        member = ctx.author
        if not await self.enforce_prison_channel(ctx):
            return
        if current_schedule() != "libre":
            await ctx.send(embed=self.error_embed(
                f"Solo puedes robar durante el {SCHEDULE_LABELS['libre']}. "
                f"Horario actual: {SCHEDULE_LABELS[current_schedule()]}."
            ))
            return
        if target_member.id == member.id:
            await ctx.send(embed=self.error_embed("No puedes robarte a ti mismo."))
            return
        if not self.is_in_prison(target_member):
            await ctx.send(embed=self.error_embed("Solo puedes robarle a otros reclusos."))
            return

        victim_data = self.get_illegal_data(target_member.id)
        if victim_data["balance"] <= 0:
            await ctx.send(embed=self.error_embed(f"{target_member.mention} no tiene dinero ilegal que robarle."))
            return

        if random.random() < 0.50:
            if random.random() < 0.50:
                await self.jail_member(member, ctx.guild, 12 * 3600, "Te atraparon intentando robar a otro recluso.")
                await ctx.send(embed=self.error_embed(
                    "Te atraparon en el intento. Se añadieron **12 horas** a tu condena.", title="Robo fallido"
                ))
            else:
                await ctx.send(embed=self.error_embed(
                    f"Intentaste robarle a {target_member.name}, pero fallaste. Por suerte, nadie se dio cuenta.",
                    title="Robo fallido"
                ))
            return

        stolen = victim_data["balance"]
        self.update_illegal_balance(target_member.id, -stolen)
        self.update_illegal_balance(member.id, stolen)
        await ctx.send(embed=self.success_embed(
            f"¡Le robaste **{stolen:,} 🕵️ (ilegal)** a {target_member.mention}!", title="Robo exitoso"
        ))

    # --- MERCADO NEGRO ---

    @commands.hybrid_command(name="blackmarket", aliases=["mercadonegro"], description="Muestra el catálogo y horario del mercado negro.")
    async def blackmarket(self, ctx: commands.Context):
        if not await self.enforce_prison_channel(ctx):
            return
        open_now, remaining = market_seconds_remaining()
        embed = discord.Embed(
            title="🕶️ Mercado Negro",
            description="Un lugar clandestino con contactos y servicios criminales. Cada compra conlleva un riesgo real de arresto.",
            color=discord.Color.dark_gray(),
        )
        if open_now:
            embed.add_field(name="Estado", value=f"🟢 Abierto — se cierra en {format_prison_duration(remaining)}", inline=False)
        else:
            embed.add_field(name="Estado", value=f"🔴 Cerrado — abre en {format_prison_duration(remaining)}", inline=False)
        embed.add_field(
            name=f"🥷 ladron - {format_money(self.bot, LADRON_PRICE)}",
            value="Empleado de robo. Genera ingresos criminales pasivos. Incompatible con tener Empresa. Máx 5.",
            inline=False,
        )
        embed.add_field(
            name=f"🔫 sicario - {format_money(self.bot, HITMAN_COST)}",
            value="Te permite usar `&kill @usuario` para garantizar el éxito de tu próximo `&steal` contra esa persona, ignorando su seguro.",
            inline=False,
        )
        embed.add_field(
            name="⚠️ Riesgo",
            value=f"Cada compra tiene un **{int(BLACK_MARKET_JAIL_CHANCE * 100)}%** de probabilidad de mandarte a prisión por 1 día.",
            inline=False,
        )
        embed.set_footer(text="Usa &buyblackmarket <sicario|ladron> para comprar.")
        await ctx.send(embed=embed)

    @commands.hybrid_command(name="buyblackmarket", aliases=["bbm"], description="Compra un artículo del mercado negro.")
    @app_commands.describe(item="Artículo a comprar: sicario o ladron")
    async def buy_black_market(self, ctx: commands.Context, item: str):
        if not await self.enforce_prison_channel(ctx):
            return
        if not is_market_open():
            _, remaining = market_seconds_remaining()
            await ctx.send(embed=self.error_embed(f"El mercado negro está cerrado. Abrirá en {format_prison_duration(remaining)}."))
            return

        item_key = item.lower().strip()
        user_data = self.get_user_data(ctx.author.id)

        if item_key == "sicario":
            cost = HITMAN_COST
            if user_data["wallet"] < cost:
                await ctx.send(embed=self.error_embed(f"Necesitas {format_money(self.bot, cost)} en tu billetera."))
                return
            self.update_balances(
                ctx.author.id, wallet_change=-cost, bank_change=0,
                reason="Mercado negro", details=f"Compró un sicario por {format_money(self.bot, cost)}."
            )
            self.add_hitman(ctx.author.id, 1)
            purchased_label = "un Sicario"

        elif item_key == "ladron":
            cost = LADRON_PRICE
            if user_data["company"] == 1:
                await ctx.send(embed=self.error_embed("Tienes una empresa legal constituida. No puedes contratar Empleados de Robo."))
                return
            if user_data["robbery_employees"] >= 5:
                await ctx.send(embed=self.error_embed("Has alcanzado el límite máximo de 5 Empleados de Robo."))
                return
            if user_data["wallet"] < cost:
                await ctx.send(embed=self.error_embed(f"Necesitas {format_money(self.bot, cost)} en tu billetera."))
                return
            self.update_balances(
                ctx.author.id, wallet_change=-cost, bank_change=0,
                reason="Mercado negro", details=f"Contrató un Empleado de Robo por {format_money(self.bot, cost)}."
            )
            self.update_asset(ctx.author.id, "robbery_employees", user_data["robbery_employees"] + 1)
            self.update_asset(ctx.author.id, "last_salary_pay", datetime.datetime.now().isoformat())
            self.update_asset(ctx.author.id, "salary_penalty_processed", 0)
            purchased_label = "un Empleado de Robo"

        else:
            await ctx.send(embed=self.error_embed("Artículo no reconocido. Opciones válidas: `sicario`, `ladron`."))
            return

        if random.random() < BLACK_MARKET_JAIL_CHANCE:
            await self.jail_member(ctx.author, ctx.guild, BLACK_MARKET_JAIL_SECONDS, "Te atraparon haciendo negocios en el mercado negro.")
            await ctx.send(embed=self.error_embed(
                f"Compraste {purchased_label}, pero te atraparon en la operación. Se añadió **1 día** a tu condena.",
                title="Emboscada"
            ))
        else:
            await ctx.send(embed=self.success_embed(f"Compraste {purchased_label} sin que nadie se diera cuenta.", title="Compra exitosa"))

    @commands.hybrid_command(name="kill", description="Usa un sicario contra alguien (65% de éxito).")
    @app_commands.describe(target_user="La persona objetivo del sicario.")
    async def kill(self, ctx: commands.Context, target_user: discord.Member):
        if not await self.enforce_prison_channel(ctx):
            return
        if target_user.id == ctx.author.id:
            await ctx.send(embed=self.error_embed("No puedes contratar un sicario contra ti mismo."))
            return
        if not self.consume_hitman(ctx.author.id):
            await ctx.send(embed=self.error_embed("No tienes ningún sicario disponible. Cómpralo en el mercado negro con `&buyblackmarket sicario`."))
            return

        if random.random() < 0.65:
            self.create_contract(ctx.author.id, target_user.id, ctx.guild.id)
            await ctx.send(embed=self.success_embed(
                f"Tu sicario cumplió el encargo contra {target_user.mention}. Tu próximo `&steal` contra esta "
                "persona tendrá éxito garantizado, sin importar su seguro.",
                title="Encargo cumplido"
            ))
        else:
            if random.random() < 0.50:
                await ctx.send(embed=self.error_embed(
                    "Tu sicario falló el encargo, pero nadie sospechó de ti.", title="Encargo fallido"
                ))
            else:
                await self.jail_member(ctx.author, ctx.guild, 3 * 86400, "Te descubrieron como el autor intelectual de un intento de asesinato.")
                await ctx.send(embed=self.error_embed(
                    "Tu sicario falló el encargo y te descubrieron como autor intelectual. Fuiste enviado a "
                    "prisión por **3 días**.",
                    title="Descubierto"
                ))


    # --- COMANDOS ADMINISTRATIVOS ---

    @commands.hybrid_command(name="sendprison", description="Envía a un usuario a prisión por un tiempo determinado.")
    @app_commands.describe(
        target_user="Usuario a encarcelar.",
        duration_input="Duración (ej. 30m, 12h, 2d, 1a).",
        reason="Razón (opcional).",
    )
    async def sendprison(self, ctx: commands.Context, target_user: discord.Member, duration_input: str, *, reason: str = "No especificada"):
        if not self.has_staff_role(ctx.author):
            await ctx.send(embed=self.error_embed("No cuentas con los roles requeridos para utilizar este comando."))
            return

        duration_seconds = parse_duration_to_seconds(duration_input)
        if duration_seconds is None:
            await ctx.send(embed=self.error_embed("Duración inválida. Usa un formato como `30m`, `12h`, `2d` o `1a`."))
            return

        await self.jail_member(target_user, ctx.guild, duration_seconds, f"Un moderador te envió a prisión. Razón: {reason}")
        await self.log_prison_action(ctx.guild, "Encarcelamiento manual", target_user, ctx.author, reason, format_prison_duration(duration_seconds))
        await ctx.send(embed=self.success_embed(
            f"{target_user.mention} fue enviado a prisión por **{format_prison_duration(duration_seconds)}**.\nMotivo: {reason}",
            title="Usuario encarcelado"
        ))

    @commands.hybrid_command(name="setfree", description="Libera a un usuario de la prisión.")
    @app_commands.describe(target_user="Usuario a liberar.", reason="Razón (opcional).")
    async def setfree(self, ctx: commands.Context, target_user: discord.Member, *, reason: str = "No especificada"):
        if not self.has_staff_role(ctx.author):
            await ctx.send(embed=self.error_embed("No cuentas con los roles requeridos para utilizar este comando."))
            return

        if not self.get_prisoner(target_user.id):
            await ctx.send(embed=self.error_embed(f"{target_user.mention} no está en prisión."))
            return

        await self.release_member(target_user, ctx.guild, f"Un moderador te liberó de la prisión. Razón: {reason}")
        await self.log_prison_action(ctx.guild, "Liberación manual", target_user, ctx.author, reason)
        await ctx.send(embed=self.success_embed(
            f"{target_user.mention} fue liberado de la prisión.\nMotivo: {reason}",
            title="Usuario liberado"
        ))

# --- REQUIRED ENTRY POINT FOR DISCORD.PY EXTENSIONS ---
async def setup(bot):
    await bot.add_cog(Economy(bot))