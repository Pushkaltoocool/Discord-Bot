# main.py
import discord
from discord.ext import commands, tasks
import logging
from dotenv import load_dotenv
import os
import aiohttp
import asyncio
import datetime as dt
import re
import random
import json
import io
import sys
import atexit

# --- Single-instance file lock (prevents double runs on Render) ---
LOCK_PATH = "/tmp/tryhard_bot.lock"
_lock_file = None
def acquire_single_instance_lock():
    """Ensure only one process connects the bot token."""
    global _lock_file
    _lock_file = open(LOCK_PATH, "w")
    try:
        import fcntl
        fcntl.flock(_lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except Exception:
        print("Another instance appears to be running. Exiting to avoid duplicate messages.")
        sys.exit(0)
def release_single_instance_lock():
    try:
        import fcntl
        fcntl.flock(_lock_file, fcntl.LOCK_UN)
        _lock_file.close()
        os.remove(LOCK_PATH)
    except Exception:
        pass
acquire_single_instance_lock()
atexit.register(release_single_instance_lock)

# --- Gemini imports ---
import google.generativeai as genai
from pydantic import BaseModel

# Added Flask keep-alive server for Render
from flask import Flask
import threading

app = Flask('')

@app.route('/')
def home():
    return "✅ Tryhard Bot is alive!"

def run_web():
    # No debug/reloader to avoid duplicate processes
    app.run(host='0.0.0.0', port=8080, debug=False, use_reloader=False)

def keep_alive():
    t = threading.Thread(target=run_web, daemon=True)
    t.start()

# -------------------------------------------------------------

load_dotenv()
token = os.getenv('DISCORD_TOKEN')
genai.configure(api_key=os.getenv("GEMINI_API_KEY"))

# Logging
handler = logging.FileHandler(filename='discord.log', encoding='utf-8', mode='w')

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)
bot.remove_command("help")

# 👤 Kalvin
TARGET_USER_ID = 620792701201154048

# fallback list of quotes
quotes = [
    "Believe you can and you're halfway there.",
    "Keep going. Everything you need will come to you at the perfect time.",
    "Dream big and dare to fail.",
    "Success is not final, failure is not fatal: It is the courage to continue that counts.",
]

# Expanded depression/sadness triggers
sad_words = [
    "sad", "so sad", "really sad", "feeling sad", "feels sad", "sadness", "sadtimes", "sadge",
    "depressed", "depression", "depressing", "depress", "down bad", "downbad", "emo", "blue",
    "cry", "crying", "cryinggg", "cryin", "tears", "tearful", "sobbing", "weeping", "😢", "😭",
    "hopeless", "pointless", "worthless", "meaningless", "nothing matters", "no point",
    "why bother", "life sucks", "fml", "ugh life", "why me", "done with life", "so tired of this",
    "lonely", "alone", "unloved", "nobody cares", "nobody loves me", "i’m worthless", "not cared about",
    "no friends", "ignored", "abandoned", "empty", "isolated",
    "kill myself", "kms", "kys", "end it all", "suicidal", "suicide", "i wanna die", "want to die",
    "wish i was dead", "better off dead", "die alone", "ending it", "goodbye world",
    "slit wrists", "cutting", "self harm", "self-harm", "hurt myself", "not gonna make it",
    "im gonna throw myself off a cliff", "throw myself off a cliff",
    "jump off a bridge", "jump off a building", "throw myself off", "end my life",
    "anxious", "anxiety", "stressed", "stressful", "overwhelmed", "drained", "burnt out", "burned out",
    "low energy", "tired", "exhausted", "done", "numb", "broken", "hurt", "pain", "painful", "suffering",
    "mentally exhausted", "emotionally drained", "can’t handle this", "can’t do this anymore",
    "down", "feelsbad", "feels bad man", "bruh im sad", "ugh", "ugh life", "not okay", "im not okay",
    "never happy", "so low", "feeling low", "stuck", "trapped", "lost", "dark thoughts", "heavy",
    "in my feels", "in my feelings", "broken heart", "💔", "🫠", "😔", "☹️", "😞", "😟", "😩", "😫", "🥺", "😿", "😕"
]

# 🚫 EXTENSIVE banned list with variations
banned_words = [
    "nigga", "nigger", "niga", "niger", "nibba", "nibber",
    "niqqa", "niqqer", "n1gga", "n1gger", "n1gg4", "nigg4",
    "neega", "neegr", "niggaz", "nigz", "nigs", "nig",
    "nygga", "nygger", "nigguh", "niggur", "niggir",
]

# ---------------- SAFE SEND HELPERS -----------------
DISCORD_LIMIT = 2000
CHUNK_SIZE = 1900  # safety margin

async def safe_send(channel, text, **kwargs):
    """Send text safely without exceeding Discord limit."""
    if len(text) <= DISCORD_LIMIT:
        return await channel.send(text, **kwargs)
    # If long, ship as a file instead of spamming chunks
    data = io.BytesIO(text.encode("utf-8"))
    return await channel.send(file=discord.File(data, filename="message.txt"))

async def send_as_file(channel, content: str, filename: str, header: str = None):
    """Attach long content as a file instead of breaking Discord limit."""
    if header:
        await channel.send(header)
    data = io.BytesIO(content.encode("utf-8"))
    await channel.send(file=discord.File(data, filename))

# ---------------- UTILITIES -----------------
async def get_quote():
    """Fetch quote from API, fallback to local list if failed."""
    url = "https://zenquotes.io/api/random"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=8)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data[0]['q'] + " — " + data[0]['a']
    except Exception:
        pass
    return quotes[dt.datetime.now().day % len(quotes)]

def normalize_message(content: str) -> str:
    """Remove symbols, punctuation, emojis and make lowercase for strict filtering."""
    text = content.lower()
    text = re.sub(r'[^a-z]', '', text)  # keep only a-z
    return text

# ---------------- BOT EVENTS -----------------
_bot_ready_once = asyncio.Event()

@bot.event
async def on_ready():
    # Guard: ensure this only logs/starts once even if Discord reconnects
    if _bot_ready_once.is_set():
        return
    _bot_ready_once.set()
    print(f"✅ {bot.user.name} is online and ready!")
    if not send_daily_quote.is_running():
        send_daily_quote.start()

@bot.event
async def on_message(message):
    if message.author == bot.user or message.author.bot:
        return

    lower_msg = message.content.lower()

    # just for kalvin HAHAHAHHAAH
    if message.author.id == TARGET_USER_ID:
        normalized = normalize_message(message.content)
        for word in banned_words:
            normalized_word = normalize_message(word)
            if normalized_word in normalized:
                try:
                    await message.delete()
                except Exception:
                    pass
                await message.channel.send(f"{message.author.mention} just called himself gay!")
                break

    # Sadness detector :(
    if any(word in lower_msg for word in sad_words):
        quote = await get_quote()
        await safe_send(message.channel, f"💙 Stay strong {message.author.mention}, here’s something for you:\n> {quote}")

    # THANK YOU auto-trigger
    if "thank you" in lower_msg:
        await message.channel.send("https://tenor.com/view/thank-you-thank-you-bro-how-i-thank-bro-fantasy-challenge-thank-you-tiktok-gif-7839145224229268701")

    # PLZ SPEED auto-trigger
    if re.search(r"plz.*speed.*i need this", lower_msg):
        await message.channel.send("https://tenor.com/view/my-mom-is-kinda-homeless-ishowspeed-speeding-please-speed-i-need-this-ishowspeed-trying-not-to-laugh-gif-16620227105127147208")

    await bot.process_commands(message)

# ---------------- BOT COMMANDS -----------------

# ✅ Moodplay schema (strict: one song only)
class MoodResponse(BaseModel):
    mood: str
    song_title: str
    artist: str

# lightweight concurrency guard to avoid overlapping !moodplay in same channel
_moodplay_locks = {}

def channel_lock(channel_id: int) -> asyncio.Lock:
    lock = _moodplay_locks.get(channel_id)
    if not lock:
        lock = asyncio.Lock()
        _moodplay_locks[channel_id] = lock
    return lock

@bot.command(name="moodplay")
async def moodplay(ctx):
    async with channel_lock(ctx.channel.id):
        await ctx.send("⚡ Moodplay command triggered!")
        await ctx.send("🔍 Collecting recent messages...")

        # Collect chat fragment
        lines = []
        async for msg in ctx.channel.history(limit=30):
            if msg.author.bot:
                continue
            author = getattr(msg.author, "display_name", str(msg.author))
            content = msg.content.replace("\n", " ").strip()
            if not content:
                continue
            content = content[:160]
            lines.append(f"{author}: {content}")
        lines.reverse()
        preview = "\n".join(lines[-10:])

        prompt = (
            "You are a DJ. Read the chat fragment and output STRICT JSON matching this schema:\n"
            "{ \"mood\": string, \"song_title\": string, \"artist\": string }\n"
            "- Choose EXACTLY ONE specific, real song.\n"
            "- 'song_title' must be the official song name only (no extra text).\n"
            "- 'artist' must be the main performing artist only (no features unless essential).\n"
            "- Do not add commentary. Only output JSON.\n\n"
            f"Chat fragment:\n{preview}"
        )

        try:
            await ctx.send("🤖 Talking to Gemini...")
            model = genai.GenerativeModel("gemini-2.0-flash")

            response = await asyncio.to_thread(
                model.generate_content,
                prompt,
                generation_config=genai.types.GenerationConfig(
                    response_mime_type="application/json",
                ),
            )

            # Try multiple places for raw text
            raw = getattr(response, "text", None) or ""
            if not raw and hasattr(response, "candidates"):
                try:
                    raw = response.candidates[0].content.parts[0].text
                except Exception:
                    pass

            # DEBUG: log raw Gemini output in Discord
            if raw:
                await send_as_file(ctx, raw, "gemini_raw.json", header="📝 Raw Gemini output:")
            else:
                await ctx.send("⚠️ Gemini returned no text.")

            # Parse JSON
            data = None
            if raw:
                try:
                    data = json.loads(raw)
                except Exception:
                    m = re.search(r"\{.*\}", raw, re.S)
                    if m:
                        data = json.loads(m.group(0))

            if not data or not isinstance(data, dict):
                fallback = [
                    {"mood": "uplifting", "song_title": "Don't Stop Me Now", "artist": "Queen"},
                    {"mood": "chill", "song_title": "Lo-Fi Beats", "artist": "ChilledCow"},
                    {"mood": "happy", "song_title": "Happy", "artist": "Pharrell Williams"},
                    {"mood": "moody", "song_title": "Blinding Lights", "artist": "The Weeknd"},
                ]
                data = random.choice(fallback)

            mood = str(data.get("mood", "unknown")).strip() or "unknown"
            song_title = str(data.get("song_title", "")).strip()
            artist = str(data.get("artist", "")).strip()

            if not song_title or not artist:
                song_title, artist = "Don't Stop Me Now", "Queen"

            await ctx.send(f"🎶 Mood: **{mood}**\nRecommendation: **{song_title} — {artist}**")

            play_cmd = f"m!play {song_title} - {artist}"
            await ctx.send(play_cmd, allowed_mentions=discord.AllowedMentions.none())

        except Exception as e:
            await safe_send(ctx, f"❌ Gemini step failed.\nError: {e}")


# Poll command
@bot.command()
async def poll(ctx, *args):
    if len(args) < 3:
        await ctx.send("Usage: !poll <question> <option1> <option2> [option3] ... (max 10 options)")
        return

    question = args[0]
    options = args[1:]

    if len(options) > 10:
        await ctx.send("You can’t have more than 10 options.")
        return

    reactions = ["1️⃣","2️⃣","3️⃣","4️⃣","5️⃣","6️⃣","7️⃣","8️⃣","9️⃣","🔟"]
    description = ""
    for i, option in enumerate(options):
        description += f"{reactions[i]} {option}\n"

    embed = discord.Embed(title=question, description=description, color=discord.Color.blue())
    msg = await ctx.send(embed=embed)

    for i in range(len(options)):
        await msg.add_reaction(reactions[i])

# Daily motivational quote at 8 AM (UTC+8)
@tasks.loop(time=dt.time(hour=8, minute=0, tzinfo=dt.timezone(dt.timedelta(hours=8))))
async def send_daily_quote():
    channel = discord.utils.get(bot.get_all_channels(), name="general")
    if channel:
        quote = await get_quote()
        await channel.send(f"🌞 Daily Motivation:\n> {quote}")

# Manual help command
@bot.command(name="ineedhelp")
async def ineedhelp(ctx):
    quote = await get_quote()
    await ctx.send(f"💡 Here’s something to lift you up, {ctx.author.mention}:\n> {quote}")

# Thank You command
@bot.command(name="thankyou")
async def thankyou(ctx):
    await ctx.send("https://tenor.com/view/thank-you-thank-you-bro-how-i-thank-bro-fantasy-challenge-thank-you-tiktok-gif-7839145224229268701")

# Speed command
@bot.command(name="plzspeedineedthis")
async def plzspeedineedthis(ctx):
    await ctx.send("https://tenor.com/view/my-mom-is-kinda-homeless-ishowspeed-speeding-please-speed-i-need-this-ishowspeed-trying-not-to-laugh-gif-16620227105127147208")

# Coin flip command
@bot.command(name="flip")
async def flip(ctx):
    result = random.choice(["Heads 👑", "Tails 🍑"])
    await ctx.send(f"🪙 The coin landed on... **{result}**!")

# Roast command (API)
@bot.command(name="roast")
async def roast(ctx, member: discord.Member = None):
    if not member:
        member = ctx.author

    url = "https://evilinsult.com/generate_insult.php?lang=en&type=json"
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as resp:
            if resp.status == 200:
                data = await resp.json()
                insult = data.get("insult", "You're lucky, I couldn't think of an insult.")
                await ctx.send(f"🔥 {member.mention}, {insult}")
            else:
                await ctx.send(f"🔥 {member.mention}, you're lucky, the roast machine broke.")

# Compliment command (API)
@bot.command(name="compliment")
async def compliment(ctx, member: discord.Member = None):
    if not member:
        member = ctx.author

    url = "https://complimentr.com/api"
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as resp:
            if resp.status == 200:
                data = await resp.json()
                comp = data.get("compliment", "You're amazing!")
                await ctx.send(f"💖 {member.mention}, {comp}")
            else:
                await ctx.send(f"💖 {member.mention}, you're amazing (API failed but I got you).")

# Custom help command (renamed)
@bot.command(name="helptryhard")
async def help_command(ctx):
    embed = discord.Embed(
        title="📖 Tryhard Bot Help",
        description="Here are all the commands and features I support:",
        color=discord.Color.green()
    )
    embed.add_field(name="!poll <question> <option1> <option2> ...", value="Create a poll (2–10 options).", inline=False)
    embed.add_field(name="!ineedhelp", value="Get a motivational quote instantly.", inline=False)
    embed.add_field(name="!thankyou", value="Send a thank you gif.", inline=False)
    embed.add_field(name="!plzspeedineedthis", value="Send a Speed gif.", inline=False)
    embed.add_field(name="!flip", value="Flip a coin (Heads or Tails).", inline=False)
    embed.add_field(name="!roast @user", value="Send a random roast from Evil Insult API.", inline=False)
    embed.add_field(name="!compliment @user", value="Send a wholesome compliment from Complimentr API.", inline=False)
    embed.add_field(name="🌞 Daily Quotes", value="I send a motivational quote every day at 8 AM in #general.", inline=False)
    embed.add_field(name="😢 Depression Checker", value="If you say sad/depressed/self-harm things, I’ll send you a motivational quote.", inline=False)
    embed.add_field(name="🛑 Special Filter", value="If user `620792701201154048` uses *any* version of the N-word, their message is deleted and replaced with a funny reply.", inline=False)
    embed.add_field(name="😂 Auto-Triggers", value="Saying 'thank you' or 'plz speed i need this' will trigger funny gifs.", inline=False)

    await ctx.send(embed=embed)

# ---------------- RUN -----------------
keep_alive()
bot.run(token, log_handler=handler, log_level=logging.INFO)
