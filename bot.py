import os
import logging
import subprocess
import asyncio
import re
import httpx
import base64
import socket
import time
from telegram import Update, constants
from telegram.ext import (
    ApplicationBuilder,
    ContextTypes,
    CommandHandler,
    MessageHandler,
    filters,
)
from telegram.request import HTTPXRequest

TOKEN = "8375331148:AAGLCsjm3UeP5eY55WckrcunSAETWKBlis0"
ADMIN_ID = 7591254790
LOG_FILE = "bot.log"
OLLAMA_URL = "http://localhost:11434"
PROXY_URL = "socks5://127.0.0.1:9050"

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
    handlers=[logging.FileHandler(LOG_FILE), logging.StreamHandler()],
)


def escape_md(text: str) -> str:
    return re.sub(r"([_*\[\]()~`>#+\-=|{}.!])", r"\\\1", text)


def ensure_tor() -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        if s.connect_ex(("127.0.0.1", 9050)) == 0:
            return True
    try:
        subprocess.Popen(["tor"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(30):
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                if s.connect_ex(("127.0.0.1", 9050)) == 0:
                    return True
            time.sleep(1)
    except Exception as e:
        logging.error(f"Tor start failed: {e}")
    return False


async def get_sys_info() -> str:
    try:
        with open("/proc/meminfo", "r") as f:
            lines = f.readlines()
        m = {
            line.split(":")[0]: int(line.split(":")[1].split()[0]) for line in lines[:3]
        }
        mem_total, mem_avail = m["MemTotal"] // 1024, m["MemAvailable"] // 1024
        mem_used = mem_total - mem_avail
        mem_pct = int((mem_used / mem_total) * 100)
        with open("/proc/loadavg", "r") as f:
            cpu_load = f.read().split()[0]
        uptime = subprocess.check_output("uptime -p", shell=True).decode().strip()
        batt_path = "/sys/class/power_supply/battery/"
        batt_cap, batt_temp, batt_status = "N/A", "N/A", "N/A"
        if os.path.exists(batt_path):
            try:
                with open(batt_path + "capacity", "r") as f:
                    batt_cap = f.read().strip() + "%"
                with open(batt_path + "temp", "r") as f:
                    batt_temp = str(int(f.read().strip()) / 10) + "°C"
                with open(batt_path + "status", "r") as f:
                    batt_status = f.read().strip()
            except Exception:
                pass
        return (
            r"🖥 *System Status*" + "\n\n"
            f"📈 *Load Avg:* `{cpu_load}`\n"
            rf"📟 *RAM:* `{mem_pct}%` \(`{mem_used}/{mem_total}MB`\)" + "\n"
            rf"🔋 *Battery:* `{batt_cap}` \(`{batt_temp}`, `{batt_status}`\)" + "\n"
            f"⏱ *Uptime:* `{uptime}`"
        )
    except Exception as e:
        return f"❌ Ошибка сбора данных: {escape_md(str(e))}"


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    await update.message.reply_text(
        "🚀 *Fold 4 Server Bot*\n\n"
        "🤖 AI доступен всем (выбери модель /models).\n"
        "🐚 Shell — только для админа.\n\n"
        "Команды:\n"
        "/status — состояние сервера\n"
        "/models — список моделей\n"
        "/set <name> — выбрать модель\n"
        "/sh <cmd> — выполнить команду",
        parse_mode=constants.ParseMode.MARKDOWN,
    )


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    await update.message.reply_text(
        await get_sys_info(), parse_mode=constants.ParseMode.MARKDOWN_V2
    )


async def shell(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if (
        not update.effective_user
        or update.effective_user.id != ADMIN_ID
        or not update.message
    ):
        return
    if not context.args:
        await update.message.reply_text("Введите команду после /sh")
        return
    cmd = " ".join(context.args)
    try:
        proc = await asyncio.create_subprocess_shell(
            cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await proc.communicate()
        res = (stdout + stderr).decode().strip() or "[No output]"
        await update.message.reply_text(
            f"```\n{escape_md(res[:4000])}\n```",
            parse_mode=constants.ParseMode.MARKDOWN_V2,
        )
    except Exception as e:
        await update.message.reply_text(
            f"❌ Ошибка: `{escape_md(str(e))}`",
            parse_mode=constants.ParseMode.MARKDOWN_V2,
        )


async def ai_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not context.user_data:
        return
    user_model = context.user_data.get("model")
    if not user_model:
        await update.message.reply_text("⚠️ Выбери модель через /models и /set")
        return
    text = update.message.text or update.message.caption or ""
    images = []
    if update.message.photo:
        photo_file = await update.message.photo[-1].get_file()
        img_bytes = await photo_file.download_as_bytearray()
        images.append(base64.b64encode(img_bytes).decode("utf-8"))
    status_msg = await update.message.reply_text(f"🤔 Думаю ({user_model})...")
    try:
        async with httpx.AsyncClient(timeout=180.0) as client:
            payload = {
                "model": user_model,
                "messages": [{"role": "user", "content": text, "images": images}],
                "stream": False,
            }
            r = await client.post(f"{OLLAMA_URL}/api/chat", json=payload)
            r.raise_for_status()
            content = r.json()["message"]["content"]
            await status_msg.edit_text(content)
    except Exception as e:
        await status_msg.edit_text(f"❌ Ollama Error: {str(e)}")


async def list_models(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(f"{OLLAMA_URL}/api/tags")
            r.raise_for_status()
            models = r.json().get("models", [])
            if not models:
                await update.message.reply_text("Моделей не найдено.")
                return
            text = "📦 *Доступные модели:*\n\n"
            for m in models:
                size_mb = m.get("size", 0) // 1024**2
                text += rf"• `{m['name']}` \({size_mb} MB\)" + "\n"
            await update.message.reply_text(
                text + "\n`/set <название>`", parse_mode=constants.ParseMode.MARKDOWN_V2
            )
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка: {str(e)}")


async def set_model(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args or not update.message or context.user_data is None:
        return
    model_name = context.args[0]
    context.user_data["model"] = model_name
    await update.message.reply_text(
        f"✅ Выбрана модель: `{escape_md(model_name)}`",
        parse_mode=constants.ParseMode.MARKDOWN_V2,
    )


if __name__ == "__main__":
    ensure_tor()
    t_req = HTTPXRequest(proxy=PROXY_URL, connect_timeout=30, read_timeout=30)
    app = (
        ApplicationBuilder()
        .token(TOKEN)
        .request(t_req)
        .get_updates_request(t_req)
        .build()
    )
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("sh", shell))
    app.add_handler(CommandHandler("models", list_models))
    app.add_handler(CommandHandler("set", set_model))
    app.add_handler(MessageHandler(filters.TEXT | filters.PHOTO, ai_handler))
    print("Fold 4 Server Bot is running (Tor ensured on 9050)...")
    app.run_polling()
