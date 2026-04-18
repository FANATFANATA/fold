import os
import logging
import subprocess
import asyncio
import re
import httpx
from telegram import Update, constants
from telegram.ext import (
    ApplicationBuilder,
    ContextTypes,
    CommandHandler,
    MessageHandler,
    filters,
)

TOKEN = "8375331148:AAGLCsjm3UeP5eY55WckrcunSAETWKBlis0"
ADMIN_ID = 7591254790
LOG_FILE = "bot.log"
OLLAMA_URL = "http://localhost:11434"

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
    handlers=[logging.FileHandler(LOG_FILE), logging.StreamHandler()],
)


def escape_md(text):
    return re.sub(f'([{re.escape(r"_*[]()~`>#+-=|{}.!")}])', r"\\\1", text)


async def get_sys_info():
    try:
        with open("/proc/meminfo", "r") as f:
            m = {
                l.split(":")[0]: int(l.split(":")[1].split()[0])
                for l in f.readlines()[:3]
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
            with open(batt_path + "capacity", "r") as f:
                batt_cap = f.read().strip() + "%"
            with open(batt_path + "temp", "r") as f:
                batt_temp = str(int(f.read().strip()) / 10) + "°C"
            with open(batt_path + "status", "r") as f:
                batt_status = f.read().strip()

        return (
            f"🖥 *System Status*\n\n"
            f"📈 *Load Avg:* `{cpu_load}`\n"
            f"📟 *RAM:* `{mem_pct}%` \(`{mem_used}/{mem_total}MB`\)\n"
            f"🔋 *Battery:* `{batt_cap}` \(`{batt_temp}`, `{batt_status}`\)\n"
            f"⏱ *Uptime:* `{uptime}`"
        )
    except Exception as e:
        return f"❌ Ошибка сбора данных: {escape_md(str(e))}"


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
    await update.message.reply_text(
        await get_sys_info(), parse_mode=constants.ParseMode.MARKDOWN_V2
    )


async def shell(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    cmd = " ".join(context.args)
    if not cmd:
        return
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
    user_model = context.user_data.get("model")
    if not user_model:
        await update.message.reply_text("⚠️ Выбери модель через /models и /set")
        return

    text = update.message.text or update.message.caption or ""
    images = []
    if update.message.photo:
        import base64

        file = await update.message.photo[-1].get_file()
        img_bytes = await file.download_as_bytearray()
        images.append(base64.b64encode(img_bytes).decode("utf-8"))

    status_msg = await update.message.reply_text(f"🤔 Думаю ({user_model})...")

    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
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
                text += f"• `{m['name']}` \({m['size'] // 1024**2} MB\)\n"
            await update.message.reply_text(
                text + "\n`/set <название>`", parse_mode=constants.ParseMode.MARKDOWN_V2
            )
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка: {str(e)}")


async def set_model(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        return
    context.user_data["model"] = context.args[0]
    await update.message.reply_text(
        f"✅ Выбрана модель: `{escape_md(context.args[0])}`",
        parse_mode=constants.ParseMode.MARKDOWN_V2,
    )


if __name__ == "__main__":
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("sh", shell))
    app.add_handler(CommandHandler("models", list_models))
    app.add_handler(CommandHandler("set", set_model))
    app.add_handler(MessageHandler(filters.TEXT | filters.PHOTO, ai_handler))
    print("Fold 4 Server Bot is running...")
    app.run_polling()
