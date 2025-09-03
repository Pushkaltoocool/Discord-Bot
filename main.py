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

# Translation
from deep_translator import GoogleTranslator

app = Flask('')

@app.route('/')
def home():
    return "✅ Tryhard Bot is alive!"

def run_web():
    port = int(os.environ.get("PORT", 8080))  # Render assigns PORT
    app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)


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

def parse_duration_to_seconds(s: str) -> int:
    """
    Parse compact duration strings like:
      10m, 2h, 1d, 45s, or combos like 1h30m, 2d4h, 2h15m30s, etc.
    Returns total seconds; raises ValueError if invalid.
    """
    s = s.strip().lower()
    if not s:
        raise ValueError("Empty duration.")
    # allow space-separated combos as well: "1h 30m"
    parts = re.findall(r'(\d+)\s*([smhd])', s)
    if not parts:
        raise ValueError("Invalid time format.")
    total = 0
    unit_map = {"s": 1, "m": 60, "h": 3600, "d": 86400}
    for amt, unit in parts:
        total += int(amt) * unit_map[unit]
    if total <= 0:
        raise ValueError("Duration must be > 0.")
    return total

# Language code resolver for translate
LANG_ALIASES = {
    # ISO codes
    "en": "en", "eng": "en", "english": "en",
    "es": "es", "spa": "es", "spanish": "es", "español": "es",
    "fr": "fr", "fra": "fr", "fre": "fr", "french": "fr",
    "de": "de", "ger": "de", "deu": "de", "german": "de",
    "it": "it", "ita": "it", "italian": "it",
    "pt": "pt", "por": "pt", "portuguese": "pt",
    "pt-br": "pt", "br": "pt",
    "ru": "ru", "rus": "ru", "russian": "ru",
    "zh": "zh-cn", "zh-cn": "zh-cn", "chinese": "zh-cn", "mandarin": "zh-cn",
    "zh-tw": "zh-tw", "traditional chinese": "zh-tw",
    "ja": "ja", "jpn": "ja", "japanese": "ja",
    "ko": "ko", "kor": "ko", "korean": "ko",
    "ar": "ar", "ara": "ar", "arabic": "ar",
    "hi": "hi", "hin": "hi", "hindi": "hi",
    "id": "id", "ind": "id", "indonesian": "id", "bahasa": "id",
    "ms": "ms", "msa": "ms", "malay": "ms",
    "tl": "tl", "fil": "tl", "tagalog": "tl", "filipino": "tl",
    "vi": "vi", "vie": "vi", "vietnamese": "vi",
    "th": "th", "tha": "th", "thai": "th",
    "tr": "tr", "tur": "tr", "turkish": "tr",
    "nl": "nl", "dut": "nl", "nld": "nl", "dutch": "nl",
    "sv": "sv", "swe": "sv", "swedish": "sv",
    "no": "no", "nor": "no", "norsk": "no", "nb": "no", "nn": "no",
    "da": "da", "dan": "da", "danish": "da",
    "pl": "pl", "pol": "pl", "polish": "pl",
    "uk": "uk", "ukr": "uk", "ukrainian": "uk",
    "cs": "cs", "cze": "cs", "ces": "cs", "czech": "cs",
    "el": "el", "greek": "el",
    "he": "he", "iw": "he", "heb": "he", "hebrew": "he",
    "fa": "fa", "per": "fa", "fas": "fa", "farsi": "fa", "persian": "fa",
    "bg": "bg", "bul": "bg", "bulgarian": "bg",
    "ro": "ro", "rum": "ro", "ron": "ro", "romanian": "ro",
    "hu": "hu", "hun": "hu", "hungarian": "hu",
    "fi": "fi", "fin": "fi", "finnish": "fi",
    "et": "et", "est": "et", "estonian": "et",
    "lt": "lt", "lit": "lt", "lithuanian": "lt",
    "lv": "lv", "lav": "lv", "latvian": "lv",
    "sr": "sr", "srp": "sr", "serbian": "sr",
    "sk": "sk", "slk": "sk", "slovak": "sk",
    "sl": "sl", "slv": "sl", "slovenian": "sl",
    "hr": "hr", "hrv": "hr", "croatian": "hr",
    "ga": "ga", "gle": "ga", "irish": "ga",
    "is": "is", "ice": "is", "isl": "is", "icelandic": "is",
    "af": "af", "afr": "af", "afrikaans": "af",
    "sw": "sw", "swa": "sw", "swahili": "sw",
    "am": "am", "amh": "am", "amharic": "am",
    "ur": "ur", "urd": "ur", "urdu": "ur",
    "bn": "bn", "ben": "bn", "bengali": "bn",
    "ta": "ta", "tam": "ta", "tamil": "ta",
    "te": "te", "tel": "te", "telugu": "te",
    "mr": "mr", "mar": "mr", "marathi": "mr",
    "gu": "gu", "guj": "gu", "gujarati": "gu",
    "pa": "pa", "pan": "pa", "punjabi": "pa",
    "swedish": "sv", "norwegian": "no",
}

def resolve_lang_code(s: str) -> str:
    key = s.strip().lower()
    return LANG_ALIASES.get(key, key)  # fall back to provided key

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

            await ctx.send(f"🎶 Mood: **{mood}**\nRecommendation: **{song_title} {artist}**")

            play_cmd = f"m!play {song_title} - {artist}"
            await ctx.send(play_cmd, allowed_mentions=discord.AllowedMentions.none())

        except Exception as e:
            await safe_send(ctx, f"❌ Gemini step failed.\nError: {e}")

# ---------------- NEW FEATURES -----------------

# -- Would You Rather (!wyr)
WYR_QUESTIONS = [
    "Would you rather be invisible or be able to fly?",
    "Would you rather have unlimited sushi for life or unlimited tacos for life?",
    "Would you rather always be 10 minutes late or always be 20 minutes early?",
    "Would you rather fight 100 duck-sized horses or 1 horse-sized duck?",
    "Would you rather know the history of every object you touch or be able to talk to animals?",
    "Would you rather never use social media again or never watch another movie or TV show?",
    "Would you rather teleport anywhere or be able to read minds?",
    "Would you rather have the ability to see 10 minutes into the future or 150 years into the future?",
    "Would you rather be forced to sing along to every song you hear or dance to every song you hear?",
    "Would you rather have a personal maid or a personal chef?",
    "Would you rather lose your sight or your memories?",
    "Would you rather always have a full phone battery or a full gas tank?",
    "Would you rather have super strength or super speed?",
    "Would you rather be able to speak all languages or be able to speak to animals?",
    "Would you rather be the funniest person in the room or the smartest?",
    "Would you rather live in a world where it pours whenever you sneeze or thunder claps whenever you laugh?",
    "Would you rather never be stuck in traffic again or never get another cold?",
    "Would you rather live without music or live without video games?",
    "Would you rather drink only water or only coffee for the rest of your life?",
    "Would you rather be an unknown superhero or a famous villain?",
    "Would you rather always step on a LEGO or always feel like you need to sneeze?",
    "Would you rather give up pizza forever or give up burgers forever?",
    "Would you rather have one real get-out-of-jail-free card or a key that opens any door?",
    "Would you rather glow bright pink every time you’re embarrassed or have a loud honk whenever you’re stressed?",
    "Would you rather be able to pause time or rewind time?",
    "Would you rather have to listen to only one song forever or watch only one movie forever?",
    "Would you rather be rich and lonely or poor and popular?",
    "Would you rather read the book or watch the movie?",
    "Would you rather live in space or live under the sea?",
    "Would you rather be the best player on a losing team or the worst player on a winning team?",
    "Would you rather only be able to whisper or only be able to shout?",
    "Would you rather be able to change the past or see into the future?",
    "Would you rather always have the perfect comeback or always get the last laugh?",
    "Would you rather wear wet socks for a day or wear winter gloves all day in summer?",
    "Would you rather never have to sleep or never have to eat?",
    "Would you rather find true love today or win the lottery next year?",
    "Would you rather have free international flights for life or never pay for food at restaurants?",
    "Would you rather only talk in rhymes or only talk in riddles?",
    "Would you rather have your dream job but no time for friends, or a simple job with tons of time for friends?",
    "Would you rather always feel slightly too hot or slightly too cold?",
    "Would you rather be trapped in a romantic comedy with your enemies or a horror movie with your friends?",
    "Would you rather be able to only move by skipping or only move by crawling?",
    "Would you rather never use emojis again or never watch memes again?",
    "Would you rather own a dragon or be a dragon?",
    "Would you rather travel the world for a year on a shoestring budget or stay in one country in luxury?",
    "Would you rather have a rewind button on your life or a pause button?",
    "Would you rather live with no internet or no AC/heating?",
    "Would you rather always get stuck behind slow walkers or always be stuck in traffic?",
    "Would you rather have a photographic memory or be able to forget anything you want?",
    "Would you rather only eat spicy food or only eat bland food?",
    "Would you rather never age physically or never age mentally?",
    "Would you rather always say what you’re thinking or never speak again?",
    "Would you rather give up your smartphone for a week or give up sugar for a week?",
    "Would you rather be able to clone yourself once or time travel once?",
    "Would you rather be famous for something embarrassing or unknown for something meaningful?",
]

@bot.command(name="wyr")
async def wyr(ctx):
    """Send a Would You Rather question and add vote reactions."""
    q = random.choice(WYR_QUESTIONS)
    # Try to split into two options for display if possible
    opt_a, opt_b = None, None
    # Common "or" splitter
    if " or " in q.lower():
        parts = re.split(r"\s+or\s+", q, flags=re.IGNORECASE)
        if len(parts) == 2:
            opt_a, opt_b = parts[0].strip(" ?"), parts[1].strip(" ?")
    desc = ""
    if opt_a and opt_b:
        desc = f"1️⃣ {opt_a}\n2️⃣ {opt_b}"
    embed = discord.Embed(title="🤔 Would You Rather...", description=desc or q, color=discord.Color.blurple())
    msg = await ctx.send(embed=embed if desc else None, content=None if desc else f"🤔 {q}")
    # Always add 1 and 2 for consistency
    try:
        await msg.add_reaction("1️⃣")
        await msg.add_reaction("2️⃣")
    except Exception:
        pass

# -- Reminder System (!remindme 10m <message>) w/ combo support like 1h30m
async def _reminder_task(channel: discord.TextChannel, user: discord.User, seconds: int, reminder: str):
    try:
        await asyncio.sleep(seconds)
        await channel.send(f"🔔 Reminder for {user.mention}: {reminder}")
    except Exception:
        # Channel may be gone or perms changed — swallow errors
        pass

@bot.command(name="remindme")
async def remindme(ctx, time: str = None, *, message: str = None):
    """
    Usage: !remindme <duration> <message>
    Examples:
      !remindme 10m stretch
      !remindme 1h30m take a break
      !remindme 2d submit assignment
    """
    if not time or not message:
        return await ctx.send("Usage: `!remindme <10s|10m|2h|1d|1h30m> <message>`")
    try:
        seconds = parse_duration_to_seconds(time)
    except ValueError:
        return await ctx.send("⏱️ Invalid duration. Examples: `10m`, `1h30m`, `2d4h`, `45s`")
    # Schedule task without blocking the command
    asyncio.create_task(_reminder_task(ctx.channel, ctx.author, seconds, message))
    await ctx.send(f"⏰ Okay {ctx.author.mention}, I’ll remind you in **{time}**: {message}")

# -- Translate Command (!translate <lang> <text>) using deep_translator (GoogleTranslator)
@bot.command(name="translate")
async def translate_cmd(ctx, lang: str = None, *, text: str = None):
    """
    Translate text into a target language.
    Usage: !translate <lang> <text>
    Examples:
      !translate es Hello, how are you?
      !translate japanese I love ramen
    """
    if not lang or not text:
        return await ctx.send("Usage: `!translate <lang> <text>` e.g., `!translate es good morning`")
    target = resolve_lang_code(lang)
    try:
        translated_text = await asyncio.to_thread(
            GoogleTranslator(source="auto", target=target).translate, text
        )
        await ctx.send(f"🌍 **{translated_text}** *(auto → {target})*")
    except Exception as e:
        await ctx.send(f"❌ Translation failed: {e}")

# -- Mood Tracker (!mymood) analyze last 20 messages by that user (cross-channels best-effort)
POS_WORDS = {
    "happy", "glad", "great", "awesome", "good", "love", "excited", "yay", "win", "nice",
    "fun", "cool", "chill", "relaxed", "relax", "lol", "lmao", "haha", "hehe", "content"
}
NEG_WORDS = {
    "sad", "tired", "angry", "mad", "upset", "anxious", "stress", "stressed", "depressed",
    "cry", "crying", "lonely", "worthless", "pain", "hurt", "numb", "lost", "down", "ugh", "hate"
}

async def collect_user_messages(guild: discord.Guild, user: discord.User, needed: int = 20, per_channel_limit: int = 200, global_scan_limit: int = 3000):
    """Collect up to `needed` most-recent messages by user across text channels (best-effort)."""
    msgs = []
    scanned = 0
    if not guild:
        return msgs
    for channel in guild.text_channels:
        if len(msgs) >= needed:
            break
        # skip channels bot can't read
        if not channel.permissions_for(guild.me).read_message_history:
            continue
        try:
            async for msg in channel.history(limit=per_channel_limit):
                scanned += 1
                if scanned > global_scan_limit or len(msgs) >= needed:
                    break
                if msg.author.id == user.id and msg.content:
                    # keep a compact single-line version
                    clean = msg.content.replace("\n", " ").strip()
                    if clean:
                        msgs.append(clean[:200])
                        if len(msgs) >= needed:
                            break
        except Exception:
            # perms or rate limits — ignore channel
            continue
    return msgs[:needed]

def heuristic_mood_guess(texts: list[str]) -> str:
    """Fallback mood guess without AI if Gemini fails."""
    text = " ".join(texts).lower()
    pos = sum(1 for w in POS_WORDS if w in text)
    neg = sum(1 for w in NEG_WORDS if w in text)
    if pos > neg and pos > 0:
        return "happy"
    if neg > pos and neg > 0:
        # pick a common negative vibe based on keywords
        if any(k in text for k in ["stress", "stressed", "pressure", "deadline"]):
            return "stressed"
        if any(k in text for k in ["angry", "mad"]):
            return "angry"
        if any(k in text for k in ["sad", "cry", "lonely", "depress"]):
            return "sad"
        return "down"
    return "neutral"

@bot.command(name="mymood")
async def mymood(ctx):
    """Analyze last 20 messages sent by the invoking user and report mood."""
    await ctx.send("🧠 Analyzing your recent messages...")
    # Prefer cross-channel (guild) collection, fallback to current channel only
    texts = []
    if ctx.guild:
        texts = await collect_user_messages(ctx.guild, ctx.author, needed=20, per_channel_limit=100, global_scan_limit=2000)
    if not texts:
        # fallback: current channel only
        async for msg in ctx.channel.history(limit=300):
            if msg.author.id == ctx.author.id and msg.content:
                texts.append(msg.content.replace("\n", " ")[:200])
                if len(texts) >= 20:
                    break
    if not texts:
        return await ctx.send("😕 I couldn’t find enough of your messages to analyze.")
    fragment = "\n".join(texts)

    prompt = (
        "You will receive up to 20 recent messages from ONE user. "
        "Infer their CURRENT OVERALL MOOD as a single lowercase word from this set:\n"
        "[happy, sad, stressed, chill, angry, excited, bored, anxious, neutral].\n"
        "Rules:\n"
        "- Respond with ONLY one word, no punctuation or explanations.\n"
        "- If uncertain, respond with 'neutral'.\n\n"
        f"MESSAGES:\n{fragment}\n\n"
        "MOOD:"
    )
    mood = None
    try:
        model = genai.GenerativeModel("gemini-2.0-flash")
        response = await asyncio.to_thread(model.generate_content, prompt)
        mood_raw = (getattr(response, "text", None) or "").strip().lower()
        # sanitize to a single token
        mood = re.sub(r"[^a-z]", "", mood_raw)
        if not mood:
            raise ValueError("Empty AI response.")
    except Exception:
        mood = heuristic_mood_guess(texts)

    await ctx.send(f"🧭 Based on your last 20 messages, your mood seems to be: **{mood}**")

# ---------------- Existing commands (plus minor improvements) -----------------

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
        try:
            await msg.add_reaction(reactions[i])
        except Exception:
            pass

# Daily motivational quote at 8 AM (UTC+8)
@tasks.loop(time=dt.time(hour=8, minute=0, tzinfo=dt.timezone(dt.timedelta(hours=8))))
async def send_daily_quote():
    # Prefer #general; fallback to the first available text channel
    channel = discord.utils.get(bot.get_all_channels(), name="general")
    if not channel:
        for ch in bot.get_all_channels():
            if isinstance(ch, discord.TextChannel):
                channel = ch
                break
    if channel:
        quote = await get_quote()
        try:
            await channel.send(f"🌞 Daily Motivation:\n> {quote}")
        except Exception:
            pass

# Manual help command (renamed)
@bot.command(name="helptryhard")
async def help_command(ctx):
    embed = discord.Embed(
        title="📖 Tryhard Bot Help",
        description="Here are all the commands and features I support:",
        color=discord.Color.green()
    )
    embed.add_field(name="!wyr", value="Would You Rather — vote with 1️⃣ / 2️⃣.", inline=False)
    embed.add_field(name="!remindme <time> <message>", value="Set a reminder. e.g. `!remindme 1h30m take a break`", inline=False)
    embed.add_field(name="!translate <lang> <text>", value="Translate text to a target language. e.g. `!translate es good morning`", inline=False)
    embed.add_field(name="!mymood", value="Analyze your last 20 messages and guess your mood.", inline=False)
    embed.add_field(name="!moodplay", value="AI DJ recommends EXACTLY one song based on chat vibe.", inline=False)
    embed.add_field(name="!poll <question> <option1> <option2> [...]", value="Create a poll (2–10 options).", inline=False)
    embed.add_field(name="!ineedhelp", value="Get a motivational quote instantly.", inline=False)
    embed.add_field(name="!thankyou", value="Send a thank you gif.", inline=False)
    embed.add_field(name="!plzspeedineedthis", value="Send a Speed gif.", inline=False)
    embed.add_field(name="!flip", value="Flip a coin (Heads or Tails).", inline=False)
    embed.add_field(name="!roast @user", value="Send a random roast from Evil Insult API.", inline=False)
    embed.add_field(name="!compliment @user", value="Send a wholesome compliment (now with fallback).", inline=False)
    embed.add_field(name="🌞 Daily Quotes", value="I send a motivational quote every day at 8 AM in #general.", inline=False)
    embed.add_field(name="😢 Depression Checker", value="If you say sad/depressed/self-harm things, I’ll send you a motivational quote.", inline=False)
    embed.add_field(name="🛑 Special Filter", value="If user `620792701201154048` uses *any* version of the N-word, their message is deleted and replaced with a funny reply.", inline=False)
    embed.add_field(name="😂 Auto-Triggers", value="Saying 'thank you' or 'plz speed i need this' will trigger funny gifs.", inline=False)

    await ctx.send(embed=embed)

# Thank You command
@bot.command(name="thankyou")
async def thankyou(ctx):
    await ctx.send("https://tenor.com/view/thank-you-thank-you-bro-how-i-thank-bro-fantasy-challenge-thank-you-tiktok-gif-7839145224229268701")

# Speed command
@bot.command(name="plzspeedineedthis")
async def plzspeedineedthis(ctx):
    await ctx.send("https://tenor.com/view/my-mom-is-kinda-homeless-ishowspeed-speeding-please-speed-i-need-this-ishowspeed-trying-not-to-laugh-gif-16620227105127147208")

# 67 command (triggers on any orientation of 6 7 or six seven)
@bot.command(name="67")
async def sixtyseven(ctx):
    await ctx.send("https://tenor.com/view/taylen-kinney-6-7-67-six-seven-doot-doot-gif-14312959711459626479")

@bot.event
async def on_message(message):
    if message.author == bot.user or message.author.bot:
        return

    lower_msg = message.content.lower()

    # 67 meme trigger (any orientation: 6 7, 7 6, six seven, seven six, etc.)
    patterns = [
        r"\b6\s*7\b", r"\b7\s*6\b",
        r"\bsix\s*seven\b", r"\bseven\s*six\b",
        r"\b6\s*seven\b", r"\bsix\s*7\b",
        r"\bseven\s*6\b", r"\b7\s*six\b"
    ]
    if any(re.search(p, lower_msg) for p in patterns):
        await message.channel.send("https://tenor.com/view/taylen-kinney-6-7-67-six-seven-doot-doot-gif-14312959711459626479")
        return

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
        try:
            async with session.get(url) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    insult = data.get("insult", "You're lucky, I couldn't think of an insult.")
                    await ctx.send(f"🔥 {member.mention}, {insult}")
                else:
                    await ctx.send(f"🔥 {member.mention}, you're lucky, the roast machine broke.")
        except Exception:
            await ctx.send(f"🔥 {member.mention}, the roast API choked. You win this round.")

# Compliment command (FIXED with robust fallback)
@bot.command(name="compliment")
async def compliment(ctx, member: discord.Member = None):
    if not member:
        member = ctx.author

    # Primary API
    url_primary = "https://complimentr.com/api"
    # Secondary API (fun fact / fortune fallback, we’ll rephrase it)
    url_secondary = "https://api.adviceslip.com/advice"

    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(url_primary, timeout=aiohttp.ClientTimeout(total=8)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    comp = data.get("compliment", "").strip()
                    if comp:
                        return await ctx.send(f"💖 {member.mention}, {comp}")
        except Exception:
            pass

        # Secondary attempt
        try:
            async with session.get(url_secondary, timeout=aiohttp.ClientTimeout(total=8)) as resp2:
                if resp2.status == 200:
                    data2 = await resp2.json()
                    advice = (data2.get("slip") or {}).get("advice", "").strip()
                    if advice:
                        return await ctx.send(f"💖 {member.mention}, you're awesome — also, a lil' thought: {advice}")
        except Exception:
            pass

    # Final local fallback
    fallback = [
        "You have a magnetic energy that brightens rooms.",
        "Your presence makes things better.",
        "You’re the kind of person people feel lucky to know.",
        "You make hard things feel possible.",
        "Your humor is elite. Never change.",
        "You’re doing better than you think.",
    ]
    await ctx.send(f"💖 {member.mention}, {random.choice(fallback)}")

# Manual help for immediate support
@bot.command(name="ineedhelp")
async def ineedhelp(ctx):
    quote = await get_quote()
    await ctx.send(f"💡 Here’s something to lift you up, {ctx.author.mention}:\n> {quote}")

# ---------------- RUN -----------------
keep_alive()
bot.run(token, log_handler=handler, log_level=logging.INFO)
