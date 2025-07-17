import os
import json
import logging
import asyncio
import difflib
import time
from datetime import datetime, timedelta
from collections import defaultdict

import pytz
from aiohttp import ClientSession, ClientTimeout
from bs4 import BeautifulSoup
from dotenv import load_dotenv

import discord
from discord.ext import commands
from discord import app_commands

# ---------------------------
# Config & Logging
# ---------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S"
)
load_dotenv("code.env")

TOKEN = os.getenv("TOKEN")
ADMIN_ROLE_IDS = [int(x) for x in os.getenv("ADMIN_ROLE_IDS", "").split(",") if x]
OWNER_ID = os.getenv("OWNER_ID", "")  # For error pings

SUBS_FILE = "subscriptions.json"
EST = pytz.timezone("US/Eastern")

# ---------------------------
# Bot Setup
# ---------------------------
intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True

bot = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree

# Shared state
bot.subscriptions = {}
bot.known_items = set()
bot.current_interval = 300  # 5 minutes
bot.poll_task = None
bot.last_snapshot = {}
bot.autoscrape_enabled = True

# ---------------------------
# Helper Functions
# ---------------------------
def load_subs():
    if not os.path.exists(SUBS_FILE):
        return {}
    with open(SUBS_FILE) as f:
        return json.load(f)

def save_subs(subs):
    with open(SUBS_FILE, "w") as f:
        json.dump(subs, f, indent=2)

async def get_garden_channel(guild: discord.Guild = None):
    """Finds #growagarden channel in the specified guild or any guild."""
    if guild:
        channel = discord.utils.get(guild.text_channels, name="growagarden")
        if channel:
            return channel
    
    for guild in bot.guilds:
        channel = discord.utils.get(guild.text_channels, name="growagarden")
        if channel:
            return channel
    
    logging.error("#growagarden channel not found in any guild!")
    return None

def is_admin(interaction: discord.Interaction):
    """Check if user has admin role or permissions."""
    if any(role.id in ADMIN_ROLE_IDS for role in interaction.user.roles):
        return True
    return interaction.user.guild_permissions.administrator

def calculate_next_scrape_time(now: datetime) -> datetime:
    """Calculate next scrape time at 1 minute past the 5-minute interval"""
    # Get current minute and calculate next target minute (1,6,11...56)
    current_minute = now.minute
    target_minute = ((current_minute // 5) * 5) + 1
    if target_minute <= current_minute:
        target_minute += 5
    if target_minute >= 60:
        target_minute -= 60
        next_hour = now.hour + 1
        if next_hour == 24:
            next_hour = 0
    else:
        next_hour = now.hour
    
    # Create target datetime
    target_time = now.replace(
        hour=next_hour,
        minute=target_minute,
        second=0,
        microsecond=0
    )
    return target_time

# ---------------------------
# Scraping Functions
# ---------------------------
async def scrape_garden_stock():
    url = "https://www.vulcanvalues.com/grow-a-garden/stock"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,*/*",
        "Accept-Language": "en-US",
        "Referer": "https://www.vulcanvalues.com/"
    }

    try:
        timeout = ClientTimeout(total=15)
        async with ClientSession(headers=headers, timeout=timeout) as session:
            async with session.get(url) as resp:
                logging.info(f"GET {url} → {resp.status}")
                if resp.status == 520:
                    return None, "Cloudflare 520 Error"
                if resp.status != 200:
                    return None, f"HTTP Error {resp.status}"
                text = await resp.text()
    except asyncio.TimeoutError:
        return None, "Request Timeout"
    except Exception as e:
        logging.error(f"Network error: {str(e)}")
        return None, f"Network error: {str(e)}"

    try:
        soup = BeautifulSoup(text, "html.parser")
        data = defaultdict(list)
        
        # Handle seeds separately to ensure correct parsing
        seeds_header = soup.find("h2", string="SEEDS STOCK")
        if seeds_header:
            seeds_list = seeds_header.find_next_sibling("ul")
            if seeds_list:
                for li in seeds_list.find_all("li", class_="bg-gray-900"):
                    img = li.find("img")
                    span = li.find("span", class_="text-gray-400")
                    if img and span:
                        name = img.get("alt", "").strip()
                        qty = span.get_text(strip=True)
                        data["SEEDS STOCK"].append((name, qty))
        
        # Handle other categories
        for h2 in soup.select("h2.text-xl.font-bold.mb-2.text-center"):
            cat = h2.get_text(strip=True)
            if cat == "SEEDS STOCK":  # Already handled
                continue
                
            ul = h2.find_next_sibling("ul")
            if not ul:
                continue
            for li in ul.select("li.bg-gray-900"):
                img = li.find("img")
                span = li.select_one("span.text-gray-400")
                if img and span:
                    name = img["alt"].strip()
                    qty = span.get_text(strip=True)
                    data[cat].append((name, qty))
        
        # Handle eggs separately
        eggs_header = soup.find("h2", string="EGGS STOCK")
        if eggs_header:
            eggs_list = eggs_header.find_next_sibling("ul")
            if eggs_list:
                for li in eggs_list.find_all("li", class_="bg-gray-900"):
                    img = li.find("img")
                    span = li.find("span", class_="text-gray-400")
                    if img and span:
                        name = img.get("alt", "").strip()
                        qty = span.get_text(strip=True)
                        data["EGGS STOCK"].append((name, qty))
        
        return data, None
    except Exception as e:
        logging.error(f"Parse error: {str(e)}")
        return None, f"Parse error: {str(e)}"

async def scrape_with_retries(max_attempts=3, delay=4):
    """Scrape with retry mechanism"""
    attempts = 0
    error = None
    while attempts < max_attempts:
        attempts += 1
        stock, err = await scrape_garden_stock()
        if stock is not None:
            return stock, None
        error = err
        if attempts < max_attempts:
            logging.warning(f"Scrape failed (attempt {attempts}/{max_attempts}): {error}. Retrying in {delay}s...")
            await asyncio.sleep(delay)
    return None, error

def build_embed(stock, now):
    next_eta = now + timedelta(seconds=bot.current_interval)
    embed = discord.Embed(
        title="🌱 Grow A Garden Stock Update 🌱",
        color=0x2ecc71,
        timestamp=now
    )
    embed.set_footer(text=f"Scraped: {now:%H:%M:%S} EST | Next: {next_eta:%H:%M:%S} EST")

    mentions = defaultdict(list)

    for cat, items in stock.items():
        desc = "\n".join(f"{n}: {q}" for n, q in items) or "No items"
        embed.add_field(name=cat, value=desc, inline=False)

        prev = bot.last_snapshot.get(cat, [])
        if cat in ("GEAR STOCK", "SEEDS STOCK", "EGGS STOCK") or prev != items:
            for name, _ in items:
                for uid, user_items in bot.subscriptions.items():
                    if name.lower() in (i.lower() for i in user_items):
                        mentions[name].append(f"<@{uid}>")

        bot.last_snapshot[cat] = items

    return embed, mentions

# ---------------------------
# Background Task
# ---------------------------
async def polling_loop():
    await bot.wait_until_ready()
    logging.info("Polling loop started")
    
    while not bot.is_closed():
        try:
            # Skip if autoscrape is disabled
            if not bot.autoscrape_enabled:
                await asyncio.sleep(10)
                continue
                
            # Calculate next scrape time
            now = datetime.now(EST)
            next_time = calculate_next_scrape_time(now)
            wait_seconds = (next_time - now).total_seconds()
            
            # Wait until next scrape time
            if wait_seconds > 0:
                logging.info(f"Next scrape at {next_time.strftime('%H:%M:%S')} EST ({wait_seconds:.1f}s wait)")
                await asyncio.sleep(wait_seconds)
            
            # Perform scrape with retries
            now = datetime.now(EST)
            stock, error = await scrape_with_retries()
            channel = await get_garden_channel()
            
            if not channel:
                await asyncio.sleep(60)
                continue
            
            if stock is None:
                # Notify owner about persistent failure
                owner_mention = f"<@{OWNER_ID}>" if OWNER_ID else "@jaymaca"
                msg = f"{owner_mention} ⚠️ **CRITICAL ERROR**\nAll scrape attempts failed! Last error: {error}"
                await channel.send(msg)
                logging.error(f"All scrape attempts failed: {error}")
            else:
                # Process successful scrape
                bot.known_items = {n for items in stock.values() for n, _ in items}
                embed, mentions = build_embed(stock, now)
                
                # Send mentions as regular messages first
                if mentions:
                    alert_messages = []
                    for item, users in mentions.items():
                        if users:
                            alert_messages.append(f"{item}: {' '.join(users)}")
                    if alert_messages:
                        await channel.send("\n".join(alert_messages))
                
                # Then send the embed
                await channel.send(embed=embed)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logging.error(f"Polling error: {str(e)}")
            await asyncio.sleep(10)

# ---------------------------
# Bot Commands
# ---------------------------
@bot.hybrid_command(name="scrape", description="Manually fetch stock data")
async def manual_scrape(ctx: commands.Context):
    """Handles both !scrape and /scrape"""
    is_slash = ctx.interaction is not None
    if is_slash:
        await ctx.defer(ephemeral=True)
    else:
        await ctx.trigger_typing()

    channel = await get_garden_channel(ctx.guild)
    if not channel:
        return await ctx.send("❌ #growagarden channel not found!", ephemeral=True)

    now = datetime.now(EST)
    stock, error = await scrape_garden_stock()  # Single attempt for manual scrape

    if stock is None:
        await ctx.send(f"⚠️ Failed: {error}", ephemeral=True)
        await channel.send(f"⚠️ Manual scrape failed: {error}")
    else:
        bot.known_items = {n for items in stock.values() for n, _ in items}
        embed, mentions = build_embed(stock, now)
        
        # Send mentions as regular messages first
        if mentions:
            alert_messages = []
            for item, users in mentions.items():
                if users:
                    alert_messages.append(f"{item}: {' '.join(users)}")
            if alert_messages:
                await channel.send("\n".join(alert_messages))
        
        # Then send the embed
        await channel.send(embed=embed)
        await ctx.send("✅ Update sent to #growagarden!", ephemeral=True)

@bot.hybrid_command(name="autoscrape", description="Enable/disable auto-scraping (admin only)")
@app_commands.describe(enable="Enable or disable auto-scraping")
async def autoscrape(ctx: commands.Context, enable: bool):
    if not is_admin(ctx.interaction):
        return await ctx.send("❌ Admin only.", ephemeral=True)
    
    bot.autoscrape_enabled = enable
    status = "enabled" if enable else "disabled"
    
    if enable:
        # Calculate next scrape time for confirmation
        now = datetime.now(EST)
        next_time = calculate_next_scrape_time(now)
        next_str = next_time.strftime("%H:%M:%S")
        msg = f"✅ Auto-scrape enabled! Next scrape at {next_str} EST"
    else:
        msg = "⏸️ Auto-scrape disabled"
    
    await ctx.send(msg, ephemeral=True)

# ---------------------------
# Subscription Commands (Hybrid)
# ---------------------------
@bot.hybrid_command(name="sub", description="Subscribe to item alerts")
@app_commands.describe(items="Comma-separated list of items to subscribe to")
async def subscribe(ctx: commands.Context, *, items: str):
    uid = str(ctx.author.id)
    subs = bot.subscriptions.setdefault(uid, [])
    chosen, warn = [], []

    for raw in items.split(","):
        want = raw.strip()
        if not want:
            continue
        if bot.known_items:
            match = difflib.get_close_matches(want, bot.known_items, n=1, cutoff=0.6)
            if match:
                want = match[0]
            else:
                warn.append(f"No close match for **{raw}**, added as-is.")
        if any(i.lower() == want.lower() for i in subs):
            warn.append(f"Already subscribed to **{want}**.")
        else:
            subs.append(want)
            chosen.append(want)

    save_subs(bot.subscriptions)
    res = []
    if chosen: 
        res.append("✅ Subscribed to: " + ", ".join(f"**{c}**" for c in chosen))
    res += warn
    await ctx.send("\n".join(res) if res else "⚠️ No valid items to subscribe.", ephemeral=True)

@bot.hybrid_command(name="unsub", description="Unsubscribe from item alerts")
@app_commands.describe(items="Comma-separated list of items to unsubscribe from")
async def unsubscribe(ctx: commands.Context, *, items: str):
    uid = str(ctx.author.id)
    subs = bot.subscriptions.get(uid, [])
    removed, warn = [], []

    for raw in items.split(","):
        want = raw.strip()
        for ex in subs:
            if ex.lower() == want.lower():
                subs.remove(ex)
                removed.append(ex)
                break
        else:
            warn.append(f"Not subscribed to **{want}**.")

    save_subs(bot.subscriptions)
    res = []
    if removed: 
        res.append("❌ Unsubscribed: " + ", ".join(f"**{r}**" for r in removed))
    res += warn
    await ctx.send("\n".join(res) if res else "⚠️ No valid items to unsubscribe.", ephemeral=True)

@bot.hybrid_command(name="mylist", description="List your current subscriptions")
async def list_subs(ctx: commands.Context):
    uid = str(ctx.author.id)
    items = bot.subscriptions.get(uid, [])
    msg = "📦 Your subscriptions: " + ", ".join(f"**{i}**" for i in items) if items else "📭 You have no subscriptions."
    await ctx.send(msg, ephemeral=True)

# ---------------------------
# Bot Events
# ---------------------------
@bot.event
async def on_ready():
    logging.info(f"Logged in as {bot.user}")
    bot.subscriptions = load_subs()
    
    try:
        synced = await tree.sync()
        logging.info(f"Synced {len(synced)} slash commands")
    except Exception as e:
        logging.error(f"Command sync failed: {str(e)}")
    
    if not bot.poll_task:
        bot.poll_task = bot.loop.create_task(polling_loop())

@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandNotFound):
        return
    logging.error(f"Command error: {str(error)}")
    await ctx.send(f"⚠️ Error: {str(error)}", ephemeral=True)

# ---------------------------
# Start Bot
# ---------------------------
if __name__ == "__main__":
    bot.run(TOKEN)