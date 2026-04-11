import os
import io
import asyncio
import httpx
import discord
import firebase_admin

from firebase_admin import credentials, db as firebase_db
from PIL import Image
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
from dotenv import load_dotenv

load_dotenv()

# ── Config ───────────────────────────────────────────────────────────────────
DISCORD_TOKEN    = os.getenv("DISCORD_TOKEN")
CHANNEL_ID       = int(os.getenv("CHANNEL_ID"))
API_SECRET       = os.getenv("API_SECRET", "")
FIREBASE_DB_URL  = os.getenv("FIREBASE_DB_URL")   # https://YOUR_PROJECT-default-rtdb.firebaseio.com
BG_COLOR         = (66, 165, 245)

# ── Firebase init ─────────────────────────────────────────────────────────────
cred = credentials.Certificate("firebase-key.json")
firebase_admin.initialize_app(cred, {"databaseURL": FIREBASE_DB_URL})

# ── Discord bot ───────────────────────────────────────────────────────────────
intents = discord.Intents.default()
bot     = discord.Client(intents=intents)
app     = FastAPI()

# ── Image: blue background + avatar ──────────────────────────────────────────
def build_image(headshot_bytes: bytes) -> bytes:
    avatar = Image.open(io.BytesIO(headshot_bytes)).convert("RGBA")
    size   = 420
    bg     = Image.new("RGB", (size, size), BG_COLOR)
    bg.paste(avatar, (0, 0), avatar)
    out = io.BytesIO()
    bg.save(out, format="PNG")
    out.seek(0)
    return out.read()

# ── HTTP helpers ──────────────────────────────────────────────────────────────
async def download_headshot(user_id: int) -> bytes:
    thumb_url = (
        f"https://thumbnails.roblox.com/v1/users/avatar-headshot"
        f"?userIds={user_id}&size=420x420&format=Png&isCircular=false"
    )
    async with httpx.AsyncClient() as client:
        r   = await client.get(thumb_url)
        url = r.json()["data"][0]["imageUrl"]
        img = await client.get(url)
        return img.content, url

# ── Firebase logger ───────────────────────────────────────────────────────────
def log_to_firebase(payload: dict, avatar_url: str):
    import time
    logs_ref = firebase_db.reference("logs")
    logs_ref.push({
        "userId":      payload.get("userId"),
        "username":    payload.get("username"),
        "displayName": payload.get("displayName"),
        "executor":    payload.get("executor"),
        "game":        payload.get("game"),
        "region":      payload.get("region"),
        "jobId":       payload.get("jobId"),
        "avatarUrl":   avatar_url,
        "timestamp":   int(time.time() * 1000)
    })

# ── Discord sender ────────────────────────────────────────────────────────────
async def send_to_discord(payload: dict, image_bytes: bytes):
    await bot.wait_until_ready()
    channel = bot.get_channel(CHANNEL_ID)
    if channel is None:
        channel = await bot.fetch_channel(CHANNEL_ID)

    dn       = payload.get("displayName", "?")
    un       = payload.get("username", "?")
    uid      = payload.get("userId", 0)
    executor = payload.get("executor", "Unknown")
    game     = payload.get("game", "Unknown")
    region   = payload.get("region", "Unknown")
    job_id   = payload.get("jobId", "Unknown")

    embed = discord.Embed(title=f"{dn} ({un}) just executed", color=discord.Color(0x42A5F5))
    embed.add_field(name="Profile Link", value=f"https://www.roblox.com/users/{uid}/profile", inline=False)
    embed.add_field(name="Executor", value=executor, inline=True)
    embed.add_field(name="Game",     value=game,     inline=True)
    embed.add_field(name="Region",   value=region,   inline=True)
    embed.add_field(name="JobId",    value=job_id,   inline=False)
    embed.set_image(url="attachment://avatar.png")

    file = discord.File(io.BytesIO(image_bytes), filename="avatar.png")
    await channel.send(embed=embed, file=file)

# ── API routes ────────────────────────────────────────────────────────────────
@app.post("/execute")
async def execute(request: Request):
    if API_SECRET:
        if request.headers.get("X-Secret", "") != API_SECRET:
            raise HTTPException(status_code=403, detail="Forbidden")

    payload = await request.json()
    user_id = payload.get("userId")
    if not user_id:
        raise HTTPException(status_code=400, detail="Missing userId")

    try:
        headshot_bytes, avatar_url = await download_headshot(user_id)
        image_bytes = build_image(headshot_bytes)
        log_to_firebase(payload, avatar_url)
        asyncio.create_task(send_to_discord(payload, image_bytes))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    return JSONResponse({"status": "ok"})

@app.get("/health")
async def health():
    return {"status": "running"}

@app.on_event("startup")
async def startup():
    asyncio.create_task(bot.start(DISCORD_TOKEN))
