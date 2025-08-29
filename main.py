import discord
from discord.ext import commands, tasks
import logging
from dotenv import load_dotenv
import os
import aiohttp
import asyncio
import datetime
import re
import random
import json
import google.generativeai as genai
from google.generativeai.types import content_types

# -------------------------------------------------------------
# Flask keep-alive (ONLY if you’re pinging with UptimeRobot)
from flask import Flask
import threading

app = Flask(__name__)

@app.route('/')
def home():
    return "✅ Tryhard Bot is alive!"

def run_web():
    app.run(host='0.0.0.0', port=8080, threaded=True)

def keep_alive():
    t = threading.Thread(target=run_web)
    t.daemon = True
    t.start()

# -------------------------------------------------------------
load_dotenv()
token = os.getenv('DISCORD_TOKEN')
genai.configure(api_key=os.getenv("GEMINI_API_KEY"))

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

# sad triggers
sad_words = [
    "sad","so sad","really sad","feeling sad","feels sad","sadness","sadtimes","sadge",
    "depressed","depression","depressing","depress","down bad","downbad","emo","blue",
    "cry","crying","cryinggg","cryin","tears","tearful","sobbing","weeping","😢","😭",
    "hopeless","pointless","worthless","meaningless","nothing matters","no point",
    "why bother","life sucks","fml","ugh life","why me","done with life","so tired of this",
    "lonely","alone","unloved","nobody cares","nobody loves me","i’m worthless","not cared about",
    "no friends","ignored","abandoned","empty","isolated",
    "kill myself","kms","kys","end it all","suicidal","suicide","i wanna die","want to die", 
    "wish i was dead","better off dead","die alone","ending it","goodbye world",
    "slit wrists","cutting","self harm","self-harm","hurt myself","not gonna make it",
    "jump off a bridge","jump off a building","end my life",
    "anxious","anxiety","stressed","stressful","overwhelmed","drained","burnt out","burned out",
    "low energy","tired","exhausted","done","numb","broken","hurt","pain","painful","suffering",
    "mentally exhausted","emotionally drained","can’t handle this","can’t do this anymore",
    "down","feelsbad","feels bad man","ugh","im not okay","never happy","so low","stuck","trapped","lost",
    "dark thoughts","heavy","in my feels","broken heart","💔","😔","☹️","😞","😟","😩","😫","🥺","😿","😕"
]

# 🚫 banned list
banned_words = ["nigga","nigger","niga","niger","nibba","nibber"]

async def get_quote():
    url = "https://zenquotes.io/api/random"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data[0]['q'] + " — " + data[0]['a']
    except:
        pass
    return random.choice(quotes)

def normalize_message(content: str) -> str:
    text = content.lower()
    text = re.sub(r'[^a-z]', '', text)
    return text

# ---------------- BOT EVENTS -----------------
@bot.event
async def on_ready():
    print(f"✅ {bot.user.name} is online and ready!")
    if not send_daily_quote.is_running():
        send_daily_quote.start()

@bot.event
async def on_message(message):
    if message.author == bot.user or message.author.bot:
        return

    lower_msg = message.content.lower()

    if message.author.id == TARGET_USER_ID:
        normalized = normalize_message(message.content)
        for word in banned_words:
            normalized_word = normalize_message(word)
            if normalized_word in normalized:
                await message.delete()
                await message.channel.send(f"{message.author.mention} just called himself gay!")
                break

    if any(word in lower_msg for word in sad_words):
        quote = await get_quote()
        await message.channel.send(f"💙 Stay strong {message.author.mention}, here’s something for you:\n> {quote}")

    if "thank you" in lower_msg:
        await message.channel.send("https://tenor.com/view/thank-you-thank-you-bro-how-i-thank-bro-fantasy-challenge-thank-you-tiktok-gif-7839145224229268701")

    if re.search(r"plz.*speed.*i need this", lower_msg):
        await message.channel.send("https://tenor.com/view/my-mom-is-kinda-homeless-ishowspeed-speeding-please-speed-i-need-this-ishowspeed-trying-not-to-laugh-gif-16620227105127147208")

    await bot.process_commands(message)

# ---------------- BOT COMMANDS -----------------
@bot.command(name="moodplay")
async def moodplay(ctx):
    message = ctx.message
    await message.channel.send("⚡ Moodplay command triggered!")

    try:
        await message.channel.send("🔍 Collecting recent messages...")
        messages = []
        async for msg in ctx.channel.history(limit=20):
            messages.append(f"{msg.author}: {msg.content}")
        messages.reverse()
        await message.channel.send(f"📝 Collected {len(messages)} messages:\n{messages}")

        await message.channel.send("🤖 Initializing Gemini model...")
        model = genai.GenerativeModel("gemini-2.0-flash")
        schema = content_types.Schema(
            type="object",
            properties={
                "mood": {"type": "string"},
                "song_recommendation": {"type": "string"},
            },
            required=["mood", "song_recommendation"]
        )

        await message.channel.send("📡 Sending messages to Gemini API...")
        response = await asyncio.to_thread(
            model.generate_content,
            f"Using these messages in the conversation, return the mood and a song recommendation in JSON. Messages: {messages}",
            generation_config=genai.types.GenerationConfig(
                response_mime_type="application/json",
                response_schema=schema,
            ),
        )

        await message.channel.send(f"📩 Raw Gemini response:\n```json\n{response.text}\n```")

        await message.channel.send("🔎 Parsing Gemini response...")
        data = json.loads(response.text)
        mood = data.get("mood")
        song = data.get("song_recommendation")

        await message.channel.send(f"✅ Parsed successfully!\nMood: **{mood}**\nSong: **{song}**")
        await message.channel.send(f"🎶 Playing now → m!play {song}")

    except Exception as e:
        await message.channel.send("⚠️ Oops, couldn’t parse Gemini’s response.")
        await message.channel.send(f"Error details: {e}")

# (other commands unchanged: poll, flip, roast, compliment, etc.)
# ---------------- RUN -----------------
keep_alive()   # ⚠️ ONLY if you’re pinging this URL externally
bot.run(token, log_handler=handler, log_level=logging.DEBUG)
