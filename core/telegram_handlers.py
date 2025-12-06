import asyncio
import base64
import io
import os
import time
import httpx
from telethon import TelegramClient, events
from PIL import Image

from core.api_clients import transcribe_audio, gemini_generate, gemini_parse_task
from core.history_manager import load_history, save_history
from app.gui_logger import log_message
import core.scheduler as scheduler

# Глобальные переменные, которые будут установлены из main.py
app_state = {
    "bots_running": False,
    "see_my_msgs": False,
    "active_chat_id_map": {},
    "active_chat_entities": {},
    "friends": [],
    "noname": ("Noname", ""),
    "system_prompt": "",
    "bots_list": [],
    "running_bots": {},
    "temp_var": 0.7
}

SEM = asyncio.Semaphore(1)

def console_input(prompt):
    print(prompt, end='', flush=True)
    return input()

async def get_dialogs():
    from core.config_loader import load_tg_config
    api_id, api_hash, session = load_tg_config()
    temp_cli = TelegramClient(session, api_id, api_hash)
    try:
        await temp_cli.start(
            phone=lambda: console_input("Введите номер телефона: "),
            code_callback=lambda: console_input("Введите код авторизации: "),
            password=lambda: console_input("Введите пароль (2FA): ")
        )
        if not await temp_cli.is_user_authorized():
            log_message("User is not authorized. Please log in first.", level="error")
            return []

        dialogs = await temp_cli.get_dialogs(limit=400)
        out = []
        for d in dialogs:
            name = d.name or getattr(d.entity, "first_name", None) or str(d.id)
            out.append((name, d.entity))
        return out
    except Exception as e:
        log_message(f"[Dialogs Error] {e}", level="error")
        return []
    finally:
        if temp_cli.is_connected():
            await temp_cli.disconnect()

async def bot_message_handler(evt, bot_token: str, db_id: str):
    cli = evt.client
    if hasattr(evt, 'sender') and evt.sender and evt.sender.bot:
        return

    chat_id = evt.chat_id
    sender = await evt.get_sender()
    sender_name = getattr(sender, 'first_name', f"User {chat_id}")
    chat_title = str(chat_id)

    current_bot_data = next((bot for bot in app_state["bots_list"] if bot.get('token') == bot_token), None)
    if not current_bot_data:
        log_message(f"Could not find data for bot with token {bot_token[:5]}...", level="error")
        return

    bot_system_prompt = current_bot_data.get('system_prompt', '')
    history, _, hist_path = load_history(chat_title, mode='bot', bot_token=bot_token)

    log_message(f"-> Bot msg from [{sender_name} ({chat_id})]", level="info")

    entry = await _process_message_media(evt)
    if not entry:
        return

    if isinstance(entry, str) and any(keyword in entry.lower() for keyword in ["напомни", "напиши через", "запланируй"]):
        if await _handle_scheduler_task(entry, cli, chat_id, db_id, sender_name):
            return

    history.append({"role": "user", "content": entry})
    save_history(hist_path, history, "")

    async with SEM:
        try:
            reply = await gemini_generate(history, friend_name=sender_name, temperature=float(app_state["temp_var"]), custom_prompt="", system_prompt=bot_system_prompt)
        except httpx.HTTPStatusError as http_err:
            log_message(f"  [Gemini API Error] HTTP Status {http_err.response.status_code}.", level="error")
            return
        except Exception as e:
            log_message(f"  [Gemini Error] An unexpected error occurred: {e}", level="error")
            return

    if reply:
        log_message(f"  [Bot] {reply}", level="focus")
        history.append({"role": "assistant", "content": reply})
        save_history(hist_path, history, "")
        if bot_token in app_state["running_bots"]:
            await cli.send_message(chat_id, reply)

async def multi_chat_handler(evt):
    if not app_state["bots_running"]: return

    cli = evt.client
    me = await cli.get_me()

    # ВРЕМЕННЫЙ КОД ДЛЯ ТЕСТИРОВАНИЯ: Ответ на свои же сообщения
    # if not app_state["see_my_msgs"]:
    #     if getattr(evt.message, "out", False): return
    #     if getattr(evt.message, "sender_id", None) == me.id: return
    # КОНЕЦ ВРЕМЕННОГО КОДА

    chat_id = evt.chat_id
    chat_title = app_state["active_chat_id_map"].get(chat_id)
    if not chat_title: return

    chat_data = app_state["active_chat_entities"].get(chat_title, {})
    friend_index = chat_data.get("friend_index", len(app_state["friends"]))
    friend_name = app_state["noname"][0] if friend_index >= len(app_state["friends"]) else app_state["friends"][friend_index][0]

    history, custom_prompt, hist_path = load_history(chat_title, mode='user')
    log_message(f"-> Msg in [{chat_title}]", level="info")

    entry = await _process_message_media(evt)
    if not entry:
        return

    if isinstance(entry, str) and any(keyword in entry.lower() for keyword in ["напомни", "напиши через", "запланируй"]):
        if await _handle_scheduler_task(entry, cli, chat_id, "user_mode", chat_title):
            return

    history.append({"role": "user", "content": entry})
    save_history(hist_path, history, custom_prompt)

    async with SEM:
        try:
            reply = await gemini_generate(history, friend_name=friend_name, temperature=float(app_state["temp_var"]), custom_prompt=custom_prompt, system_prompt=app_state["system_prompt"])
        except httpx.HTTPStatusError as http_err:
            log_message(f"  [Gemini API Error] HTTP Status {http_err.response.status_code}.", level="error")
            return
        except Exception as e:
            log_message(f"  [Gemini Error] An unexpected error occurred: {e}", level="error")
            return

    if reply:
        log_message(f"  [Sanya] {reply}", level="focus")
        history.append({"role": "assistant", "content": reply})
        save_history(hist_path, history, custom_prompt)
        if app_state["bots_running"]:
            await cli.send_message(chat_id, reply)

async def _process_message_media(evt):
    is_voice = evt.message.voice
    media = evt.media

    if is_voice:
        log_message(f"  [User] <голосовое сообщение>", level="user")
        buf = io.BytesIO()
        await evt.client.download_media(evt.message, buf)
        transcribed_text = await transcribe_audio(buf)
        buf.close()
        if transcribed_text:
            log_message(f"  [Transcription] {transcribed_text}", level="info")
            return transcribed_text
        else:
            log_message(f"  [Transcription] Не удалось распознать речь.", level="error")
            return None

    elif media:
        log_message(f"  [User] <media>", level="user")
        mime = _get_media_mime_type(evt)
        buf = io.BytesIO()
        await evt.client.download_media(media, buf)
        data = buf.getvalue()
        buf.close()

        supported_mimes = {"image/png", "image/jpeg", "image/webp", "image/gif", "image/heic"}
        if mime not in supported_mimes:
            try:
                log_message(f"  [Image] Unsupported type '{mime}', converting to PNG...", level="debug")
                img = Image.open(io.BytesIO(data))
                out_buf = io.BytesIO()
                img.save(out_buf, format="PNG")
                data = out_buf.getvalue()
                mime = "image/png"
            except Exception as e:
                log_message(f"[Image Error] Failed to convert image: {e}", level="error")
                return None

        b64 = base64.b64encode(data).decode("ascii")
        return f"DATA:{mime};base64,{b64}"

    else:
        text = (evt.raw_text or "").strip()
        if text:
            log_message(f"  [User] {text}", level="user")
            return text
    return None

def _get_media_mime_type(evt):
    try:
        if getattr(evt.message, "file", None) and getattr(evt.message.file, "mime_type", None):
            return evt.message.file.mime_type
        elif getattr(evt, "photo", None) or getattr(evt.message, "photo", None):
            return "image/jpeg"
    except Exception:
        return None

async def _handle_scheduler_task(entry, cli, chat_id, db_id, context_name):
    log_message(f"🔎 Обнаружен запрос на задачу в '{context_name}'. Парсинг...", level="warning")
    try:
        task_data = await gemini_parse_task(entry)
        if task_data and task_data.get("minutes"):
            addressee = task_data.get("addressee", "тебе")
            task_text = task_data.get("text", "")
            minutes = int(task_data.get("minutes", 0))

            if not task_text or minutes <= 0:
                raise ValueError("Некорректные данные от Gemini для задачи.")

            execution_time = int(time.time()) + minutes * 60
            scheduler.add_task(db_id, chat_id, addressee, task_text, execution_time)

            confirmation_msg = f"✅ Ок, напомню '{addressee}': '{task_text}' через {minutes} мин."
            await cli.send_message(chat_id, confirmation_msg)
            log_message(f"  [Scheduler] {confirmation_msg}", level="info")
            return True
        else:
            log_message(f"⚠️ Не удалось распарсить задачу, обрабатываю как обычное сообщение.", level="warning")
    except Exception as e:
        log_message(f"❗️ Ошибка обработки задачи: {e}", level="error")
    return False
