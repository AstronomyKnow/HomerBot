import discord
from discord import app_commands
from discord.ext import commands, tasks
import sqlite3
import random
import datetime
import json
import asyncio

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
            await interaction.response.send_message("❌ Esta no es tu sesión de inversión.", ephemeral=True)
            return
        self.action = "buy"
        await self.process_result(interaction)

    @discord.ui.button(label="Vender (Short)", style=discord.ButtonStyle.red)
    async def sell_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id: 
            await interaction.response.send_message("❌ Esta no es tu sesión de inversión.", ephemeral=True)
            return
        self.action = "sell"
        await self.process_result(interaction)

    async def process_result(self, interaction: discord.Interaction):
        for child in self.children:
            child.disabled = True
        await interaction.message.edit(view=self)
        
        await interaction.response.send_message("⏳ Procesando transacción criptográfica en la blockchain (5 segundos)...")
        await asyncio.sleep(5)
        
        user_data = self.economy.get_user_data(self.user_id)
        if user_data["wallet"] < self.investment:
            await interaction.followup.send("❌ Ya no dispones de los fondos suficientes en tu billetera.")
            return

        market_went_up = random.random() < 0.70 if self.trend else random.random() < 0.30
        win = False
        
        if self.action == "buy" and market_went_up: win = True
        elif self.action == "sell" and not market_went_up: win = True
        
        if win:
            self.economy.update_balances(self.user_id, wallet_change=self.investment, bank_change=0)
            await interaction.followup.send(f"🎉 ¡Operación Exitosa! El mercado se movió a tu favor y ganaste **${self.investment:,}** en efectivo.")
        else:
            self.economy.update_balances(self.user_id, wallet_change=-self.investment, bank_change=0)
            await interaction.followup.send(f"📉 ¡Liquidado! El mercado se movió en tu contra y perdiste los **${self.investment:,}** invertidos.")
        self.stop()


# --- MAIN ECONOMY COG ---
class Economy(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.db_path = "economy.db"
        self.staff_roles = [1362456351263035553, 1361138268829253875, 1359359923770757150, 1372448974211911770]
        
        self.prices = {
            "seguro": 15000,
            "empresa": 75000,
            "empleado": 10000,
            "ladron": 12000,
            "acciones_yt": 5000,
            "acciones_ms": 4500,
            "mega_yt": 750000,
            "mega_ms": 650000
        }
        
        self.passive_interval_seconds = 30 * 60
        self.passive_last_run = datetime.datetime.now(datetime.timezone.utc)
        self.topbar_channel_id = 1524131320962220084
        self.audit_channel_id = 1524421682377392338
        self.topbar_message_id = None
        self.initialize_database()
        self.passive_engine.start()
        self.topbar_engine.start()

    def cog_unload(self):
        self.passive_engine.cancel()
        self.topbar_engine.cancel()

    # --- DATABASE UTILITIES ---
    def initialize_database(self):
        try:
            conn = sqlite3.connect(self.db_path)
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
                ("fine", "INTEGER DEFAULT 0")
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

    def apply_fine(self, user_id: int, amount: int):
        if amount <= 0:
            return 0
        data = self.get_user_data(user_id)
        self.update_asset(user_id, "fine", data["fine"] + amount)
        asyncio.create_task(self.log_economy_event(
            "⚖️ Multa impuesta",
            "Se aplicó una multa a un usuario de economía.",
            user_id=user_id,
            fields=[("💸 Monto", f"${amount:,}"), ("🧾 Multa nueva", f"${data['fine'] + amount:,}")],
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

        self.update_balances(user_id, wallet_change=-wallet_to_pay, bank_change=-bank_to_pay, reason="Pago de multa", details=f"Pagó ${payable:,} de una multa pendiente.")
        self.update_asset(user_id, "fine", data["fine"] - payable)
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
                fields=[("💵 Billetera cobrada", f"${wallet_pending:,}"), ("🏦 Banco cobrado", f"${bank_pending:,}")],
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
            ("💵 Cambio billetera", f"{wallet_change:+,}"),
            ("🏦 Cambio banco", f"{bank_change:+,}"),
            ("📊 Balance anterior", f"💵 ${before_wallet:,} | 🏦 ${before_bank:,}"),
            ("📈 Nuevo balance", f"💵 ${new_wallet:,} | 🏦 ${new_bank:,}")
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
                    lines.append(f"{index}. {name} — 💵 ${wallet:,} | 🏦 ${bank:,} | 💰 ${total_money:,}")

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
            cursor.execute("SELECT user_id, employees, stocks, mega_companies, robbery_employees, fine FROM users")
            all_users = cursor.fetchall()
            
            for row in all_users:
                user_id, employees, stocks_str, mega_str, rob_emp, fine = row
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
                
                if wallet_yield != 0 or bank_yield != 0:
                    cursor.execute(
                        "UPDATE users SET pending_wallet = max(0, pending_wallet + ?), pending_bank = max(0, pending_bank + ?) WHERE user_id = ?",
                        (wallet_yield, bank_yield, user_id)
                    )
            
            conn.commit()
            conn.close()
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

    @commands.Cog.listener()
    async def on_command_error(self, ctx, error):
        if isinstance(error, commands.CommandOnCooldown):
            embed = discord.Embed(
                title="⏳ Cooldown activo",
                description=self.format_cooldown_message(error.retry_after),
                color=discord.Color.orange()
            )
            await ctx.send(embed=embed)
            return

    @commands.Cog.listener()
    async def on_app_command_error(self, interaction, error):
        if isinstance(error, discord.app_commands.CommandOnCooldown):
            embed = discord.Embed(
                title="⏳ Cooldown activo",
                description=self.format_cooldown_message(error.retry_after),
                color=discord.Color.orange()
            )
            if interaction.response.is_done():
                await interaction.followup.send(embed=embed, ephemeral=True)
            else:
                await interaction.response.send_message(embed=embed, ephemeral=True)
            return

    # --- HYBRID COMMANDS SYSTEM (PREFIX & SLASH) ---

    @commands.hybrid_command(name="ecohelp", description="Muestra la lista de comandos disponibles y cómo funcionan.")
    async def ecohelp(self, ctx: commands.Context):
        embed = discord.Embed(
            title="📖 Manual del Sistema de Economía", 
            description="Todos los comandos responden tanto a comandos de barra (`/`) como al prefijo (`&`).", 
            color=discord.Color.blue()
        )
        embed.add_field(
            name="💳 Gestión Básica",
            value="`&balance [usuario]` - Consulta el saldo en billetera y banco.\n"
                  "`&daily` - Reclama tu bono diario de $500.\n"
                  "`&deposit <monto|all>` - Introduce tu efectivo en el banco seguro.\n"
                  "`&withdraw <monto|all>` - Retira tus fondos del banco.\n"
                  "`&give <usuario> <monto>` - Transfiere dinero directo a otro miembro.\n"
                  "`&top` - Lista de clasificación de los usuarios más ricos.",
            inline=False
        )
        embed.add_field(
            name="🛠️ Ingresos Activos e Inversión",
            value="`&work` - Trabaja para ganar un sueldo fijo (Cooldown: 30s).\n"
                  "`&crime` - Intenta un acto ilegal riesgoso (Cooldown: 3m).\n"
                  "`&collect` - Cobra el dinero pasivo acumulado por tus negocios e inversiones.\n"
                  "`&collectiontime` - Consulta cuándo y cuánto generarán tus activos AFK.\n"
                  "`&crypto [monto]` - Abre un panel interactivo para predecir alzas o bajas cripto.",
            inline=False
        )
        embed.add_field(
            name="🏢 Negocios e Infraestructura",
            value="`&shop` - Revisa el catálogo completo de propiedades y activos.\n"
                  "`&buy <artículo>` - Adquiere un objeto de la tienda.\n"
                  "`&salarypay` - Paga las nóminas de tus empleados (Deberás hacerlo cada 24h).",
            inline=False
        )
        embed.add_field(
            name="🥷 Interacción Criminal Inter-Usuario",
            value="`&steal <usuario>` - Intenta desvalijar la cartera de alguien (Cooldown: 1h).\n"
                  "`&sue <usuario>` - Demanda judicialmente a tu último asaltante para exigir una compensación.",
            inline=False
        )
        if self.is_staff(ctx.author):
            embed.add_field(
                name="⚙️ Herramientas Administrativas",
                value="`&addmoney <usuario> <monto>` - Adición arbitraria de capital.\n"
                      "`&removemoney <usuario> <monto>` - Extracción arbitraria de capital.",
                inline=False
            )
        await ctx.send(embed=embed)

    @commands.hybrid_command(name="balance", aliases=["bal"], description="Muestra tu dinero actual en la billetera y el banco.")
    @app_commands.describe(target_user="El usuario del cual deseas revisar las finanzas.")
    async def balance_slash(self, ctx: commands.Context, target_user: discord.Member = None):
        user = target_user or ctx.author
        if target_user and target_user != ctx.author:
            if not self.is_staff(ctx.author):
                await ctx.send("❌ No tienes permiso para ver el balance de otros miembros.", ephemeral=True)
                return
                
        user_data = self.get_user_data(user.id)
        
        embed = discord.Embed(title=f"Balance de {user.display_name}", color=discord.Color.green())
        embed.set_thumbnail(url=user.display_avatar.url)
        embed.add_field(name="💵 Billetera", value=f"${user_data['wallet']:,}", inline=True)
        embed.add_field(name="🏦 Banco", value=f"${user_data['bank']:,}", inline=True)
        embed.add_field(name="⚠️ Multa pendiente", value=f"${user_data['fine']:,}", inline=True)
        embed.add_field(name="⏳ Por cobrar", value=f"💵 ${user_data['pending_wallet']:,} / 🏦 ${user_data['pending_bank']:,}", inline=False)
        embed.add_field(name="💰 Total", value=f"${(user_data['wallet'] + user_data['bank']):,}", inline=False)
        embed.timestamp = datetime.datetime.now(datetime.timezone.utc)
        
        await ctx.send(embed=embed)

    @commands.hybrid_command(name="addmoney", description="Añade dinero a la billetera, banco o ambos de un usuario específico.")
    @app_commands.describe(target_user="Usuario beneficiado", amount="Monto de dinero", scope_input="billetera, banco o ambos")
    async def add_money_slash(self, ctx: commands.Context, target_user: discord.Member, amount: int, scope_input: str = "billetera"):
        if not self.is_staff(ctx.author):
            await ctx.send("❌ No tienes permiso para usar este comando.", ephemeral=True)
            return
        if amount <= 0:
            await ctx.send("❌ La cantidad debe ser mayor a cero.", ephemeral=True)
            return

        scope = self.parse_balance_scope(scope_input)
        if scope is None:
            await ctx.send("❌ Scope inválido. Usa: billetera, banco, ambos, 1, 2 o 3.", ephemeral=True)
            return

        wallet_change = amount if scope in ("wallet", "both") else 0
        bank_change = amount if scope in ("bank", "both") else 0
        self.update_balances(target_user.id, wallet_change=wallet_change, bank_change=bank_change)
        await ctx.send(f"✅ Se han añadido **${amount:,}** a {self.scope_label(scope)} de {target_user.mention}.")

    @commands.hybrid_command(name="removemoney", description="Quita dinero de la billetera, banco o ambos de un usuario específico.")
    @app_commands.describe(target_user="Usuario afectado", amount="Monto de dinero", scope_input="billetera, banco o ambos")
    async def remove_money_slash(self, ctx: commands.Context, target_user: discord.Member, amount: int, scope_input: str = "billetera"):
        if not self.is_staff(ctx.author):
            await ctx.send("❌ No tienes permiso para usar este comando.", ephemeral=True)
            return
        if amount <= 0:
            await ctx.send("❌ La cantidad debe ser mayor a cero.", ephemeral=True)
            return

        scope = self.parse_balance_scope(scope_input)
        if scope is None:
            await ctx.send("❌ Scope inválido. Usa: billetera, banco, ambos, 1, 2 o 3.", ephemeral=True)
            return

        wallet_change = -amount if scope in ("wallet", "both") else 0
        bank_change = -amount if scope in ("bank", "both") else 0
        self.update_balances(target_user.id, wallet_change=wallet_change, bank_change=bank_change)
        await ctx.send(f"✅ Se han retirado **${amount:,}** de {self.scope_label(scope)} de {target_user.mention}.")

    @commands.hybrid_command(name="shop", description="Muestra el catálogo de la tienda de economía.")
    async def shop_prefix(self, ctx: commands.Context):
        embed = discord.Embed(title="🏪 Tienda del Servidor", description="Utiliza `&buy <item>` o `/buy` para adquirir mejoras financieras.", color=discord.Color.gold())
        embed.add_field(name="🛡️ seguro - $15,000", value="Mitiga pérdidas de dinero si sufres un robo con éxito.", inline=False)
        embed.add_field(name="🏢 empresa - $75,000", value="Añade permanentemente un **75% más de ingresos** al usar `&work`.", inline=False)
        embed.add_field(name="👥 empleado - $10,000", value="Genera ingresos pasivos recurrentes. Requiere Empresa. Máx 20.", inline=False)
        embed.add_field(name="🥷 ladron - $12,000", value="Genera ingresos criminales automáticos. Requiere NO tener Empresa. Máx 5.", inline=False)
        embed.add_field(name="📈 acciones_yt / acciones_ms - $5,000 / $4,500", value="Generan dividendos pasivos volátiles inyectados en el banco (pueden dar pérdidas).", inline=False)
        embed.add_field(name="🏛️ mega_yt / mega_ms - $750,000 / $650,000", value="Adquiere monopolios absolutos para recibir masivas inyecciones de dinero.", inline=False)
        await ctx.send(embed=embed)

    @commands.hybrid_command(name="buy", description="Compra un artículo o inversión de la tienda.")
    @app_commands.describe(item_name="El nombre exacto del artículo que deseas comprar.")
    async def buy_prefix(self, ctx: commands.Context, item_name: str):
        user_id = ctx.author.id
        data = self.get_user_data(user_id)
        item_key = item_name.lower()
        
        if item_key not in self.prices:
            await ctx.send("❌ Ese artículo no se encuentra disponible en la tienda. Revisa `&shop`.")
            return
            
        cost = self.prices[item_key]
        if data["wallet"] < cost:
            await ctx.send(f"❌ Fondos insuficientes en tu billetera. Necesitas **${cost:,}**.")
            return

        if item_key == "seguro":
            if data["insurance"] == 1:
                await ctx.send("❌ Ya tienes contratado un Seguro de Banco.")
                return
            self.update_asset(user_id, "insurance", 1)
            
        elif item_key == "empresa":
            if data["company"] == 1:
                await ctx.send("❌ Ya eres propietario de una Empresa.")
                return
            if data["robbery_employees"] > 0:
                await ctx.send("❌ No puedes fundar una empresa legal si tienes contratados Empleados de Robo.")
                return
            self.update_asset(user_id, "company", 1)
            
        elif item_key == "empleado":
            if data["company"] == 0:
                await ctx.send("❌ Necesitas comprar primero una Empresa legal para poder contratar empleados.")
                return
            if data["employees"] >= 20:
                await ctx.send("❌ Has alcanzado el límite máximo permitido de 20 empleados.")
                return
            self.update_asset(user_id, "employees", data["employees"] + 1)
            self.update_asset(user_id, "last_salary_pay", datetime.datetime.now().isoformat())
            
        elif item_key == "ladron":
            if data["company"] == 1:
                await ctx.send("❌ Tienes una empresa legal constituida. No puedes contratar Empleados de Robo.")
                return
            if data["robbery_employees"] >= 5:
                await ctx.send("❌ Has alcanzado el límite máximo de 5 Empleados de Robo.")
                return
            self.update_asset(user_id, "robbery_employees", data["robbery_employees"] + 1)
            self.update_asset(user_id, "last_salary_pay", datetime.datetime.now().isoformat())
            
        elif item_key in ["acciones_yt", "acciones_ms"]:
            stock_id = item_key.replace("acciones_", "")
            current_stocks = data["stocks"]
            current_stocks[stock_id] = current_stocks.get(stock_id, 0) + 1
            self.update_asset(user_id, "stocks", json.dumps(current_stocks))
            
        elif item_key in ["mega_yt", "mega_ms"]:
            mega_id = item_key.replace("mega_", "")
            current_megas = data["mega_companies"]
            if mega_id in current_megas:
                await ctx.send(f"❌ Ya eres el dueño de la multinacional {mega_id.upper()}.")
                return
            current_megas.append(mega_id)
            self.update_asset(user_id, "mega_companies", json.dumps(current_megas))

        self.update_balances(user_id, wallet_change=-cost, bank_change=0)
        await ctx.send(f"🛍️ ¡Has comprado **{item_name}** con éxito por **${cost:,}**!")

    @commands.hybrid_command(name="salarypay", description="Paga los salarios de tus empleados contratados.")
    async def salary_pay_prefix(self, ctx: commands.Context):
        user_id = ctx.author.id
        data = self.get_user_data(user_id)
        
        if data["employees"] == 0 and data["robbery_employees"] == 0:
            await ctx.send("❌ No posees empleados contratados que requieran nómina.")
            return
            
        cost_per_employee = 800 if data["employees"] > 0 else 1200
        total_count = data["employees"] if data["employees"] > 0 else data["robbery_employees"]
        total_salary_cost = total_count * cost_per_employee
        
        if data["wallet"] < total_salary_cost:
            await ctx.send(f"❌ Dinero insuficiente en tu billetera. Necesitas **${total_salary_cost:,}**.")
            return
            
        self.update_balances(user_id, wallet_change=-total_salary_cost, bank_change=0, reason="Nómina", details=f"Pagaste ${total_salary_cost:,} de salarios a tus empleados.")
        self.update_asset(user_id, "last_salary_pay", datetime.datetime.now().isoformat())
        await ctx.send(f"💼 Nómina pagada por **${total_salary_cost:,}**. Contrato renovado por 24 horas.")

    @commands.hybrid_command(name="work", description="Trabaja para conseguir dinero legal de forma activa.")
    @commands.cooldown(1, 30, commands.BucketType.user)
    async def work_prefix(self, ctx: commands.Context):
        user_id = ctx.author.id
        data = self.get_user_data(user_id)
        base_earnings = random.randint(100, 300)
        
        if data["company"] == 1:
            base_earnings = int(base_earnings * 1.75)

        if data["fine"] > 0:
            base_earnings = int(base_earnings * 0.25)
            
        self.update_balances(user_id, wallet_change=base_earnings, bank_change=0, reason="Trabajo", details=f"Ganaste ${base_earnings:,} trabajando.")
        await ctx.send(f"💰 ¡Trabajaste duro y ganaste **${base_earnings}**!")

    @commands.hybrid_command(name="crime", description="Comete un crimen ilegal para conseguir dinero rápido.")
    @commands.cooldown(1, 180, commands.BucketType.user)
    async def crime_prefix(self, ctx: commands.Context):
        user_id = ctx.author.id
        data = self.get_user_data(user_id)
        
        if random.random() < 0.20:
            payout = random.randint(800, 1500)
            if data["fine"] > 0:
                payout = int(payout * 0.25)
            self.update_balances(user_id, wallet_change=payout, bank_change=0, reason="Crimen", details=f"Tu crimen salió bien y ganaste ${payout:,}.")
            await ctx.send(f"🥷 ¡El atraco fue un éxito! Obtuviste **${payout:,}**.")
        else:
            penalty = random.randint(400, 900)
            self.apply_fine(user_id, penalty)
            await ctx.send(f"🚨 ¡Te atraparon cometiendo el crimen! Te impusieron una multa de **${penalty:,}**. Usa `&pay` para pagarla.")
    @commands.hybrid_command(name="steal", description="Intenta desvalijar la cartera de alguien.")
    @app_commands.describe(target_member="El usuario al que intentas robar.")
    async def steal_prefix(self, ctx: commands.Context, target_member: discord.Member):
        if target_member == ctx.author:
            await ctx.send("❌ No puedes robarte a ti mismo.")
            return
            
        thief_id = ctx.author.id
        victim_id = target_member.id
        victim_data = self.get_user_data(victim_id)
        
        total_stealable = victim_data["wallet"] + int(victim_data["bank"] * 0.10)
        if total_stealable <= 100:
            await ctx.send("❌ Este objetivo no tiene suficiente dinero para valer la pena.")
            return
            
        if random.random() < 0.50:
            if random.random() < 0.50:
                self.apply_fine(thief_id, 10000)
                await ctx.send(f"❌ Intentaste abrir la mochila de {target_member.name} pero fallaste y además te impusieron una multa de **$10,000**. Usa `&pay` para saldarla.")
            else:
                await ctx.send(f"❌ Intentaste abrir la mochila de {target_member.name} pero fallaste y escapaste corriendo.")
            return
            
        wallet_stolen = victim_data["wallet"]
        bank_stolen = int(victim_data["bank"] * 0.10)
        final_stolen_amount = wallet_stolen + bank_stolen
        
        if victim_data["insurance"] == 1:
            insurance_roll = random.random()
            if insurance_roll < 0.34:
                await ctx.send(f"🛡️ Intentaste robar a {target_member.name}, pero su **Seguro de Banco** bloqueó todo acceso a sus fondos.")
                return
            elif insurance_roll < 0.67:
                final_stolen_amount = int(final_stolen_amount * 0.5)
                wallet_stolen = int(wallet_stolen * 0.5)
                bank_stolen = int(bank_stolen * 0.5)
                await ctx.send(f"🛡️ El **Seguro de Banco** de {target_member.name} mitigó parcialmente el impacto.")

        self.update_balances(victim_id, wallet_change=-wallet_stolen, bank_change=-bank_stolen, reason="Robo", details=f"Se le retiraron fondos tras un intento de robo.", target_user_id=thief_id)
        self.update_balances(thief_id, wallet_change=final_stolen_amount, bank_change=0, reason="Robo", details=f"Robaste ${final_stolen_amount:,} a {target_member.name}.", actor_id=thief_id, target_user_id=victim_id)
        
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("INSERT INTO recent_thefts (thief_id, victim_id, amount_stolen, timestamp, resolved) VALUES (?, ?, ?, ?, 0)",
                       (thief_id, victim_id, final_stolen_amount, datetime.datetime.now().isoformat()))
        conn.commit()
        conn.close()
        
        await ctx.send(f"💸 ¡Robo completado con éxito! Le quitaste **${final_stolen_amount:,}** a {target_member.mention}.")

    @commands.hybrid_command(name="sue", description="Demanda legalmente a un usuario que te robó dinero hace poco.")
    @app_commands.describe(target_thief="El presunto ladrón al que vas a demandar.")
    async def sue_prefix(self, ctx: commands.Context, target_thief: discord.Member):
        victim_id = ctx.author.id
        thief_id = target_thief.id
        
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT amount_stolen, rowid FROM recent_thefts WHERE thief_id = ? AND victim_id = ? AND resolved = 0 ORDER BY timestamp DESC LIMIT 1", (thief_id, victim_id))
        row = cursor.fetchone()
        
        if not row:
            conn.close()
            await ctx.send(f"❌ No tienes registros de robos recientes sin resolver contra {target_thief.name}.")
            return
            
        stolen_amount, record_rowid = row
        cursor.execute("UPDATE recent_thefts SET resolved = 1 WHERE rowid = ?", (record_rowid,))
        conn.commit()
        conn.close()
        
        if random.random() < 0.50:
            compensation = int(stolen_amount * 1.10)
            self.update_balances(thief_id, wallet_change=-compensation, bank_change=0, reason="Demanda", details=f"Pagaste ${compensation:,} por una demanda judicial.")
            self.update_balances(victim_id, wallet_change=compensation, bank_change=0, reason="Demanda", details=f"Recibiste ${compensation:,} por una demanda judicial.")
            await ctx.send(f"⚖️ ¡Anulaste las defensas de {target_thief.mention} en la corte! Recibiste **${compensation:,}** por el robo y daños.")
        else:
            await ctx.send(f"⚖️ Perdiste el juicio contra {target_thief.name} por falta de pruebas.")

    @commands.hybrid_command(name="give", description="Transfiere una cantidad de efectivo a otro miembro.")
    @app_commands.describe(target_member="Usuario que recibe el dinero", amount="Monto a dar")
    async def give_prefix(self, ctx: commands.Context, target_member: discord.Member, amount: int):
        if target_member == ctx.author:
            await ctx.send("❌ No puedes transferir fondos a ti mismo.")
            return
        if amount <= 0:
            await ctx.send("❌ La cantidad debe ser superior a cero.")
            return
            
        sender_data = self.get_user_data(ctx.author.id)
        if sender_data["wallet"] < amount:
            await ctx.send("❌ No cuentas con suficiente efectivo disponible en tu billetera.")
            return
            
        self.update_balances(ctx.author.id, wallet_change=-amount, bank_change=0, reason="Transferencia", details=f"Transferiste ${amount:,} a {target_member.name}.", actor_id=ctx.author.id, target_user_id=target_member.id)
        self.update_balances(target_member.id, wallet_change=amount, bank_change=0, reason="Transferencia", details=f"Recibiste ${amount:,} de {ctx.author.name}.", actor_id=ctx.author.id, target_user_id=target_member.id)
        await ctx.send(f"🤝 Has transferido **${amount:,}** a la billetera de {target_member.mention}.")

    @commands.hybrid_command(name="crypto", description="Invierte en un broker simulado de criptomonedas.")
    @app_commands.describe(investment="Cantidad de efectivo que quieres arriesgar.")
    async def crypto_prefix(self, ctx: commands.Context, investment: int = 500):
        user_id = ctx.author.id
        user_data = self.get_user_data(user_id)
        
        if investment <= 0:
            await ctx.send("❌ La cantidad a invertir debe ser mayor a cero.")
            return
        if user_data["wallet"] < investment:
            await ctx.send("❌ No tienes suficiente dinero en tu billetera.")
            return
            
        trend_positive = random.choice([True, False])
        trend_text = "📈 POSITIVA" if trend_positive else "📉 NEGATIVA"
        
        embed = discord.Embed(title="🛸 Broker de Criptomonedas", description=f"Análisis en tiempo real:\nTendencia estimada: **{trend_text}**\nMonto en juego: **${investment:,}**\n\n¿Qué acción deseas ejecutar?", color=discord.Color.purple())
        
        view = CryptoView(self, user_id, investment, trend_positive)
        await ctx.send(embed=embed, view=view)

    @commands.hybrid_command(name="collect", description="Cobra el dinero pasivo acumulado por tus negocios e inversiones.")
    async def collect_prefix(self, ctx: commands.Context):
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

        embed = discord.Embed(
            title="✅ Cobro completado",
            description="Tu dinero pasivo ha sido transferido a tus cuentas.",
            color=discord.Color.green()
        )
        embed.add_field(name="💵 Billetera", value=f"${wallet_collected:,}", inline=True)
        embed.add_field(name="🏦 Banco", value=f"${bank_collected:,}", inline=True)
        embed.timestamp = datetime.datetime.now(datetime.timezone.utc)
        await ctx.send(embed=embed)

    @commands.hybrid_command(name="collectiontime", description="Muestra cuándo y cuánto generarán tus activos AFK.")
    async def collection_time_prefix(self, ctx: commands.Context):
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
            summary_lines.append(f"👥 Empleados legales: entre **${min_income:,}** y **${max_income:,}** por ciclo")

        if data["robbery_employees"] > 0:
            summary_lines.append(f"🥷 Empleados de robo: ingresos variables, con posibilidad de generar efectivo adicional por ciclo")

        if data["mega_companies"]:
            mega_total = len(data["mega_companies"])
            summary_lines.append(f"🏛️ Megacorporaciones: **${mega_total * 5000:,}** a **${mega_total * 15000:,}** por ciclo")

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
        embed.add_field(name="💸 Pendiente por cobrar", value=f"💵 ${data['pending_wallet']:,} / 🏦 ${data['pending_bank']:,}", inline=False)
        embed.timestamp = datetime.datetime.now(datetime.timezone.utc)
        await ctx.send(embed=embed)

    @commands.hybrid_command(name="pay", description="Paga parte o la totalidad de tu multa pendiente.")
    @app_commands.describe(amount_input="Cantidad a pagar o escribe 'all'.")
    async def pay_prefix(self, ctx: commands.Context, amount_input: str = "all"):
        user_id = ctx.author.id
        data = self.get_user_data(user_id)
        if data["fine"] <= 0:
            await ctx.send("✅ No tienes multas pendientes.")
            return

        if amount_input.lower() == "all":
            amount = data["fine"]
        else:
            try:
                amount = int(amount_input)
            except ValueError:
                await ctx.send("❌ Cantidad inválida.")
                return

        if amount <= 0:
            await ctx.send("❌ La cantidad debe ser mayor a cero.")
            return

        paid = self.pay_fine(user_id, amount)
        if paid <= 0:
            await ctx.send("❌ No tienes fondos suficientes para pagar esa cantidad.")
            return

        remaining = max(0, data["fine"] - paid)
        embed = discord.Embed(title="💳 Pago de multa", description=f"Pagaste **${paid:,}** de tu multa pendiente.", color=discord.Color.green())
        embed.add_field(name="Restante", value=f"${remaining:,}", inline=False)
        await ctx.send(embed=embed)

    @commands.hybrid_command(name="daily", description="Reclama tu recompensa financiera diaria.")
    async def daily_prefix(self, ctx: commands.Context):
        user_id = ctx.author.id
        user_data = self.get_user_data(user_id)
        current_date_str = datetime.date.today().isoformat()
        
        if user_data["last_daily"] == current_date_str:
            await ctx.send("❌ Ya has reclamado tu recompensa diaria hoy.")
            return
            
        daily_reward = 500
        self.update_balances(user_id, wallet_change=daily_reward, bank_change=0, reason="Daily", details=f"Reclamaste el bono diario de ${daily_reward:,}.")
        self.update_asset(user_id, "last_daily", current_date_str)
        await ctx.send(f"🎁 ¡Has reclamado tus **${daily_reward}** del bono diario!")

    @commands.hybrid_command(name="deposit", aliases=["dep"], description="Deposita dinero de tu billetera al banco.")
    @app_commands.describe(amount_input="Cantidad de dinero numérico o escribe 'all'.")
    async def deposit_prefix(self, ctx: commands.Context, amount_input: str):
        user_id = ctx.author.id
        user_data = self.get_user_data(user_id)
        wallet_balance = user_data["wallet"]
        
        if amount_input.lower() == "all":
            amount = wallet_balance
        else:
            try:
                amount = int(amount_input)
            except ValueError:
                await ctx.send("❌ Cantidad inválida.")
                return
                
        if amount <= 0 or amount > wallet_balance:
            await ctx.send("❌ Cantidad inválida o fondos insuficientes.")
            return
            
        self.update_balances(user_id, wallet_change=-amount, bank_change=amount, reason="Depósito", details=f"Depositaste ${amount:,} en el banco.")
        await ctx.send(f"🏦 Depositados **${amount:,}** en el banco.")

    @commands.hybrid_command(name="withdraw", aliases=["with"], description="Retira dinero de tu cuenta bancaria a tu billetera.")
    @app_commands.describe(amount_input="Cantidad de dinero numérico o escribe 'all'.")
    async def withdraw_prefix(self, ctx: commands.Context, amount_input: str):
        user_id = ctx.author.id
        user_data = self.get_user_data(user_id)
        bank_balance = user_data["bank"]
        
        if amount_input.lower() == "all":
            amount = bank_balance
        else:
            try:
                amount = int(amount_input)
            except ValueError:
                await ctx.send("❌ Cantidad inválida.")
                return
                
        if amount <= 0 or amount > bank_balance:
            await ctx.send("❌ Cantidad inválida o fondos insuficientes.")
            return
            
        self.update_balances(user_id, wallet_change=amount, bank_change=-amount, reason="Retiro", details=f"Retiraste ${amount:,} del banco.")
        await ctx.send(f"💵 Retirados **${amount:,}** de tu cuenta bancaria.")

    # --- TOP COMMAND (LIMIT 100) ---
    @commands.hybrid_command(name="top", aliases=["leaderboard", "ricos"], description="Muestra la lista de los usuarios con más dinero.")
    async def top_rich(self, ctx: commands.Context):
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
            await ctx.send("🪙 La base de datos de economía está vacía actualmente.")
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
                
            leaderboard_text += f"{medal} **{name}** — ${total:,} *(💵 ${wallet:,} / 🏦 ${bank:,})*\n"

        embed.description += leaderboard_text
        
        embed.set_footer(
            text=f"Tu Posición: {author_rank} | Fortuna Actual: ${author_total:,}",
            icon_url=ctx.author.display_avatar.url
        )
        embed.timestamp = datetime.datetime.now(datetime.timezone.utc)

        await ctx.send(embed=embed)

# --- REQUIRED ENTRY POINT FOR DISCORD.PY EXTENSIONS ---
async def setup(bot):
    await bot.add_cog(Economy(bot))