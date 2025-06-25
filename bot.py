
import discord
from discord.ext import tasks, commands
from discord import app_commands
import asyncio
import datetime
import pytz
import io
import os
import json
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import firebase_admin
from firebase_admin import credentials, firestore
from typing import Optional, Dict, Any
from statistics import mean
from geopy.geocoders import Nominatim
from timezonefinder import TimezoneFinder
import random
import bot
from flask import Flask
from threading import Thread

# Keep-alive server
app = Flask(__name__)

@app.route('/')
def home():
    return "Levi is alive!"

def run():
    app.run(host='0.0.0.0', port=8080)

def keep_alive():
        app = Flask(__name__)

        @app.route('/')
        def home():
            return "Bot is alive!"

        # These specific settings force public exposure
        Thread(target=lambda: app.run(
            host='0.0.0.0',
            port=8080,
            debug=False,
            use_reloader=False
        )).start()

# --- ENVIRONMENT CONFIG ---
TOKEN = os.getenv("DISCORD_TOKEN")
if TOKEN is None:
    raise ValueError("DISCORD_TOKEN environment variable not set.")

GUILD_ID_STR = os.getenv("GUILD_ID")
if GUILD_ID_STR is None:
    raise ValueError("GUILD_ID environment variable not set.")
GUILD_ID = int(GUILD_ID_STR)

CHECKIN_CHANNEL_ID_STR = os.getenv("CHECKIN_CHANNEL_ID")
if CHECKIN_CHANNEL_ID_STR is None:
    raise ValueError("CHECKIN_CHANNEL_ID environment variable not set.")
CHECKIN_CHANNEL_ID = int(CHECKIN_CHANNEL_ID_STR)

REPORT_CHANNEL_ID_STR = os.getenv("REPORT_CHANNEL_ID")
if REPORT_CHANNEL_ID_STR is None:
    raise ValueError("REPORT_CHANNEL_ID environment variable not set.")
REPORT_CHANNEL_ID = int(REPORT_CHANNEL_ID_STR)

ADMIN_ROLE_NAME = os.getenv("ADMIN_ROLE_NAME")
if ADMIN_ROLE_NAME is None:
    raise ValueError("ADMIN_ROLE_NAME environment variable not set.")

# --- TIMEZONE CONFIG ---
EST = pytz.timezone("US/Eastern")
DAILY_CHECK_HOUR_EST = 20  # 8 PM

# --- FIREBASE ---
firebase_cred_str = os.getenv("FIREBASE_CRED")
if firebase_cred_str is None:
    raise ValueError("FIREBASE_CRED environment variable not set.")
firebase_key_dict = json.loads(firebase_cred_str)
cred = credentials.Certificate(firebase_key_dict)
firebase_admin.initialize_app(cred)
db = firestore.client()

# --- DISCORD BOT ---
intents = discord.Intents.default()
intents.members = True
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

pending_level_check: Dict[str, str] = {}  # Tracks DM reply states like "asked", "awaiting"
last_checkin_sent: Dict[str, str] = {}   # Tracks the date string (YYYY-MM-DD) when check-in was last sent

# Data for new features
DAILY_FACTS = [
    "🧠 Your brain uses about 20% of your body's total energy!",
    "🌟 Honey never spoils - archaeologists have found edible honey in ancient Egyptian tombs!",
    "🐙 Octopuses have three hearts and blue blood!",
    "🦋 A group of flamingos is called a 'flamboyance'!",
    "🌍 There are more possible games of chess than atoms in the observable universe!",
    "🐝 Bees can recognize human faces!",
    "🌙 A day on Venus is longer than a year on Venus!",
    "🦈 Sharks have been around longer than trees!",
    "🍌 Bananas are berries, but strawberries aren't!",
    "🐧 Penguins have knees - they're just hidden inside their bodies!"
]

MOTIVATIONAL_QUOTES = [
    "💪 'The only way to do great work is to love what you do.' - Steve Jobs",
    "🚀 'Success is not final, failure is not fatal: it is the courage to continue that counts.' - Winston Churchill",
    "⭐ 'Believe you can and you're halfway there.' - Theodore Roosevelt",
    "🌟 'The future belongs to those who believe in the beauty of their dreams.' - Eleanor Roosevelt",
    "🔥 'It is during our darkest moments that we must focus to see the light.' - Aristotle",
    "💎 'The only impossible journey is the one you never begin.' - Tony Robbins",
    "🎯 'In the middle of difficulty lies opportunity.' - Albert Einstein",
    "🌈 'What lies behind us and what lies before us are tiny matters compared to what lies within us.' - Ralph Waldo Emerson",
    "⚡ 'The way to get started is to quit talking and begin doing.' - Walt Disney",
    "🏆 'Don't watch the clock; do what it does. Keep going.' - Sam Levenson"
]

# --- ROLE CONFIGURATION ---
LEVEL_ROLES = {
    "800-1000": (800, 1000),
    "1000-2000": (1000, 2000),
    "2000-3000": (2000, 3000),
    "3000-4000": (3000, 4000),
    "4000-5000": (4000, 5000),
    "5000-6000": (5000, 6000),
    "6000-7000": (6000, 7000),
    "7000-8000": (7000, 8000),
    "8000-9000": (8000, 9000),
    "10K+": (10000, float('inf'))
}

# --- HELPERS ---
def get_today_date_str() -> str:
    now = datetime.datetime.now(EST)
    return now.strftime("%Y-%m-%d")

def get_week_dates() -> list[str]:
    now = datetime.datetime.now(EST)
    start = now - datetime.timedelta(days=now.weekday())
    return [(start + datetime.timedelta(days=i)).strftime("%Y-%m-%d") for i in range(7)]

def get_month_dates() -> list[str]:
    now = datetime.datetime.now(EST)
    start = now.replace(day=1)
    dates = []
    current = start
    while current.month == now.month:
        dates.append(current.strftime("%Y-%m-%d"))
        current += datetime.timedelta(days=1)
    return dates

def get_all_time_scores():
    docs = db.collection("level_progress").stream()
    scores = []
    for doc in docs:
        d = doc.to_dict() or {}
        username = d.get("username", "?")
        total = sum(v for v in d.get("entries", {}).values() if isinstance(v, int) and v >= 0)
        scores.append((username, total))
    return sorted(scores, key=lambda x: x[1], reverse=True)

def is_admin(member: discord.Member) -> bool:
    return any(role.name == ADMIN_ROLE_NAME for role in member.roles)

# Helper to get user timezone or default EST
async def get_user_timezone(user_id: str) -> pytz.timezone:
    doc = db.collection("user_prefs").document(user_id).get()
    if doc.exists:
        data = doc.to_dict()
        tz_name = data.get("timezone")
        if tz_name:
            try:
                return pytz.timezone(tz_name)
            except Exception:
                pass
    return EST

# Helper to fetch user entries as a dict[str, int]
async def get_user_entries(user_id: str) -> dict[str, int]:
    doc = db.collection("level_progress").document(user_id).get()
    data = doc.to_dict() or {}
    entries = data.get("entries", {})
    return {k: v for k, v in entries.items() if isinstance(v, int) and v >= 0}

# Helper to get opt-in status (default True)
async def get_opt_in_status(user_id: str) -> bool:
    doc = db.collection("user_prefs").document(user_id).get()
    data = doc.to_dict()
    if data is None:
        return True
    return data.get("opt_in", True)

# Helper to set opt-in status
async def set_opt_in_status(user_id: str, value: bool):
    db.collection("user_prefs").document(user_id).set({"opt_in": value})

# Helper to add warning
async def add_warning(user_id: str, username: str, reason: str, admin_id: str):
    warning_data = {
        "reason": reason,
        "timestamp": datetime.datetime.now(EST).isoformat(),
        "admin_id": admin_id
    }
    doc_ref = db.collection("warnings").document(user_id)
    doc = doc_ref.get()
    if doc.exists:
        warnings = doc.to_dict().get("warnings", [])
    else:
        warnings = []
    warnings.append(warning_data)
    doc_ref.set({"username": username, "warnings": warnings})

# Helper to get warnings
async def get_warnings(user_id: str):
    doc = db.collection("warnings").document(user_id).get()
    if doc.exists:
        return doc.to_dict().get("warnings", [])
    return []

# Helper to clear warnings
async def clear_warnings(user_id: str):
    db.collection("warnings").document(user_id).delete()

# Helper to get user's current total level
async def get_user_total_level(user_id: str) -> int:
    entries = await get_user_entries(user_id)
    if not entries:
        return 0
    return max(entries.values())

# Helper to determine role based on level
def get_role_for_level(level: int) -> str:
    for role_name, (min_level, max_level) in LEVEL_ROLES.items():
        if min_level <= level < max_level:
            return role_name
    return None

# Helper to assign role to user
async def assign_level_role(member: discord.Member, level: int):
    try:
        guild = member.guild
        current_level_roles = []
        
        # Find all level roles the user currently has
        for role in member.roles:
            if role.name in LEVEL_ROLES.keys():
                current_level_roles.append(role)
        
        # Determine what role they should have
        target_role_name = get_role_for_level(level)
        target_role = None
        
        if target_role_name:
            # Find or create the target role
            target_role = discord.utils.get(guild.roles, name=target_role_name)
            if not target_role:
                try:
                    target_role = await guild.create_role(name=target_role_name, reason="Level-based role")
                    print(f"Created new role: {target_role_name}")
                except Exception as e:
                    print(f"Failed to create role {target_role_name}: {e}")
                    return
        
        # Remove old level roles
        for role in current_level_roles:
            if role != target_role:
                try:
                    await member.remove_roles(role, reason="Level changed")
                    print(f"Removed role {role.name} from {member.name}")
                except Exception as e:
                    print(f"Failed to remove role {role.name} from {member.name}: {e}")
        
        # Add new role if applicable
        if target_role and target_role not in member.roles:
            try:
                await member.add_roles(target_role, reason="Level-based role assignment")
                print(f"Assigned role {target_role.name} to {member.name}")
            except Exception as e:
                print(f"Failed to assign role {target_role.name} to {member.name}: {e}")
                
    except Exception as e:
        print(f"Error in role assignment for {member.name}: {e}")

# --- SAVE PROGRESS ---
async def save_level_entry(user_id: str, username: str, level: Optional[int]):
    ref = db.collection("level_progress").document(user_id)
    snapshot = ref.get()
    raw = snapshot.to_dict() or {}
    entries = raw.get("entries", {})
    entries[get_today_date_str()] = level if level is not None else -1
    ref.set({"username": username, "entries": entries})
    
    # Trigger role assignment if level is provided
    if level is not None and level > 0:
        guild = bot.get_guild(GUILD_ID)
        if guild:
            try:
                member = guild.get_member(int(user_id))
                if member:
                    await assign_level_role(member, level)
            except Exception as e:
                print(f"Failed to assign role after level entry: {e}")

# --- SEND DM CHECK-IN ---
async def send_checkin(user: discord.User):
    try:
        await user.send("🧠 Did your level increase today? Reply with `yes` or `no`.")
    except Exception as e:
        print(f"DM error: {e}")

# --- ADMIN CHECK DECORATOR ---
def is_admin_role():
    async def predicate(interaction: discord.Interaction):
        member = interaction.guild.get_member(interaction.user.id) or await interaction.guild.fetch_member(interaction.user.id)
        return any(role.name == ADMIN_ROLE_NAME for role in member.roles)
    return app_commands.check(predicate)

# --- READY ---
@bot.event
async def on_ready():
    print(f"✅ Logged in as {bot.user}")
    try:
        # Try guild-specific sync first
        synced = await bot.tree.sync(guild=discord.Object(id=GUILD_ID))
        print(f"🔁 Synced {len(synced)} commands to guild {GUILD_ID}")
        
        # If no commands synced to guild, try global sync as fallback
        if len(synced) == 0:
            print("⚠️ No commands synced to guild, trying global sync...")
            global_synced = await bot.tree.sync()
            print(f"🌍 Synced {len(global_synced)} commands globally")
            
    except Exception as e:
        print(f"❌ Sync failed: {e}")
        # Try global sync as fallback
        try:
            global_synced = await bot.tree.sync()
            print(f"🌍 Fallback: Synced {len(global_synced)} commands globally")
        except Exception as e2:
            print(f"❌ Global sync also failed: {e2}")
    
    daily_checkin_task.start()
    weekly_report_task.start()

# --- MESSAGE HANDLER ---
@bot.event
async def on_message(message: discord.Message):
    if message.author == bot.user:
        return
    if isinstance(message.channel, discord.DMChannel):
        uid = str(message.author.id)
        text = message.content.strip().lower()
        if uid in pending_level_check:
            state = pending_level_check[uid]
            if state == "asked":
                if text in ["yes", "y"]:
                    pending_level_check[uid] = "awaiting"
                    await message.channel.send("📈 What level are you at now?")
                elif text in ["no", "n"]:
                    await save_level_entry(uid, message.author.name, None)
                    pending_level_check.pop(uid)
                    await message.channel.send("👍 Got it! No level today.")
            elif state == "awaiting":
                if text.isdigit():
                    await save_level_entry(uid, message.author.name, int(text))
                    pending_level_check.pop(uid)
                    await message.channel.send(f"✅ Saved level {text} for today!")
                else:
                    await message.channel.send("❌ Please enter a number.")
    await bot.process_commands(message)

# --- DAILY CHECK-IN LOOP ---
@tasks.loop(minutes=10)
async def daily_checkin_task():
    await bot.wait_until_ready()
    guild = bot.get_guild(GUILD_ID)
    if not guild:
        print("⚠️ Guild not found.")
        return

    now_utc = datetime.datetime.utcnow().replace(tzinfo=pytz.utc)
    today_str = get_today_date_str()

    for member in guild.members:
        if member.bot:
            continue

        user_id = str(member.id)
        
        # Check if user opted out
        opt_in = await get_opt_in_status(user_id)
        if not opt_in:
            continue
            
        user_tz = await get_user_timezone(user_id)
        now_user = now_utc.astimezone(user_tz)

        # Check if current hour matches daily check hour, and minute is within first 10 min
        if now_user.hour == DAILY_CHECK_HOUR_EST and now_user.minute < 10:
            if last_checkin_sent.get(user_id) == today_str:
                continue  # Already sent today

            last_checkin_sent[user_id] = today_str

            # Only send if user didn't already have pending check-in unanswered
            if user_id not in pending_level_check:
                try:
                    pending_level_check[user_id] = "asked"
                    await send_checkin(member)
                    print(f"Sent daily check-in to {member.name} at {now_user.isoformat()}")
                except Exception as e:
                    print(f"Failed to send DM to {member.name}: {e}")

# --- WEEKLY REPORT LOOP ---
@tasks.loop(hours=168)
async def weekly_report_task():
    await bot.wait_until_ready()
    channel = bot.get_channel(REPORT_CHANNEL_ID)
    if not isinstance(channel, discord.abc.Messageable):
        print("⚠️ Report channel not messageable.")
        return
    
    docs = db.collection("level_progress").stream()
    dates = get_week_dates()
    user_data = {}
    weekly_gains = {}
    
    for doc in docs:
        d = doc.to_dict() or {}
        username = d.get("username", "?")
        entries = d.get("entries", {})
        values = [entries.get(day, None if day not in entries else -1) for day in dates]
        clean_values = [v if isinstance(v, int) and v >= 0 else None for v in values]
        user_data[username] = clean_values
        
        # Calculate weekly gain
        valid_values = [v for v in clean_values if v is not None]
        if len(valid_values) >= 2:
            weekly_gains[username] = max(valid_values) - min(valid_values)
        elif len(valid_values) == 1:
            weekly_gains[username] = valid_values[0]
        else:
            weekly_gains[username] = 0
    
    if not user_data:
        return
    
    # Create graph
    plt.figure(figsize=(12, 8))
    for user, values in user_data.items():
        plt.plot(dates, values, marker='o', label=user, linewidth=2)
    plt.title("📈 Weekly Level Progress", fontsize=16)
    plt.xlabel("Date", fontsize=12)
    plt.ylabel("Level", fontsize=12)
    plt.grid(True, alpha=0.3)
    plt.legend(bbox_to_anchor=(1.05, 1), loc='upper left')
    plt.tight_layout()
    
    buf = io.BytesIO()
    plt.savefig(buf, format="png", dpi=150, bbox_inches='tight')
    buf.seek(0)
    
    # Get current and previous week leaderboards
    current_scores = get_all_time_scores()[:10]
    
    # Most improved users
    top_improved = sorted(weekly_gains.items(), key=lambda x: x[1], reverse=True)[:5]
    
    # Create report text
    report_text = "📊 **Weekly Progress Report**\n\n"
    
    if top_improved and top_improved[0][1] > 0:
        report_text += "🚀 **Most Improved This Week:**\n"
        for i, (user, gain) in enumerate(top_improved, 1):
            if gain > 0:
                report_text += f"`{i}.` **{user}** — +{gain} levels\n"
        report_text += "\n"
    
    report_text += "🏆 **Current Top 5:**\n"
    for i, (user, total) in enumerate(current_scores[:5], 1):
        report_text += f"`{i}.` **{user}** — {total} total levels\n"
    
    await channel.send(report_text, file=discord.File(buf, filename="weekly_progress.png"))

# --- COMMANDS ---
@bot.tree.command(name="ping", description="Check bot latency")
async def ping(interaction: discord.Interaction):
    await interaction.response.send_message(f"🏓 Pong! `{bot.latency * 1000:.2f}ms`")

@bot.tree.command(name="help", description="List of available commands")
async def help_cmd(interaction: discord.Interaction):
    text = (
        "**🔹 General Commands:**\n"
        "/ping — Check bot latency\n"
        "/help — Show this help message\n"
        "/myprogress — Weekly progress graph\n"
        "/mystats — Show your stats and streak\n"
        "/myrank — See your leaderboard rank\n"
        "/leaderboard — Top 10 levelers (with filters)\n"
        "/levelof [user] — View latest level\n"
        "/checkin — DM check-in prompt\n"
        "/nextcheckin — When next check-in is scheduled\n"
        "/optin — Enable daily DM check-ins\n"
        "/optout — Disable daily DM check-ins\n"
        "/settimezone — Set your timezone\n"
        "/dailyfact — Get a random fun fact\n"
        "/motivation — Get a motivational quote\n"
        "\n**🔸 Admin Commands:**\n"
        "/setlevel — Set user's level\n"
        "/resetuser — Reset user's data\n"
        "/announce — Post announcement\n"
        "/forcesync — Sync commands\n"
        "/warnings — Log user warnings\n"
        "/viewwarnings — View user warnings\n"
        "/clearwarnings — Clear user warnings\n"
        "/shoutout — Give user shoutout\n"
        "/syncroles — Sync all user roles"
    )
    await interaction.response.send_message(text, ephemeral=True)

@bot.tree.command(name="myprogress", description="Show your weekly level graph")
async def myprogress(interaction: discord.Interaction):
    uid = str(interaction.user.id)
    doc = db.collection("level_progress").document(uid).get()
    data = doc.to_dict() or {}
    entries = data.get("entries", {})
    dates = get_week_dates()
    values = [entries.get(day, None if day not in entries else -1) for day in dates]
    clean = [v if isinstance(v, int) and v >= 0 else None for v in values]
    plt.figure(figsize=(8, 5))
    plt.plot(dates, clean, marker='o', label=interaction.user.name)
    plt.title(f"{interaction.user.name}'s Weekly Progress")
    plt.xlabel("Date")
    plt.ylabel("Level")
    plt.grid(True)
    plt.legend()
    plt.tight_layout()
    buf = io.BytesIO()
    plt.savefig(buf, format="png")
    buf.seek(0)
    await interaction.response.send_message("📊 Sent you a DM with your progress!", ephemeral=True)
    await interaction.user.send(file=discord.File(buf, filename="my_progress.png"))

@bot.tree.command(name="mystats", description="Show all your level check-ins, streak, and average")
async def mystats(interaction: discord.Interaction):
    user_id = str(interaction.user.id)
    entries = await get_user_entries(user_id)
    if not entries:
        await interaction.response.send_message("No level check-ins recorded yet.", ephemeral=True)
        return

    # Sort dates ascending
    sorted_dates = sorted(entries.keys())
    levels = [entries[d] for d in sorted_dates]

    # Calculate streak (consecutive days with check-in)
    from datetime import datetime, timedelta
    dates_dt = [datetime.strptime(d, "%Y-%m-%d") for d in sorted_dates]
    streak = 1
    for i in range(len(dates_dt) - 1, 0, -1):
        if (dates_dt[i] - dates_dt[i-1]) == timedelta(days=1):
            streak += 1
        else:
            break

    avg_level = mean(levels)
    text = (
        f"📊 **Your Level Stats**\n"
        f"Check-ins: {len(levels)}\n"
        f"Current streak: {streak} day(s)\n"
        f"Average level: {avg_level:.2f}\n"
        f"Latest level: {levels[-1]} on {sorted_dates[-1]}"
    )
    await interaction.response.send_message(text, ephemeral=True)

@bot.tree.command(name="myrank", description="See your rank on the leaderboard")
async def myrank(interaction: discord.Interaction):
    docs = db.collection("level_progress").stream()
    scores = []
    for doc in docs:
        d = doc.to_dict() or {}
        username = d.get("username", "?")
        total = sum(v for v in d.get("entries", {}).values() if isinstance(v, int) and v >= 0)
        scores.append((doc.id, username, total))
    scores.sort(key=lambda x: x[2], reverse=True)

    user_id = str(interaction.user.id)
    rank = next((i + 1 for i, (uid, _, _) in enumerate(scores) if uid == user_id), None)
    if rank is None:
        await interaction.response.send_message("You have no recorded levels yet.", ephemeral=True)
        return
    total_score = scores[rank - 1][2]
    await interaction.response.send_message(f"🏅 Your rank is #{rank} with a total of {total_score} levels.", ephemeral=True)

@bot.tree.command(name="nextcheckin", description="Tells you when the next check-in is scheduled")
async def nextcheckin(interaction: discord.Interaction):
    from datetime import datetime, timedelta

    user_id = str(interaction.user.id)
    tz = await get_user_timezone(user_id)

    now = datetime.now(tz)
    next_checkin = now.replace(hour=DAILY_CHECK_HOUR_EST, minute=0, second=0, microsecond=0)
    if now >= next_checkin:
        next_checkin += timedelta(days=1)

    formatted = next_checkin.strftime("%Y-%m-%d %H:%M %Z")
    await interaction.response.send_message(f"⏰ Next daily check-in is scheduled at {formatted}.", ephemeral=True)

@bot.tree.command(name="optin", description="Enable daily DM level check-ins")
async def optin(interaction: discord.Interaction):
    await set_opt_in_status(str(interaction.user.id), True)
    await interaction.response.send_message("✅ You have opted in for daily DM check-ins.", ephemeral=True)

@bot.tree.command(name="optout", description="Disable daily DM level check-ins")
async def optout(interaction: discord.Interaction):
    await set_opt_in_status(str(interaction.user.id), False)
    await interaction.response.send_message("✅ You have opted out of daily DM check-ins.", ephemeral=True)



geolocator = Nominatim(user_agent="level-bot")
tzfinder = TimezoneFinder()

@bot.tree.command(name="settimezone", description="Set your timezone using your city name (e.g. London, Mumbai)")
@app_commands.describe(city="Your city name")
async def settimezone(interaction: discord.Interaction, city: str):
    await interaction.response.defer(ephemeral=True)

    try:
        location = geolocator.geocode(city)
        if not location:
            await interaction.followup.send("❌ Could not find that city. Try a more specific name.", ephemeral=True)
            return

        timezone = tzfinder.timezone_at(lat=location.latitude, lng=location.longitude)
        if timezone not in pytz.all_timezones:
            await interaction.followup.send("❌ Found coordinates, but couldn't determine a valid timezone.", ephemeral=True)
            return

        db.collection("user_prefs").document(str(interaction.user.id)).set({"timezone": timezone}, merge=True)
        await interaction.followup.send(f"✅ Timezone set to `{timezone}` based on `{location.address}`", ephemeral=True)

    except Exception as e:
        print(f"[TZ SET ERROR] {e}")
        await interaction.followup.send("⚠️ An error occurred while detecting timezone.", ephemeral=True)

@bot.tree.command(name="dailyfact", description="Get a random fun fact")
async def dailyfact(interaction: discord.Interaction):
    fact = random.choice(DAILY_FACTS)
    await interaction.response.send_message(fact)

@bot.tree.command(name="motivation", description="Get a motivational quote")
async def motivation(interaction: discord.Interaction):
    quote = random.choice(MOTIVATIONAL_QUOTES)
    await interaction.response.send_message(quote)

@bot.tree.command(name="leaderboard", description="Show leaderboard with optional filters")
@app_commands.describe(filter="Choose time period: week, month, or alltime")
@app_commands.choices(filter=[
    app_commands.Choice(name="This Week", value="week"),
    app_commands.Choice(name="This Month", value="month"), 
    app_commands.Choice(name="All Time", value="alltime")
])
async def leaderboard(interaction: discord.Interaction, filter: str = "alltime"):
    docs = db.collection("level_progress").stream()
    scores = []
    
    if filter == "week":
        dates = get_week_dates()
        title = "🏆 Weekly Leaderboard"
    elif filter == "month":
        dates = get_month_dates()
        title = "🏆 Monthly Leaderboard"
    else:
        dates = None
        title = "🏆 All-Time Leaderboard"
    
    for doc in docs:
        d = doc.to_dict() or {}
        username = d.get("username", "?")
        entries = d.get("entries", {})
        
        if dates:  # Week or month filter
            total = sum(v for k, v in entries.items() if k in dates and isinstance(v, int) and v >= 0)
        else:  # All time
            total = sum(v for v in entries.values() if isinstance(v, int) and v >= 0)
        
        if total > 0:  # Only include users with progress in the time period
            scores.append((username, total))
    
    scores.sort(key=lambda x: x[1], reverse=True)
    if not scores:
        await interaction.response.send_message(f"📭 No data found for {filter} period.")
        return
    
    text = f"**{title}:**\n"
    for i, (name, score) in enumerate(scores[:10], 1):
        text += f"`{i:>2}`. **{name}** — `{score}` levels\n"
    await interaction.response.send_message(text)

@bot.tree.command(name="levelof", description="Show user's latest level")
@app_commands.describe(user="User to check")
async def levelof(interaction: discord.Interaction, user: discord.Member):
    doc = db.collection("level_progress").document(str(user.id)).get()
    data = doc.to_dict()
    if not data or "entries" not in data:
        await interaction.response.send_message("No data found.", ephemeral=True)
        return
    entries = data["entries"]
    valid = {k: v for k, v in entries.items() if isinstance(v, int) and v >= 0}
    if not valid:
        await interaction.response.send_message("No valid entries.", ephemeral=True)
        return
    latest = max(valid.items(), key=lambda x: x[0])
    await interaction.response.send_message(f"{user.name}'s latest level is `{latest[1]}` on `{latest[0]}`.", ephemeral=True)

@bot.tree.command(name="checkin", description="Send yourself a level check-in")
async def checkin(interaction: discord.Interaction):
    uid = str(interaction.user.id)
    if uid in pending_level_check:
        await interaction.response.send_message("Check-in already sent. Respond to the DM.", ephemeral=True)
    else:
        pending_level_check[uid] = "asked"
        await send_checkin(interaction.user)
        await interaction.response.send_message("📩 Check your DMs!", ephemeral=True)

# --- ADMIN COMMANDS ---
@bot.tree.command(name="forcesync", description="Admin: Force sync commands")
@is_admin_role()
async def forcesync(interaction: discord.Interaction):
    # Respond immediately to prevent timeout
    await interaction.response.send_message("🔄 Syncing commands...", ephemeral=True)
    
    try:
        synced = await bot.tree.sync(guild=discord.Object(id=GUILD_ID))
        await interaction.edit_original_response(content=f"✅ Synced {len(synced)} commands to guild!")
        
        # If no commands synced, try global
        if len(synced) == 0:
            global_synced = await bot.tree.sync()
            await interaction.edit_original_response(content=f"✅ Synced {len(global_synced)} commands globally!")
            
    except Exception as e:
        await interaction.edit_original_response(content="❌ Failed to sync commands.")
        print(f"[ERROR] {e}")

@bot.tree.command(name="setlevel", description="Admin: Set a user's level")
@is_admin_role()
@app_commands.describe(user="User to update", level="New level")
async def setlevel(interaction: discord.Interaction, user: discord.Member, level: int):
    await save_level_entry(str(user.id), user.name, level)
    await interaction.response.send_message(f"✅ Set {user.name}'s level to {level}.", ephemeral=True)

@bot.tree.command(name="resetuser", description="Admin: Reset all user progress")
@is_admin_role()
@app_commands.describe(user="User to reset")
async def resetuser(interaction: discord.Interaction, user: discord.Member):
    db.collection("level_progress").document(str(user.id)).delete()
    await interaction.response.send_message(f"🗑️ Cleared all data for {user.name}.", ephemeral=True)

@bot.tree.command(name="announce", description="Admin: Send announcement to check-in channel")
@is_admin_role()
@app_commands.describe(message="Message to send")
async def announce(interaction: discord.Interaction, message: str):
    channel = bot.get_channel(CHECKIN_CHANNEL_ID)
    if channel and isinstance(channel, discord.abc.Messageable):
        await channel.send(f"📢 **{interaction.user.mention} says:**\n{message}")
        await interaction.response.send_message("✅ Sent.", ephemeral=True)
    else:
        await interaction.response.send_message("❌ Channel not found.", ephemeral=True)

@bot.tree.command(name="warnings", description="Admin: Log a warning for a user")
@is_admin_role()
@app_commands.describe(user="User to warn", reason="Reason for warning")
async def warnings(interaction: discord.Interaction, user: discord.Member, reason: str):
    await add_warning(str(user.id), user.name, reason, str(interaction.user.id))
    await interaction.response.send_message(f"⚠️ Warning logged for {user.mention}: {reason}", ephemeral=True)

@bot.tree.command(name="viewwarnings", description="Admin: View warnings for a user")
@is_admin_role()
@app_commands.describe(user="User to check warnings for")
async def viewwarnings(interaction: discord.Interaction, user: discord.Member):
    warnings = await get_warnings(str(user.id))
    if not warnings:
        await interaction.response.send_message(f"✅ {user.mention} has no warnings.", ephemeral=True)
        return
    
    text = f"⚠️ **Warnings for {user.mention}:**\n"
    for i, warning in enumerate(warnings, 1):
        timestamp = warning.get("timestamp", "Unknown")
        reason = warning.get("reason", "No reason provided")
        text += f"`{i}.` {timestamp[:10]} - {reason}\n"
    
    await interaction.response.send_message(text, ephemeral=True)

@bot.tree.command(name="clearwarnings", description="Admin: Clear all warnings for a user")
@is_admin_role()
@app_commands.describe(user="User to clear warnings for")
async def clearwarnings(interaction: discord.Interaction, user: discord.Member):
    await clear_warnings(str(user.id))
    await interaction.response.send_message(f"✅ Cleared all warnings for {user.mention}.", ephemeral=True)

@bot.tree.command(name="shoutout", description="Admin: Give a shoutout to a user")
@is_admin_role()
@app_commands.describe(user="User to shoutout", message="Shoutout message")
async def shoutout(interaction: discord.Interaction, user: discord.Member, message: str):
    channel = bot.get_channel(CHECKIN_CHANNEL_ID)
    if channel and isinstance(channel, discord.abc.Messageable):
        shoutout_text = f"🌟 **SHOUTOUT** to {user.mention}! 🌟\n{message}\n\n— {interaction.user.mention}"
        await channel.send(shoutout_text)
        await interaction.response.send_message(f"✅ Shoutout sent for {user.mention}!", ephemeral=True)
    else:
        await interaction.response.send_message("❌ Channel not found.", ephemeral=True)

@bot.tree.command(name="syncroles", description="Admin: Sync all user roles based on their current levels")
@is_admin_role()
async def syncroles(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)

    guild = bot.get_guild(GUILD_ID)
    if not guild:
        await interaction.followup.send("❌ Guild not found.", ephemeral=True)
        return

    # Check bot permissions
    if not guild.me.guild_permissions.manage_roles:
        await interaction.followup.send("❌ I need the 'Manage Roles' permission.", ephemeral=True)
        return

    updated_count = 0
    failed_count = 0
    docs = db.collection("level_progress").stream()

    report_lines = ["**Role Sync Report**"]

    for doc in docs:
        try:
            user_id = doc.id
            data = doc.to_dict() or {}
            entries = data.get("entries", {})
            username = data.get("username", "Unknown")

            # Get highest level
            valid_levels = [v for k, v in entries.items() 
                          if isinstance(v, int) and v >= 0 and k != "username"]

            if not valid_levels:
                report_lines.append(f"⚠️ {username}: No valid level entries")
                failed_count += 1
                continue

            highest_level = max(valid_levels)
            member = guild.get_member(int(user_id))

            if not member:
                report_lines.append(f"⚠️ {username}: Not in server")
                failed_count += 1
                continue

            # Get current level roles to remove
            current_roles = [role for role in member.roles if role.name in LEVEL_ROLES]

            # Get target role
            target_role_name = get_role_for_level(highest_level)
            if not target_role_name:
                report_lines.append(f"⚠️ {username}: No role for level {highest_level}")
                failed_count += 1
                continue

            target_role = discord.utils.get(guild.roles, name=target_role_name)
            if not target_role:
                try:
                    target_role = await guild.create_role(
                        name=target_role_name,
                        reason="Auto-created by level sync"
                    )
                    report_lines.append(f"➕ Created new role: {target_role_name}")
                except Exception as e:
                    report_lines.append(f"❌ Failed to create role {target_role_name}: {str(e)}")
                    failed_count += 1
                    continue

            # Skip if already has the correct role
            if target_role in member.roles and not current_roles:
                report_lines.append(f"✅ {member.display_name}: Already correct ({target_role_name})")
                continue

            try:
                # Remove old roles
                if current_roles:
                    await member.remove_roles(*current_roles, reason="Level sync")
                    removed_names = ", ".join(r.name for r in current_roles)
                    report_lines.append(f"➖ {member.display_name}: Removed {removed_names}")

                # Add new role
                await member.add_roles(target_role, reason=f"Level sync: {highest_level}")
                report_lines.append(f"➕ {member.display_name}: Added {target_role_name} (Level {highest_level})")
                updated_count += 1

            except discord.Forbidden:
                report_lines.append(f"❌ {member.display_name}: Missing permissions")
                failed_count += 1
            except discord.HTTPException as e:
                report_lines.append(f"❌ {member.display_name}: Error: {str(e)}")
                failed_count += 1

        except Exception as e:
            report_lines.append(f"❌ Error processing user {doc.id}: {str(e)}")
            failed_count += 1

    # Send the report
    summary = (
        f"**Sync completed**\n"
        f"✅ Updated: {updated_count}\n"
        f"⚠️ Failed: {failed_count}\n"
        f"📄 Details below:"
    )

    # Split report into chunks to avoid message length limits
    chunk_size = 15
    report_chunks = [report_lines[i:i + chunk_size] 
                    for i in range(0, len(report_lines), chunk_size)]

    await interaction.followup.send(summary, ephemeral=True)

    for chunk in report_chunks:
        await interaction.followup.send("\n".join(chunk), ephemeral=True)     

# --- ERROR HANDLER ---
@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error):
    print(f"[SLASH ERROR] {error}")
    try:
        if isinstance(error, app_commands.errors.MissingRole):
            await interaction.response.send_message("🚫 You don't have permission.", ephemeral=True)
        else:
            await interaction.response.send_message("⚠️ An error occurred.", ephemeral=True)
    except Exception as e:
        print(f"[RESPONSE ERROR] {e}")

# --- START ---
bot.run(TOKEN)
