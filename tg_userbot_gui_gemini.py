#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import asyncio
import base64
import io
import json
import os
import re
import threading
import time
import tkinter as tk
import importlib
from tkinter import ttk, messagebox, scrolledtext
from collections import deque
import sys

import httpx
from telethon import TelegramClient, events
from deepgram import DeepgramClient, PrerecordedOptions, FileSource
from PIL import Image

import scheduler
# ===================== Папки/файлы =====================
# onefile-режим PyInstaller: писать рядом с .exe
try:
    if getattr(sys, "frozen", False):
        BASE_DIR = os.path.dirname(sys.executable)
    else:
        BASE_DIR = os.path.dirname(os.path.abspath(__file__))
except Exception:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

CHATS_DIR   = os.path.join(BASE_DIR, "Chats")
API_FILE    = os.path.join(BASE_DIR, "api_text_model.json")
TG_FILE     = os.path.join(BASE_DIR, "telegram_api.json")
PROMPT_FILE = os.path.join(BASE_DIR, "SYSTEM_PROMPT.json")
DEEPGRAM_FILE = os.path.join(BASE_DIR, "deepgram_api.json")
BOTS_FILE = os.path.join(BASE_DIR, "bots.json")
STATE_FILE = os.path.join(BASE_DIR, "gui_state.json")
os.makedirs(CHATS_DIR, exist_ok=True)

# ===================== Конфиги API/Telegram/Bots =====================
def ensure_deepgram_config():
    if os.path.exists(DEEPGRAM_FILE):
        return
    with open(DEEPGRAM_FILE, "w", encoding="utf-8") as f:
        json.dump({"api_key": ""}, f, ensure_ascii=False, indent=2)

def load_deepgram_config():
    ensure_deepgram_config()
    with open(DEEPGRAM_FILE, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    api_key = (cfg.get("api_key") or "").strip()
    if not api_key:
        raise RuntimeError("В deepgram_api.json пустой api_key.")
    return api_key

def ensure_api_config():
    if os.path.exists(API_FILE):
        return
    cfg = {
        "provider": "gemini",
        "base_url": "https://generativelanguage.googleapis.com",
        "api_key": "",  # ВСТАВЬ СВОЙ GOOGLE API KEY
        "model": "gemini-1.5-flash",
        "rpm_limit": 45
    }
    with open(API_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)

def ensure_tg_config():
    if os.path.exists(TG_FILE):
        return
    with open(TG_FILE, "w", encoding="utf-8") as f:
        json.dump(
            {"api_id": 0, "api_hash": "", "session_file": "userbot_session.session"},
            f, ensure_ascii=False, indent=2
        )

def _normalize_gemini_model_name(model: str) -> str:
    """Возвращает имя модели без префиксов вроде `models/` и суффиксов `:generateContent`."""
    name = (model or "").strip()
    if not name:
        return ""
    if name.startswith("models/"):
        name = name.split("/", 1)[1]
    if name.endswith(":generateContent"):
        name = name[: -len(":generateContent")]
    return name.strip()


def load_api_config():
    ensure_api_config()
    with open(API_FILE, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    provider = (cfg.get("provider") or "gemini").strip().lower()
    base_url = (cfg.get("base_url") or "").strip()
    api_key  = (cfg.get("api_key") or "").strip()
    model    = _normalize_gemini_model_name(cfg.get("model"))
    rpm      = int(cfg.get("rpm_limit") or 45)

    if provider != "gemini":
        raise RuntimeError("В api_text_model.json provider должен быть 'gemini'.")
    if not api_key:
        raise RuntimeError("В api_text_model.json пустой api_key (Google API Key).")
    if not model:
        raise RuntimeError("В api_text_model.json не указана model (например, gemini-1.5-flash).")

    if not base_url:
        base_url = "https://generativelanguage.googleapis.com"

    base_url = base_url.rstrip("/")
    # Пользователи иногда добавляют в base_url сегменты /v1 или /v1beta. Эти части нужно
    # убрать, иначе итоговый путь получится неверным и Google вернёт 404.
    for suffix in ("/v1beta/models", "/v1beta", "/v1/models", "/v1"):
        if base_url.endswith(suffix):
            base_url = base_url[: -len(suffix)].rstrip("/")

    if not base_url:
        raise RuntimeError("Поле base_url в api_text_model.json не должно быть пустым.")

    # Gemini "latest" models (и другие свежие версии) сейчас доступны только через v1beta.
    # Если пользоваться путём /v1/..., то Google возвращает 404 ("model ... is not found for API
    # version v1") — именно эту ошибку видели пользователи. Поэтому всегда вызываем v1beta.
    endpoint = f"{base_url}/v1beta/models/{model}:generateContent?key={api_key}"
    return endpoint, model, rpm

def load_tg_config():
    ensure_tg_config()
    with open(TG_FILE, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    api_id   = int(cfg.get("api_id") or 0)
    api_hash = (cfg.get("api_hash") or "").strip()
    session  = (cfg.get("session_file") or "userbot_session.session").strip()

    if not os.path.isabs(session):
        session = os.path.join(BASE_DIR, session)

    if not api_id or not api_hash:
        raise RuntimeError("Заполни api_id и api_hash в telegram_api.json")
    return api_id, api_hash, session


def ensure_bots_config():
    if os.path.exists(BOTS_FILE):
        return
    # Список словарей, каждый содержит token и опционально desc
    with open(BOTS_FILE, "w", encoding="utf-8") as f:
        json.dump([], f, ensure_ascii=False, indent=2)

def load_bots():
    ensure_bots_config()
    with open(BOTS_FILE, "r", encoding="utf-8") as f:
        bots = json.load(f)
    # Для обратной совместимости добавляем поле system_prompt, если его нет
    for bot in bots:
        if "system_prompt" not in bot:
            bot["system_prompt"] = ""
    return bots

def save_bots(bots_list):
    with open(BOTS_FILE, "w", encoding="utf-8") as f:
        json.dump(bots_list, f, ensure_ascii=False, indent=2)


# ===================== SYSTEM_PROMPT.json =====================
def _default_system_prompt() -> str:
    return (
        ""
    )

def _default_friends():
    return [
        {"name": "admin",    "desc": "создатель веселый рассеяный общяйся как удобно ему!"},
        {"name": "1",  "desc": "лучший друг — можно шутить и поливать гадостями и расслабляться"},
        {"name": "2",   "desc": ""},
        {"name": "3",    "desc": ""},
        {"name": "4",     "desc":""}
    ]

def ensure_prompt_config():
    if os.path.exists(PROMPT_FILE):
        return
    data = {
        "system_prompt": _default_system_prompt(),
        "friends": _default_friends(),
        "noname": {"name": "Noname", "desc": "собеседник не в списке — общайся по контексту"}
    }
    with open(PROMPT_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def load_prompt_config():
    ensure_prompt_config()
    with open(PROMPT_FILE, "r", encoding="utf-8") as f:
        js = json.load(f)

    system_prompt = js.get("system_prompt")
    if not system_prompt or not isinstance(system_prompt, str):
        log_message(
            "Ключ 'system_prompt' не найден или пуст в SYSTEM_PROMPT.json. Используется промпт по умолчанию.",
            level="error"
        )
        system_prompt = _default_system_prompt()

    friends_items = js.get("friends")
    if not isinstance(friends_items, list) or not friends_items:
        friends_items = _default_friends()
    friends = [(str(i.get("name") or "Noname"), str(i.get("desc") or "")) for i in friends_items]
    noname_obj = js.get("noname") or {"name": "Noname", "desc": "собеседник не в списке — общайся по контексту"}
    noname = (str(noname_obj.get("name") or "Noname"), str(noname_obj.get("desc") or ""))
    return system_prompt, friends, noname

SYSTEM_PROMPT_TXT = None
FRIENDS = None
NONAME = None

# ===================== Глобальное состояние =====================
SEM = asyncio.Semaphore(1)
aio_loop = None
aio_loop_ready = threading.Event()
running_bots = {}  # token -> {client, handler, scheduler_task}
bots_running = False
scheduler_task = None # This will now be part of running_bots

root = None
chat_listbox = None
chat_search = None
friend_combo = None
log_text = None
start_btn = stop_btn = restart_btn = clear_btn = None
status_label = None
see_my_msgs_var = None
temp_var = None
temp_value_label = None

chat_entities = []
filtered_chats = []
active_chats_listbox = None
active_chat_entities = {}
active_chat_id_map = {}
custom_prompt_text = None
gui_message_text = None
verbose_logging_var = None

# ===================== Rate limit (RPM) =====================
_rate_lock = asyncio.Lock()
_rate_window = deque()
async def acquire_rate_slot(limit_per_min: int):
    if not limit_per_min:
        return
    async with _rate_lock:
        now = time.time()
        while _rate_window and now - _rate_window[0] > 60:
            _rate_window.popleft()
        if len(_rate_window) >= limit_per_min:
            wait = 60 - (now - _rate_window[0]) + 0.02
            await asyncio.sleep(max(0.0, wait))
        _rate_window.append(time.time())

# ===================== История =====================
def _sanitize_filename(name: str) -> str:
    name = name or "chat"
    name = re.sub(r"[\u0000-\u001F\u007F]", "", name)
    name = re.sub(r'[<>:"/\\|?*]', "_", name).strip(" .")
    return (name or "chat")[:120]

def _history_path(chat_title: str, mode: str, bot_token: str = None) -> str:
    if mode == 'bot':
        if not bot_token:
            raise ValueError("bot_token is required for bot mode history path")
        bot_id = bot_token.split(':', 1)[0]

        # Special case for the bot's own global prompt file
        if chat_title == f"bot_prompt_{bot_id}":
            return os.path.join(CHATS_DIR, f"{chat_title}.json")

        # Per-user history files for the bot
        bot_chat_dir = os.path.join(CHATS_DIR, f"bot_{bot_id}")
        os.makedirs(bot_chat_dir, exist_ok=True)
        return os.path.join(bot_chat_dir, f"{_sanitize_filename(chat_title)}.json")
    else: # user mode
        return os.path.join(CHATS_DIR, f"{_sanitize_filename(chat_title)}.json")

def load_history(chat_title: str, friend_name: str, mode: str, bot_token: str = None):
    p = _history_path(chat_title, mode=mode, bot_token=bot_token)
    custom_prompt = ""
    history = []

    if os.path.exists(p):
        try:
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                history = data.get("history", [])
                custom_prompt = data.get("custom_prompt", "")
                return history, custom_prompt, p
        except Exception:
            pass

    history = []
    save_history(p, history, custom_prompt)
    return history, custom_prompt, p

def save_history(path: str, history, custom_prompt: str):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(
            {"custom_prompt": custom_prompt, "history": history},
            f, ensure_ascii=False, indent=2
        )

def clear_log():
    if not log_text: return
    log_text.configure(state='normal'); log_text.delete('1.0','end'); log_text.configure(state='disabled')

def log_message(text: str, level: str = "info"):
    log_file_path = os.path.join(BASE_DIR, "console.log")
    try:
        with open(log_file_path, "a", encoding="utf-8") as f:
            f.write(f"[{level.upper()}] {text}\n")
    except Exception as e:
        print(f"Failed to write to log file: {e}")

    if not root or not log_text:
        print(text)
        return

    tag_map = {
        "info": "green", "error": "red", "focus": "violet", "debug": "grey",
        "warning": "yellow", "user": "white"
    }
    tag = tag_map.get(level, "white")

    def _append_to_gui():
        if level == 'debug' and not (verbose_logging_var and verbose_logging_var.get()):
            return
        log_text.configure(state='normal')
        log_text.insert('end', text + '\n', tag)
        log_text.see('end')
        log_text.configure(state='disabled')

    root.after(0, _append_to_gui)

def render_history_to_log(history):
    shown = 0
    for m in history:
        r = m.get("role"); c = m.get("content","")
        if r == "system": continue
        if isinstance(c, str) and c.startswith("DATA:image/"): c = "<media>"
        if r == "user": log_message(f"[User] {c}", level="user")
        elif r == "assistant": log_message(f"[Sanya] {c}", level="focus")
        else: log_message(str(c), level="user")
        shown += 1
    log_message(f"📜 История загружена: {shown} сообщений.", level="info")

# ===================== Gemini (REST v1beta) =====================
def _history_to_gemini_contents(history):
    contents = []
    for m in history:
        role = m.get("role")
        if role == "system":
            continue
        role_map = "user" if role == "user" else "model"
        parts = []
        content = m.get("content", "")
        if isinstance(content, str) and content.startswith("DATA:"):
            try:
                prefix, b64 = content.split(",", 1)
                mime = prefix.split(":",1)[1].split(";")[0]
                parts.append({"inline_data":{"mime_type": mime, "data": b64}})
            except Exception:
                parts.append({"text": str(content)})
        else:
            parts.append({"text": str(content)})
        contents.append({"role": role_map, "parts": parts})
    return contents


def _gemini_safety_settings(block_level="BLOCK_NONE"):
    """Формирует настройки безопасного режима Gemini с единым порогом."""
    categories = [
        "HARM_CATEGORY_HARASSMENT",
        "HARM_CATEGORY_HATE_SPEECH",
        "HARM_CATEGORY_SEXUALLY_EXPLICIT",
        "HARM_CATEGORY_DANGEROUS_CONTENT",
    ]
    return [{"category": cat, "threshold": block_level} for cat in categories]

async def transcribe_audio(media_buffer):
    try:
        api_key = load_deepgram_config()
        dg_client = DeepgramClient(api_key)

        media_buffer.seek(0)
        payload: FileSource = {"buffer": media_buffer.read()}
        options = PrerecordedOptions(
            model="nova-2-general",
            language="ru",
            smart_format=True
        )

        response = await asyncio.to_thread(
            dg_client.listen.rest.v("1").transcribe_file, payload, options
        )

        transcript = response["results"]["channels"][0]["alternatives"][0]["transcript"]
        return transcript

    except Exception as e:
        log_message(f"[Deepgram Error] {e}", level="error")
        return None

async def gemini_generate(history, friend_name: str, temperature: float, custom_prompt: str):
    print(">>> ЗАПУСТИЛАСЬ НОВАЯ ВЕРСИЯ gemini_generate <<<")
    endpoint, model, rpm = load_api_config()
    full_system_prompt = f"{SYSTEM_PROMPT_TXT}\n\n{custom_prompt}\n\nСейчас ты общаешься с: {friend_name}."

    contents = _history_to_gemini_contents(history)

    payload = {
        "systemInstruction": {
            "role": "system",
            "parts": [{"text": full_system_prompt}]
        },
        "contents": contents,
        "generationConfig": {
            "temperature": float(temperature),
            "topP": 0.95,
            "maxOutputTokens": 1024
        },
        "safetySettings": _gemini_safety_settings()
    }
    headers = {"Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=90) as cli:
        await acquire_rate_slot(rpm)
        r = await cli.post(endpoint, headers=headers, json=payload)
        log_message(f"[API Response Raw] {r.text}", level="debug")
        if r.status_code >= 400:
            try:
                js = r.json()
                raise httpx.HTTPStatusError(json.dumps(js, ensure_ascii=False), request=r.request, response=r)
            except Exception:
                r.raise_for_status()
        js = r.json()
        feedback = js.get("promptFeedback") or {}
        if feedback.get("blockReason"):
            reason = feedback.get("blockReason")
            log_message(f"[Gemini Safety Blocked] {reason}", level="error")
            return ""
        cand = (js.get("candidates") or [])
        if not cand:
            return ""
        content = cand[0].get("content") or {}
        parts = content.get("parts") or []
        out = []
        for p in parts:
            if "text" in p:
                out.append(p["text"])
        return "\n".join(out).strip()


async def gemini_parse_task(text: str):
    """Использует Gemini для извлечения данных задачи из текста."""
    endpoint, model, rpm = load_api_config()
    prompt = (
        "Ты — ИИ-парсер для планировщика задач. Твоя задача — извлечь из текста три параметра: "
        "кому адресована задача (addressee), что нужно сделать (text) и через сколько минут (minutes). "
        "Если адресат не указан, используй 'мне'. Если время не указано, верни 0. "
        "Ответ должен быть ТОЛЬКО в формате JSON. Пример: "
        '{"addressee": "Петя", "text": "подойти к компьютеру", "minutes": 15}'
    )
    payload = {
        "contents": [
            {"role": "user", "parts": [{"text": prompt}]},
            {"role": "model", "parts": [{"text": "OK"}]},
            {"role": "user", "parts": [{"text": text}]}
        ],
        "generationConfig": {"temperature": 0.0, "maxOutputTokens": 200},
        "safetySettings": _gemini_safety_settings("BLOCK_ONLY_HIGH")
    }
    headers = {"Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=30) as cli:
        await acquire_rate_slot(rpm)
        r = await cli.post(endpoint, headers=headers, json=payload)
        r.raise_for_status()
        js = r.json()
        feedback = js.get("promptFeedback") or {}
        if feedback.get("blockReason"):
            log_message(f"[Gemini Safety Blocked] {feedback.get('blockReason')}", level="error")
            return None
        cand = (js.get("candidates") or [])
        if not cand: return None
        content = cand[0].get("content") or {}
        parts = content.get("parts") or []
        if not parts or "text" not in parts[0]: return None

        raw_text = parts[0]["text"]
        match = re.search(r'\{.*\}', raw_text, re.DOTALL)
        if not match: return None

        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            return None

# ===================== Async event loop =====================
def start_background_loop():
    global aio_loop
    aio_loop = asyncio.new_event_loop()
    asyncio.set_event_loop(aio_loop)
    aio_loop_ready.set()
    aio_loop.run_forever()

def run_async(coro):
    aio_loop_ready.wait()
    return asyncio.run_coroutine_threadsafe(coro, aio_loop)

# ===================== Telegram =====================
async def get_dialogs():
    # Эта функция вызывается для заполнения GUI и должна работать независимо.
    # Она создает временный клиент только для получения списка диалогов.
    api_id, api_hash, session = load_tg_config()
    temp_cli = TelegramClient(session, api_id, api_hash)
    try:
        await temp_cli.connect()
        # Если сессия не авторизована, get_dialogs вызовет исключение
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

    current_bot_data = next((bot for bot in bots_list if bot.get('token') == bot_token), None)
    if not current_bot_data:
        log_message(f"Could not find data for bot with token {bot_token[:5]}...", level="error")
        return

    bot_system_prompt = current_bot_data.get('system_prompt', '')
    history, _, hist_path = load_history(chat_title, sender_name, mode='bot', bot_token=bot_token)

    log_message(f"-> Bot msg from [{sender_name} ({chat_id})]", level="info")

    entry = None
    is_voice = evt.message.voice
    media = evt.media

    if is_voice:
        log_message(f"  [User] <голосовое сообщение>", level="user")
        buf = io.BytesIO()
        await cli.download_media(evt.message, buf)
        transcribed_text = await transcribe_audio(buf)
        buf.close()
        if transcribed_text:
            log_message(f"  [Transcription] {transcribed_text}", level="info")
            entry = transcribed_text
        else:
            log_message(f"  [Transcription] Не удалось распознать речь.", level="error")

    elif media:
        log_message(f"  [User] <media>", level="user")
        mime = None
        try:
            if getattr(evt.message, "file", None) and getattr(evt.message.file, "mime_type", None):
                mime = evt.message.file.mime_type
            elif getattr(evt, "photo", None) or getattr(evt.message, "photo", None):
                mime = "image/jpeg"
        except Exception:
            pass

        buf = io.BytesIO()
        await cli.download_media(media, buf)
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
                return

        b64 = base64.b64encode(data).decode("ascii")
        entry = f"DATA:{mime};base64,{b64}"

    else:
        text = (evt.raw_text or "").strip()
        if text:
            log_message(f"  [User] {text}", level="user")
            entry = text

    if not entry:
        return

    # Scheduler check for bots
    if isinstance(entry, str) and any(keyword in entry.lower() for keyword in ["напомни", "напиши через", "запланируй"]):
        log_message(f"🔎 Обнаружен запрос на задачу от '{sender_name}'. Парсинг...", level="warning")
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
                return
            else:
                log_message(f"⚠️ Не удалось распарсить задачу, обрабатываю как обычное сообщение.", level="warning")
        except Exception as e:
            log_message(f"❗️ Ошибка обработки задачи: {e}", level="error")

    history.append({"role": "user", "content": entry})
    # For bots, we don't save the global prompt with every message, just the user history
    save_history(hist_path, history, "")

    async with SEM:
        try:
            # Используем системный промпт конкретного бота
            reply = await gemini_generate(history, friend_name=sender_name, temperature=float(temp_var.get()), custom_prompt=bot_system_prompt)
        except httpx.HTTPStatusError as http_err:
            log_message(f"  [Gemini API Error] HTTP Status {http_err.response.status_code}. Проверьте ваш API-ключ или имя модели.", level="error")
            return
        except Exception as e:
            log_message(f"  [Gemini Error] Произошла непредвиденная ошибка: {e}", level="error")
            return

    if reply:
        log_message(f"  [Bot] {reply}", level="focus")
        history.append({"role": "assistant", "content": reply})
        save_history(hist_path, history, "")
        if bot_token in running_bots:
            await cli.send_message(chat_id, reply)


async def multi_chat_handler(evt):
    if not bots_running: return

    cli = evt.client
    me = await cli.get_me()

    if not see_my_msgs_var.get():
        if getattr(evt.message, "out", False): return
        if getattr(evt.message, "sender_id", None) == me.id: return

    chat_id = evt.chat_id
    chat_title = active_chat_id_map.get(chat_id)
    if not chat_title: return

    chat_data = active_chat_entities.get(chat_title, {})
    friend_index = chat_data.get("friend_index", len(FRIENDS))

    if friend_index >= len(FRIENDS):
        friend_index = 0

    friend_name = FRIENDS[friend_index][0]
    history, custom_prompt, hist_path = load_history(chat_title, friend_name, mode='user')

    log_message(f"-> Msg in [{chat_title}]", level="info")

    entry = None
    is_voice = evt.message.voice
    media = evt.media

    if is_voice:
        log_message(f"  [User] <голосовое сообщение>", level="user")
        buf = io.BytesIO()
        await cli.download_media(evt.message, buf)
        transcribed_text = await transcribe_audio(buf)
        buf.close()
        if transcribed_text:
            log_message(f"  [Transcription] {transcribed_text}", level="info")
            entry = transcribed_text
        else:
            log_message(f"  [Transcription] Не удалось распознать речь.", level="error")

    elif media:
        log_message(f"  [User] <media>", level="user")
        mime = None
        try:
            if getattr(evt.message, "file", None) and getattr(evt.message.file, "mime_type", None):
                mime = evt.message.file.mime_type
            elif getattr(evt, "photo", None) or getattr(evt.message, "photo", None):
                mime = "image/jpeg"
        except Exception:
            pass

        buf = io.BytesIO()
        await cli.download_media(media, buf)
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
                return

        b64 = base64.b64encode(data).decode("ascii")
        entry = f"DATA:{mime};base64,{b64}"

    else:
        text = (evt.raw_text or "").strip()
        if text:
            log_message(f"  [User] {text}", level="user")
            entry = text

    if not entry:
        return

    # Проверяем, не является ли это командой для планировщика
    if isinstance(entry, str) and any(keyword in entry.lower() for keyword in ["напомни", "напиши через", "запланируй"]):
        log_message(f"🔎 Обнаружен запрос на задачу в чате '{chat_title}'. Парсинг...", level="warning")
        try:
            task_data = await gemini_parse_task(entry)
            if task_data and task_data.get("minutes"):
                addressee = task_data.get("addressee", "мне")
                task_text = task_data.get("text", "")
                minutes = int(task_data.get("minutes", 0))

                if not task_text or minutes <= 0:
                    raise ValueError("Некорректные данные от Gemini для задачи.")

                execution_time = int(time.time()) + minutes * 60

                scheduler.add_task("user_mode", chat_id, addressee, task_text, execution_time)

                confirmation_msg = f"✅ Ок, напомню '{addressee}': '{task_text}' через {minutes} мин."
                await cli.send_message(chat_id, confirmation_msg)
                log_message(f"  [Scheduler] {confirmation_msg}", level="info")
                return
            else:
                log_message(f"⚠️ Не удалось распарсить задачу, обрабатываю как обычное сообщение.", level="warning")
        except Exception as e:
            log_message(f"❗️ Ошибка обработки задачи: {e}", level="error")

    history.append({"role": "user", "content": entry})
    save_history(hist_path, history, custom_prompt)

    async with SEM:
        try:
            reply = await gemini_generate(history, friend_name=friend_name, temperature=float(temp_var.get()), custom_prompt=custom_prompt)
        except httpx.HTTPStatusError as http_err:
            log_message(f"  [Gemini API Error] HTTP Status {http_err.response.status_code}. Проверьте ваш API-ключ или имя модели.", level="error")
            return
        except Exception as e:
            log_message(f"  [Gemini Error] Произошла непредвиденная ошибка: {e}", level="error")
            return

    if reply:
        log_message(f"  [Sanya] {reply}", level="focus")
        history.append({"role": "assistant", "content": reply})
        save_history(hist_path, history, custom_prompt)
        if bots_running:
            await cli.send_message(chat_id, reply)

async def start_user_listeners():
    global handler_ref, bots_running, active_chat_id_map, scheduler_task, client
    importlib.reload(scheduler)
    running_bots.clear() # Ensure no bots are marked as running
    api_id, api_hash, session = load_tg_config()
    client = TelegramClient(session, api_id, api_hash)
    await client.start()

    # User mode has its own single scheduler task
    if scheduler_task and not scheduler_task.done():
        scheduler_task.cancel()
    db_id = "user_mode"
    scheduler.init_db(db_id)
    # The scheduler for user mode uses the main user client
    scheduler_task = asyncio.create_task(scheduler.scheduler_loop(db_id, client, log_message))
    me = await client.get_me()

    active_chat_id_map = { v["entity"].id: k for k, v in active_chat_entities.items() }
    entities = [v["entity"] for v in active_chat_entities.values()]

    handler_ref = multi_chat_handler
    client.add_event_handler(handler_ref, events.NewMessage(chats=entities))

    clear_log()
    log_message(f"✅ [User Mode] Подключено как {me.first_name} (id={me.id})", level="focus")
    log_message(f"🤖 Провайдер: gemini | Модель: {load_api_config()[1]}", level="info")
    log_message(f"🚀 Мост запущен для {len(entities)} чатов. Жду сообщения.", level="info")


async def start_bot_listeners(bot_token: str):
    global bots_running
    importlib.reload(scheduler)
    bot_id = bot_token.split(':', 1)[0]
    session_name = f"bot_{bot_id}.session"
    session_path = os.path.join(BASE_DIR, session_name)
    api_id, api_hash, _ = load_tg_config()

    bot_client = TelegramClient(session_path, api_id, api_hash)
    bot_desc = "N/A"
    try:
        bot_data = next((b for b in bots_list if b.get('token') == bot_token), None)
        if bot_data: bot_desc = bot_data.get('desc', 'N/A')

        await bot_client.start(bot_token=bot_token)
        me = await bot_client.get_me()

        handler = lambda evt: bot_message_handler(evt, bot_token=bot_token, db_id=f"bot_{bot_id}")
        bot_client.add_event_handler(handler, events.NewMessage(incoming=True))

        db_id = f"bot_{bot_id}"
        scheduler.init_db(db_id)
        scheduler_task = asyncio.create_task(scheduler.scheduler_loop(db_id, bot_client, log_message))

        running_bots[bot_token] = {"client": bot_client, "handler": handler, "scheduler_task": scheduler_task}

        log_message(f"✅ [Bot Mode] Бот {me.first_name} (id={me.id}) подключен.", level="focus")
        update_bots_listbox()

    except Exception as e:
        log_message(f"❗️ Ошибка запуска бота '{bot_desc}': {e}", level="error")
        if "The authorization key has expired" in str(e) and os.path.exists(session_path):
             os.remove(session_path)
             log_message(f"   Файл сессии {session_name} удален. Попробуйте перезапустить бота.", level="warning")
        if bot_client.is_connected():
            await bot_client.disconnect()
        if bot_token in running_bots:
            del running_bots[bot_token]
        update_bots_listbox()

async def stop_listeners(tokens_to_stop=None):
    global running_bots, scheduler_task

    if tokens_to_stop is None:
        tokens_to_stop = list(running_bots.keys())
        if client and client.is_connected():
            await client.disconnect()
            if scheduler_task and not scheduler_task.done():
                scheduler_task.cancel()
                scheduler_task = None
            log_message("Клиент пользователя остановлен.", level="info")

    for token in tokens_to_stop:
        if token in running_bots:
            try:
                bot_info = running_bots.pop(token)
                bot_client = bot_info['client']
                if bot_client.is_connected():
                    await bot_client.disconnect()

                bot_scheduler_task = bot_info.get('scheduler_task')
                if bot_scheduler_task and not bot_scheduler_task.done():
                    bot_scheduler_task.cancel()

                bot_desc = next((b.get('desc', 'N/A') for b in bots_list if b.get('token') == token), token[:5])
                log_message(f"Бот '{bot_desc}' остановлен.", level="info")

            except Exception as e:
                log_message(f"Ошибка при остановке бота с токеном {token[:5]}...: {e}", level="error")

    update_bots_listbox()
    if not running_bots and not (client and client.is_connected()):
        log_message("⛔ Все мосты остановлены.", level="error")


# ===================== GUI =====================
def update_chat_list(dialogs, clear_selection=False):
    global chat_entities, filtered_chats
    chat_entities = dialogs
    filtered_chats = dialogs
    if not chat_listbox: return
    chat_listbox.delete(0, 'end')
    for i, (name, _) in enumerate(filtered_chats, 1):
        chat_listbox.insert('end', f"{i}. {name}")
    if clear_selection:
        chat_listbox.selection_clear(0, 'end')

def refresh_dialogs_from_async(clear_selection=False, on_done=None):
    def _done(fut):
        try:
            ds = fut.result()
            if root:
                root.after(0, update_chat_list, ds, clear_selection)
            log_message("Список чатов обновлён.", level="info")
            if on_done:
                on_done()
        except Exception as e:
            log_message(f"[Refresh Error] {e}", level="error")
    fut = run_async(get_dialogs())
    fut.add_done_callback(_done)

def on_search(*_):
    q = chat_search.get().lower()
    if not chat_listbox: return
    chat_listbox.delete(0, 'end')
    global filtered_chats
    filtered_chats = [(n, e) for n, e in chat_entities if q in n.lower()]
    for i, (name, _) in enumerate(filtered_chats, 1):
        chat_listbox.insert('end', f"{i}. {name}")

def _selected_chat_title():
    if not active_chats_listbox: return None
    sel = active_chats_listbox.curselection()
    if not sel: return None
    return active_chats_listbox.get(sel[0])

def on_start():
    global bots_running
    mode = operation_mode_var.get()

    if mode == "user":
        if bots_running:
            messagebox.showinfo("Info", "Мост уже запущен."); return
        if not active_chat_entities:
            messagebox.showwarning("Ошибка", "Добавьте хотя бы один чат в список активных.")
            return
        set_buttons(True)
        run_async(start_user_listeners())
        bots_running = True
    elif mode == "bot":
        selections = bot_listbox.curselection()
        if not selections:
            messagebox.showwarning("Ошибка", "Выберите хотя бы одного бота для запуска.")
            return

        for i in selections:
            bot_token = bots_list[i].get('token')
            if not bot_token:
                log_message(f"У бота {bots_list[i].get('desc')} нет токена, пропускаем.", "warning")
                continue
            if bot_token in running_bots:
                log_message(f"Бот {bots_list[i].get('desc')} уже запущен, пропускаем.", "warning")
                continue

            run_async(start_bot_listeners(bot_token))

        if running_bots:
            bots_running = True
            set_buttons(True)

def on_stop_button_click():
    global bots_running
    mode = operation_mode_var.get()

    if mode == 'user':
        run_async(stop_listeners())
        bots_running = False
    else: # mode == 'bot'
        selections = bot_listbox.curselection()
        if not selections:
            messagebox.showwarning("Внимание", "Выберите ботов для остановки.")
            return

        tokens_to_stop = [bots_list[i]['token'] for i in selections if bots_list[i]['token'] in running_bots]

        if not tokens_to_stop:
            messagebox.showinfo("Информация", "Ни один из выбранных ботов не запущен.")
            return

        run_async(stop_listeners(tokens_to_stop))

    if not running_bots:
        bots_running = False
        set_buttons(False)

def on_restart():
    run_async(stop_listeners())
    global bots_running
    bots_running = False
    set_buttons(False)
    if operation_mode_var.get() == "user":
        refresh_dialogs_from_async(clear_selection=True)
    log_message("🔄 Перезагрузка завершена. Готов к запуску.", "info")

def on_add_chat():
    if not chat_listbox or not active_chats_listbox: return
    selected_indices = chat_listbox.curselection()
    if not selected_indices:
        return

    current_active_chats = active_chats_listbox.get(0, "end")
    selected_friend_index = friend_combo.current()

    for i in selected_indices:
        chat_name, chat_entity = filtered_chats[i]

        if chat_name not in current_active_chats:
            active_chat_entities[chat_name] = {
                "entity": chat_entity,
                "friend_index": selected_friend_index
            }
            active_chats_listbox.insert("end", chat_name)

def on_remove_chat():
    if not active_chats_listbox: return
    selected_indices = active_chats_listbox.curselection()
    if not selected_indices:
        return

    for i in sorted(selected_indices, reverse=True):
        chat_name = active_chats_listbox.get(i)
        active_chats_listbox.delete(i)
        if chat_name in active_chat_entities:
            del active_chat_entities[chat_name]


def on_active_chat_select(_=None):
    if not active_chats_listbox: return
    sel = active_chats_listbox.curselection()
    if not sel: return
    chat_name = active_chats_listbox.get(sel[0])

    friend_index = active_chat_entities.get(chat_name, {}).get("friend_index", 0)
    if friend_index >= len(FRIENDS):
        friend_index = 0

    friend_combo.current(friend_index)
    friend_name = FRIENDS[friend_index][0]
    _, custom_prompt, _ = load_history(chat_name, friend_name)

    custom_prompt_text.configure(state='normal')
    custom_prompt_text.delete('1.0', 'end')
    custom_prompt_text.insert('1.0', custom_prompt)
    custom_prompt_text.configure(state='normal')

def on_save_prompt():
    # Эта функция теперь ТОЛЬКО для User Mode. Промпты ботов сохраняются в on_save_bot
    if operation_mode_var.get() != 'user':
        messagebox.showinfo("Информация", "Эта функция предназначена для сохранения доп. промпта для чатов в User Mode.")
        return

    if not active_chats_listbox: return
    sel = active_chats_listbox.curselection()
    if not sel:
        messagebox.showwarning("Ошибка", "Сначала выберите чат в списке активных."); return

    chat_title = active_chats_listbox.get(sel[0])
    prompt_content = custom_prompt_text.get("1.0", "end-1c").strip()

    chat_data = active_chat_entities.get(chat_title, {})
    friend_index = chat_data.get("friend_index", len(FRIENDS))
    friend_name = NONAME[0] if friend_index >= len(FRIENDS) else FRIENDS[friend_index][0]

    history, _, path = load_history(chat_title, friend_name, mode='user')
    save_history(path, history, prompt_content)
    log_message(f"✅ Доп. промпт для чата '{chat_title}' сохранен.", level="info")


def on_clear_history():
    if not active_chats_listbox: return
    sel = active_chats_listbox.curselection()
    if not sel:
        messagebox.showwarning("Ошибка", "Выбери активный чат."); return

    title = active_chats_listbox.get(sel[0])
    if not messagebox.askyesno("Подтверждение", f"Вы уверены, что хотите полностью удалить историю для чата '{title}'? Это действие необратимо."):
        return

    history_file_path = _history_path(title)

    try:
        if os.path.exists(history_file_path):
            os.remove(history_file_path)
        on_active_chat_select()
        clear_log()
        log_message(f"[Info] Файл истории для '{title}' был удален.", level="info")
    except Exception as e:
        log_message(f"Не удалось удалить файл истории: {e}", level="error")
        messagebox.showerror("Ошибка", f"Не удалось удалить файл истории: {e}")

async def on_send_from_gui():
    # Эта функция только для User Mode и приватного общения с ИИ
    if operation_mode_var.get() != 'user':
        messagebox.showinfo("Информация", "Эта функция доступна только в режиме 'User Mode'."); return

    if not active_chats_listbox: return
    sel = active_chats_listbox.curselection()
    if not sel:
        messagebox.showwarning("Ошибка", "Сначала выберите чат для отправки в списке активных."); return

    chat_title = active_chats_listbox.get(sel[0])
    chat_data = active_chat_entities.get(chat_title, {})
    chat_entity = chat_data.get("entity")
    if not chat_entity:
        messagebox.showerror("Ошибка", "Не удалось найти объект чата. Попробуйте перезагрузить."); return

    text = gui_message_text.get("1.0", "end-1c").strip()
    if not text:
        messagebox.showwarning("Ошибка", "Введите текст сообщения."); return

    log_message(f"~> Отправка в [{chat_title}]: {text}", level="warning")

    friend_index = chat_data.get("friend_index", len(FRIENDS))
    friend_name = NONAME[0] if friend_index >= len(FRIENDS) else FRIENDS[friend_index][0]
    history, custom_prompt, hist_path = load_history(chat_title, friend_name)

    history.append({"role": "user", "content": text})
    save_history(hist_path, history, custom_prompt)

    try:
        reply = await gemini_generate(history, friend_name, float(temp_var.get()), custom_prompt)
    except httpx.HTTPStatusError as http_err:
        log_message(f"[Send GUI Msg Error] HTTP Status {http_err.response.status_code}. Проверьте ваш API-ключ или имя модели.", level="error")
        return
    except Exception as e:
        log_message(f"[Send GUI Msg Error] Произошла непредвиденная ошибка: {e}", level="error")
        return

    if reply:
        log_message(f"<~ Ответ для [{chat_title}]: {reply}", level="warning")
        history.append({"role": "assistant", "content": reply})
        save_history(hist_path, history, custom_prompt)
        gui_message_text.delete('1.0', 'end')
        log_message(f"   (переписка сохранена в историю)", level="info")
    else:
        log_message(f"[Send GUI Msg] Нет ответа от AI.", level="error")

async def on_send_to_focused_chat():
    if not bots_running or not client:
        messagebox.showerror("Ошибка", "Мост не запущен."); return

    # This function is only for user mode
    if operation_mode_var.get() != 'user':
        messagebox.showinfo("Информация", "Эта функция доступна только в режиме 'User Mode'."); return

    if not active_chats_listbox: return
    sel = active_chats_listbox.curselection()
    if not sel:
        messagebox.showwarning("Ошибка", "Сначала выберите чат для отправки в списке активных."); return

    chat_title = active_chats_listbox.get(sel[0])
    chat_data = active_chat_entities.get(chat_title, {})
    chat_entity = chat_data.get("entity")
    if not chat_entity:
        messagebox.showerror("Ошибка", "Не удалось найти объект чата. Попробуйте перезагрузить."); return

    text = gui_message_text.get("1.0", "end-1c").strip()
    if not text:
        messagebox.showwarning("Ошибка", "Введите текст сообщения."); return

    log_message(f"~> Отправка в TELEGRAM [{chat_title}]: {text}", level="warning")

    friend_index = chat_data.get("friend_index", len(FRIENDS))
    friend_name = NONAME[0] if friend_index >= len(FRIENDS) else FRIENDS[friend_index][0]
    history, custom_prompt, hist_path = load_history(chat_title, friend_name)

    history.append({"role": "user", "content": text})

    try:
        reply = await gemini_generate(history, friend_name, float(temp_var.get()), custom_prompt)
    except httpx.HTTPStatusError as http_err:
        log_message(f"[Send TG Msg Error] HTTP Status {http_err.response.status_code}. Проверьте ваш API-ключ или имя модели.", level="error")
        return
    except Exception as e:
        log_message(f"[Send TG Msg Error] Произошла непредвиденная ошибка: {e}", level="error")
        return

    if reply:
        log_message(f"<~ Ответ для [{chat_title}]: {reply}", level="warning")
        history.append({"role": "assistant", "content": reply})
        save_history(hist_path, history, custom_prompt)
        gui_message_text.delete('1.0', 'end')
        await client.send_message(chat_entity, reply)
        log_message(f"   (сообщение отправлено в Telegram)", level="info")
    else:
        log_message(f"[Send TG Msg] Нет ответа от AI.", level="error")

def set_buttons(run):
    start_btn.configure(state='disabled' if run else 'normal')
    stop_btn.configure(state='normal' if run else 'disabled')
    restart_btn.configure(state='normal')
    clear_btn.configure(state='normal')
    status_label.configure(text="Состояние: Запущен" if run else "Состояние: Остановлен",
                           foreground="lightgreen" if run else "red")

def save_gui_state():
    try:
        # Сохраняем только если виджеты уже созданы
        if not all([active_chats_listbox, temp_var, friend_combo, see_my_msgs_var, notebook, operation_mode_var]):
            return

        active_chats_data = []
        for chat_name in active_chats_listbox.get(0, "end"):
            friend_index = active_chat_entities.get(chat_name, {}).get("friend_index", 0)
            active_chats_data.append({"name": chat_name, "friend_index": friend_index})

        # Сохраняем индексы выбранных ботов
        selected_bot_indices = []
        if bot_listbox and bot_listbox.curselection():
            selected_bot_indices = bot_listbox.curselection()

        state = {
            "active_chats": active_chats_data,
            "temperature": temp_var.get(),
            "friend_index": friend_combo.current(),
            "see_my_msgs": see_my_msgs_var.get(),
            "selected_tab": notebook.index(notebook.select()),
            "selected_bot_indices": selected_bot_indices
        }
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"Failed to save GUI state: {e}")

def load_gui_state():
    if not os.path.exists(STATE_FILE):
        return None
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"Failed to load GUI state: {e}")
        return None

def on_friend_change(_=None):
    sel = active_chats_listbox.curselection()
    if not sel: return
    chat_name = active_chats_listbox.get(sel[0])
    new_friend_index = friend_combo.current()
    if chat_name in active_chat_entities:
        active_chat_entities[chat_name]["friend_index"] = new_friend_index

def restore_active_chats(active_chats_data):
    for chat_data in active_chats_data:
        name = chat_data.get("name")
        friend_index = chat_data.get("friend_index", 0)
        if not name: continue
        for chat_name, chat_entity in chat_entities:
            if chat_name == name:
                if name not in active_chats_listbox.get(0, "end"):
                    active_chats_listbox.insert("end", name)
                    active_chat_entities[name] = {"entity": chat_entity, "friend_index": friend_index}
                break

def update_bots_listbox():
    if not bot_listbox: return

    selected_indices = bot_listbox.curselection()
    bot_listbox.delete(0, 'end')

    for i, bot_data in enumerate(bots_list):
        token = bot_data.get('token')
        desc = bot_data.get('desc', f'Bot {i+1}')
        status = " (Running)" if token in running_bots else ""

        token_preview = token[:12] if token else "NO_TOKEN"

        display_text = f"{desc} ({token_preview}...){status}"
        bot_listbox.insert('end', display_text)
        if status:
            bot_listbox.itemconfig(i, {'fg': '#40C040'}) # A brighter green for running bots

    # Restore selection
    for idx in selected_indices:
        if idx < bot_listbox.size():
            bot_listbox.selection_set(idx)

def on_add_bot():
    token = bot_token_entry.get().strip()
    if not re.match(r'^\d+:[a-zA-Z0-9_-]+$', token):
        messagebox.showerror("Ошибка", "Неверный формат токена. Введите токен в формате 12345:ABC-xyz.")
        return

    # Проверяем, нет ли уже такого токена
    if any(b.get('token') == token for b in bots_list):
        messagebox.showwarning("Внимание", "Этот бот уже добавлен.")
        return

    # Простое имя по умолчанию
    new_bot_desc = f"Bot {len(bots_list) + 1}"
    bots_list.append({'token': token, 'desc': new_bot_desc, 'system_prompt': ''})
    save_bots(bots_list)
    update_bots_listbox()
    update_cleanup_tab_bots()
    bot_token_entry.delete(0, 'end')
    log_message(f"🤖 Добавлен новый бот: {new_bot_desc}", level="info")

def on_delete_bot():
    selections = bot_listbox.curselection()
    if not selections:
        messagebox.showwarning("Внимание", "Выберите бота для удаления.")
        return

    indices_to_delete = sorted(selections, reverse=True)
    tokens_to_stop = []
    db_ids_to_delete = []

    for i in indices_to_delete:
        bot_data = bots_list[i]
        token = bot_data.get('token')
        if token:
            db_id = f"bot_{token.split(':', 1)[0]}"
            db_ids_to_delete.append(db_id)
            if token in running_bots:
                tokens_to_stop.append(token)

    def perform_delete():
        for i in indices_to_delete:
            removed_bot = bots_list.pop(i)
            desc = removed_bot.get('desc', 'N/A')
            log_message(f"🤖 Удален бот: {desc}", level="info")

        for db_id in db_ids_to_delete:
            db_path = scheduler.get_db_path(db_id)
            if os.path.exists(db_path):
                os.remove(db_path)
                log_message(f"   Файл БД планировщика {os.path.basename(db_path)} удален.", "info")

        save_bots(bots_list)
        update_bots_listbox()
        update_cleanup_tab_bots()
        on_bot_select()

    if tokens_to_stop:
        log_message(f"Останавливаю {len(tokens_to_stop)} работающих ботов перед удалением...", "warning")
        future = run_async(stop_listeners(tokens_to_stop))
        future.add_done_callback(lambda f: perform_delete())
    else:
        perform_delete()

def on_paste_token():
    try:
        clipboard_content = root.clipboard_get()
        if bot_token_entry:
            bot_token_entry.delete(0, 'end')
            bot_token_entry.insert(0, clipboard_content)
            log_message("Токен вставлен из буфера обмена.", level="debug")
    except tk.TclError:
        log_message("Буфер обмена пуст или содержит не-текстовые данные.", level="warning")
        messagebox.showwarning("Буфер обмена", "Не удалось получить текстовое содержимое из буфера обмена.")

def on_save_bot():
    selections = bot_listbox.curselection()
    if not selections:
        messagebox.showwarning("Внимание", "Сначала выберите бота из списка.")
        return

    bot_index = selections[0]

    # Получаем новые данные из полей ввода
    new_desc = bot_desc_entry.get().strip()
    new_prompt = bot_prompt_text.get("1.0", "end-1c").strip()

    if not new_desc:
        messagebox.showerror("Ошибка", "Имя/описание бота не может быть пустым.")
        return

    # Обновляем данные в списке и сохраняем
    bots_list[bot_index]['desc'] = new_desc
    bots_list[bot_index]['system_prompt'] = new_prompt
    save_bots(bots_list)

    # Обновляем Listbox, чтобы отразить новое имя
    update_bots_listbox()
    # Восстанавливаем выделение
    bot_listbox.selection_set(bot_index)

    log_message(f"✅ Данные для бота '{new_desc}' сохранены.", level="info")


def update_cleanup_tab_bots():
    if not cleanup_bot_combo: return

    bot_names = [bot.get('desc', f"Bot {i+1}") for i, bot in enumerate(bots_list)]
    cleanup_bot_combo['values'] = bot_names
    if bot_names:
        cleanup_bot_combo.current(0)

def on_clear_bot_history():
    selected_bot_index = cleanup_bot_combo.current()
    if selected_bot_index == -1:
        messagebox.showwarning("Внимание", "Пожалуйста, выберите бота из списка.")
        return

    bot_data = bots_list[selected_bot_index]
    bot_token = bot_data.get('token')
    bot_desc = bot_data.get('desc')
    bot_id = bot_token.split(':', 1)[0]

    if not messagebox.askyesno("Подтверждение", f"Вы уверены, что хотите УДАЛИТЬ ВСЮ историю переписок для бота '{bot_desc}'? Это действие необратимо."):
        return

    bot_history_dir = os.path.join(CHATS_DIR, f"bot_{bot_id}")

    if not os.path.isdir(bot_history_dir):
        log_message(f"Каталог истории для бота '{bot_desc}' не найден: {bot_history_dir}", level="warning")
        messagebox.showinfo("Информация", f"История для бота '{bot_desc}' уже пуста.")
        return

    try:
        for filename in os.listdir(bot_history_dir):
            file_path = os.path.join(bot_history_dir, filename)
            if os.path.isfile(file_path):
                os.remove(file_path)
        log_message(f"✅ Вся история для бота '{bot_desc}' была удалена.", level="info")
        messagebox.showinfo("Успех", f"Вся история переписок для бота '{bot_desc}' была успешно удалена.")
    except Exception as e:
        log_message(f"Ошибка при удалении истории для бота '{bot_desc}': {e}", level="error")
        messagebox.showerror("Ошибка", f"Не удалось удалить историю: {e}")


def on_bot_select(_=None):
    selections = bot_listbox.curselection()
    if not selections:
        # Очищаем поля, если ничего не выбрано
        bot_desc_entry.delete(0, 'end')
        bot_prompt_text.delete('1.0', 'end')
        return

    bot_index = selections[0]
    bot_data = bots_list[bot_index]

    # Загружаем данные в поля
    bot_desc_entry.delete(0, 'end')
    bot_desc_entry.insert(0, bot_data.get('desc', ''))

    bot_prompt_text.delete('1.0', 'end')
    bot_prompt_text.insert('1.0', bot_data.get('system_prompt', ''))

def on_close():
    save_gui_state()
    run_async(stop_listeners())
    if aio_loop: aio_loop.call_soon_threadsafe(aio_loop.stop)
    root.destroy()

def on_temp_change(val):
    try: v = float(val)
    except: v = 0.7
    if temp_value_label:
        temp_value_label.config(text=f"{v:.1f}")

def style_combobox_dropdown(cb: ttk.Combobox, bg="#101010", fg="#ffffff",
                            sel_bg="#0f0f0f", sel_fg="#17a556"):
    def _apply(_=None):
        try:
            popdown = cb.tk.call("ttk::combobox::PopdownWindow", str(cb))
            lb = cb.nametowidget(f"{popdown}.f.l")
            lb.configure(background=bg, foreground=fg,
                         selectbackground=sel_bg, selectforeground=sel_fg)
        except Exception: pass
    cb.bind("<Button-1>", lambda e: cb.after(10, _apply), add="+")
    cb.bind("<<ComboboxSelected>>", lambda e: cb.after(10, _apply), add="+")
    cb.after(300, _apply)

def main():
    global root, chat_listbox, chat_search, friend_combo, log_text
    global start_btn, stop_btn, restart_btn, clear_btn, status_label
    global see_my_msgs_var, temp_var, temp_value_label
    global SYSTEM_PROMPT_TXT, FRIENDS, NONAME
    global active_chats_listbox, custom_prompt_text, gui_message_text, verbose_logging_var
    global notebook, operation_mode_var, bots_list, bot_listbox, bot_token_entry, bot_desc_entry, bot_prompt_text
    global cleanup_bot_combo

    ensure_api_config(); ensure_tg_config(); ensure_deepgram_config(); ensure_bots_config()
    SYSTEM_PROMPT_TXT, FRIENDS, NONAME = load_prompt_config()
    bots_list = load_bots()

    BG, SEARCH_BG, CHAT_COLOR, BLUE, FG, FRIEND_GREEN = "#171717", "#3e3e3e", "#c714c9", "#1e66ff", "#ffffff", "#17a556"

    root = tk.Tk()
    root.title("TG Userbot & Bot — Gemini Bridge")
    root.configure(bg=BG)
    root.option_add("*selectBackground", "#0f0f0f"); root.option_add("*selectForeground", FRIEND_GREEN)

    style = ttk.Style(); style.theme_use("clam")
    style.configure(".", background=BG, foreground=FG, fieldbackground=BG)
    style.configure("Dark.TLabel", background=BG, foreground=FG)
    style.configure("Dark.TButton", background=BG, foreground=FG, relief="flat", padding=6)
    style.map("Dark.TButton", background=[("disabled","#121212"),("active","#1f1f1f"),("pressed","#232323")], foreground=[("disabled","#8a8a8a")])
    style.configure("Dark.TCheckbutton", background=BG, foreground=FG)
    style.configure("Friend.TCombobox", fieldbackground="#101010", background="#101010", foreground=FRIEND_GREEN, bordercolor="#0a0a0a", lightcolor="#0d0d0d", darkcolor="#0a0a0a", arrowcolor=FRIEND_GREEN)
    style.map("Friend.TCombobox", fieldbackground=[("readonly","#101010"),("focus","#0e0e0e"),("active","#0e0e0e")], background=[("readonly","#101010"),("focus","#0e0e0e"),("active","#0e0e0e")], foreground=[("!disabled",FRIEND_GREEN)], arrowcolor=[("!disabled",FRIEND_GREEN)])
    style.configure("TNotebook", background=BG, borderwidth=0); style.configure("TNotebook.Tab", background=BG, foreground=FG, padding=[5, 2], relief="flat"); style.map("TNotebook.Tab", background=[("selected", SEARCH_BG)], expand=[("selected", [1, 1, 1, 0])])

    main_frame = ttk.Frame(root, padding=8, style="Dark.TLabel")
    main_frame.pack(fill="both", expand=True)
    main_frame.columnconfigure(0, weight=3); main_frame.columnconfigure(1, weight=3); main_frame.rowconfigure(0, weight=1)

    notebook_frame = ttk.Frame(main_frame, style="Dark.TLabel")
    notebook_frame.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
    notebook = ttk.Notebook(notebook_frame, style="TNotebook")
    notebook.pack(fill="both", expand=True)

    # ==================== USER TAB ====================
    user_tab = ttk.Frame(notebook, style="Dark.TLabel", padding=2); user_tab.columnconfigure(0, weight=2); user_tab.columnconfigure(1, weight=0); user_tab.columnconfigure(2, weight=2); user_tab.rowconfigure(0, weight=1)
    notebook.add(user_tab, text="👤 User Mode")
    left = ttk.Frame(user_tab, style="Dark.TLabel"); left.grid(row=0, column=0, sticky="nsew", padx=(0, 4)); left.rowconfigure(2, weight=1); left.columnconfigure(0, weight=1)
    ttk.Label(left, text="Все чаты:", style="Dark.TLabel").grid(row=0, column=0, sticky="w")
    chat_search = tk.Entry(left, bg=SEARCH_BG, fg=FG, insertbackground=FG, relief="flat"); chat_search.grid(row=1, column=0, sticky="we", pady=4); chat_search.bind("<KeyRelease>", on_search)
    chat_listbox = tk.Listbox(left, bg=BG, fg=CHAT_COLOR, selectbackground=BLUE, selectforeground=FG, activestyle="none", exportselection=False, width=32, selectmode="extended"); chat_listbox.grid(row=2, column=0, sticky="nsew")
    mid_buttons = ttk.Frame(user_tab, style="Dark.TLabel"); mid_buttons.grid(row=0, column=1, sticky="ns", padx=4); mid_buttons.rowconfigure(0, weight=1); mid_buttons.rowconfigure(1, weight=1)
    ttk.Button(mid_buttons, text=">>", command=on_add_chat, style="Dark.TButton", width=4).pack(pady=(150, 5))
    ttk.Button(mid_buttons, text="<<", command=on_remove_chat, style="Dark.TButton", width=4).pack(pady=5)
    active_chats_frame = ttk.Frame(user_tab, style="Dark.TLabel"); active_chats_frame.grid(row=0, column=2, sticky="nsew", padx=(0, 8)); active_chats_frame.rowconfigure(1, weight=3); active_chats_frame.rowconfigure(3, weight=2); active_chats_frame.columnconfigure(0, weight=1)
    ttk.Label(active_chats_frame, text="Активные чаты:", style="Dark.TLabel").grid(row=0, column=0, sticky="w")
    active_chats_listbox = tk.Listbox(active_chats_frame, bg=BG, fg=FRIEND_GREEN, selectbackground=BLUE, selectforeground=FG, activestyle="none", exportselection=False, width=32, selectmode="extended"); active_chats_listbox.grid(row=1, column=0, sticky="nsew", pady=(4,0)); active_chats_listbox.bind("<<ListboxSelect>>", on_active_chat_select)
    ttk.Label(active_chats_frame, text="Доп. промпт для чата:", style="Dark.TLabel").grid(row=2, column=0, sticky="w", pady=(8,0))
    custom_prompt_text = scrolledtext.ScrolledText(active_chats_frame, height=5, bg=SEARCH_BG, fg=FG, relief="flat", insertbackground=FG); custom_prompt_text.grid(row=3, column=0, sticky="nsew", pady=4)
    ttk.Button(active_chats_frame, text="Сохранить промпт", command=on_save_prompt, style="Dark.TButton").grid(row=4, column=0, sticky="ew")

    # ==================== BOT TAB ====================
    bot_tab = ttk.Frame(notebook, style="Dark.TLabel", padding=2)
    bot_tab.columnconfigure(0, weight=1)
    bot_tab.columnconfigure(1, weight=1)
    bot_tab.rowconfigure(1, weight=1)
    notebook.add(bot_tab, text="🤖 Bot Mode")

    # Left side of Bot Tab: Add new bot and list
    bot_list_frame = ttk.Frame(bot_tab, style="Dark.TLabel")
    bot_list_frame.grid(row=0, column=0, sticky="nsew", padx=(0, 5))
    bot_list_frame.columnconfigure(0, weight=1)
    bot_list_frame.rowconfigure(2, weight=1)

    add_bot_frame = ttk.Frame(bot_list_frame, style="Dark.TLabel")
    add_bot_frame.grid(row=0, column=0, sticky="ew", pady=(0, 5))
    add_bot_frame.columnconfigure(0, weight=1)
    ttk.Label(add_bot_frame, text="Токен нового бота:", style="Dark.TLabel").grid(row=0, column=0, columnspan=2, sticky="w")
    bot_token_entry = tk.Entry(add_bot_frame, bg=SEARCH_BG, fg=FG, insertbackground=FG, relief="flat")
    bot_token_entry.grid(row=1, column=0, sticky="ew", pady=(0, 5))
    bot_buttons_frame = ttk.Frame(add_bot_frame, style="Dark.TLabel")
    bot_buttons_frame.grid(row=1, column=1, sticky="w", padx=(5,0))
    paste_bot_btn = ttk.Button(bot_buttons_frame, text="Вставить", command=on_paste_token, style="Dark.TButton")
    paste_bot_btn.pack(side="left", padx=(0, 5))
    add_bot_btn = ttk.Button(bot_buttons_frame, text="Добавить", command=on_add_bot, style="Dark.TButton")
    add_bot_btn.pack(side="left")

    ttk.Label(bot_list_frame, text="Сохраненные боты:", style="Dark.TLabel").grid(row=1, column=0, sticky="w", pady=(5,0))
    bot_listbox = tk.Listbox(bot_list_frame, bg=BG, fg=FRIEND_GREEN, selectbackground=BLUE, selectforeground=FG, activestyle="none", exportselection=False, width=40, selectmode="extended")
    bot_listbox.grid(row=2, column=0, sticky="nsew")
    bot_listbox.bind("<<ListboxSelect>>", on_bot_select)
    delete_bot_btn = ttk.Button(bot_list_frame, text="Удалить выбранного бота", command=on_delete_bot, style="Dark.TButton")
    delete_bot_btn.grid(row=3, column=0, sticky="ew", pady=(5,0))

    # Right side of Bot Tab: Edit selected bot
    bot_edit_frame = ttk.Frame(bot_tab, style="Dark.TLabel")
    bot_edit_frame.grid(row=0, column=1, rowspan=2, sticky="nsew")
    bot_edit_frame.columnconfigure(0, weight=1)
    bot_edit_frame.rowconfigure(3, weight=1)

    ttk.Label(bot_edit_frame, text="Имя/описание бота:", style="Dark.TLabel").grid(row=0, column=0, sticky="w")
    bot_desc_entry = tk.Entry(bot_edit_frame, bg=SEARCH_BG, fg=FG, insertbackground=FG, relief="flat")
    bot_desc_entry.grid(row=1, column=0, sticky="ew", pady=(0, 10))

    ttk.Label(bot_edit_frame, text="Системный промпт бота:", style="Dark.TLabel").grid(row=2, column=0, sticky="w")
    bot_prompt_text = scrolledtext.ScrolledText(bot_edit_frame, height=10, bg=SEARCH_BG, fg=FG, relief="flat", insertbackground=FG)
    bot_prompt_text.grid(row=3, column=0, sticky="nsew", pady=4)

    save_bot_btn = ttk.Button(bot_edit_frame, text="Сохранить изменения для бота", command=on_save_bot, style="Dark.TButton")
    save_bot_btn.grid(row=4, column=0, sticky="ew", pady=(5,0))

    # ==================== CLEANUP TAB ====================
    cleanup_tab = ttk.Frame(notebook, style="Dark.TLabel", padding=10)
    cleanup_tab.columnconfigure(0, weight=1)
    notebook.add(cleanup_tab, text="🧹 Очистка")

    cleanup_frame = ttk.Frame(cleanup_tab, style="Dark.TLabel", padding=10)
    cleanup_frame.pack(anchor="n", fill="x")
    cleanup_frame.columnconfigure(0, weight=1)

    ttk.Label(cleanup_frame, text="Очистка истории для бота", style="Dark.TLabel").grid(row=0, column=0, sticky="w")

    cleanup_bot_combo = ttk.Combobox(cleanup_frame, state="readonly", style="Friend.TCombobox")
    cleanup_bot_combo.grid(row=1, column=0, sticky="ew", pady=5)
    style_combobox_dropdown(cleanup_bot_combo, bg="#101010", fg="#ffffff", sel_bg="#0f0f0f", sel_fg="#17a556")

    clear_history_btn = ttk.Button(cleanup_frame, text="УДАЛИТЬ ИСТОРИЮ ВЫБРАННОГО БОТА", command=on_clear_bot_history, style="Dark.TButton")
    clear_history_btn.grid(row=2, column=0, sticky="ew", pady=5)


    # ==================== RIGHT PANEL (Controls & Log) ====================
    right = ttk.Frame(main_frame, style="Dark.TLabel"); right.grid(row=0, column=1, sticky="nsew"); right.columnconfigure(0, weight=1); right.columnconfigure(1, weight=1); right.columnconfigure(2, weight=1); right.rowconfigure(12, weight=1)
    ttk.Label(right, text="С кем общаемся (для User Mode):", style="Dark.TLabel").grid(row=0, column=0, columnspan=3, sticky="w")
    friend_combo = ttk.Combobox(right, width=50, state="readonly", style="Friend.TCombobox"); friend_combo['values'] = [f"{n} — {d}" for n, d in FRIENDS] + [f"{NONAME[0]} — {NONAME[1]}"]; friend_combo.current(0); friend_combo.grid(row=1, column=0, columnspan=3, sticky="we", pady=(0,6)); style_combobox_dropdown(friend_combo, bg="#101010", fg="#ffffff", sel_bg="#0f0f0f", sel_fg="#17a556"); friend_combo.bind("<<ComboboxSelected>>", on_friend_change)
    see_my_msgs_var = tk.BooleanVar(value=False); ttk.Checkbutton(right, text="Видеть мои сообщения (User Mode)", variable=see_my_msgs_var, style="Dark.TCheckbutton").grid(row=2, column=0, columnspan=2, sticky="w", pady=(0,6))
    verbose_logging_var = tk.BooleanVar(value=False); ttk.Checkbutton(right, text="Подробные логи", variable=verbose_logging_var, style="Dark.TCheckbutton").grid(row=2, column=2, sticky="w", pady=(0,6))
    ttk.Label(right, text="Температура модели:", style="Dark.TLabel").grid(row=3, column=0, sticky="w")
    temp_var = tk.DoubleVar(value=0.7); tk.Scale(right, from_=0.0, to=2.0, resolution=0.1, orient="horizontal", variable=temp_var, showvalue=False, bg=BG, fg=FG, highlightthickness=0, troughcolor=BLUE, activebackground=BLUE, relief="flat", bd=0, command=on_temp_change).grid(row=3, column=1, sticky="we", pady=2)
    temp_value_label = ttk.Label(right, text=f"{temp_var.get():.1f}", style="Dark.TLabel"); temp_value_label.grid(row=3, column=2, sticky="w")
    start_btn = ttk.Button(right, text="Запустить", command=on_start, style="Dark.TButton"); stop_btn = ttk.Button(right, text="Остановить", command=on_stop_button_click, style="Dark.TButton"); restart_btn = ttk.Button(right, text="Перезагрузить", command=on_restart, style="Dark.TButton"); clear_btn = ttk.Button(right, text="Очистить историю (User Mode)", command=on_clear_history, style="Dark.TButton")
    start_btn.grid(row=4, column=0, pady=6, sticky="we"); stop_btn.grid(row=4, column=1, pady=6, sticky="we"); restart_btn.grid(row=4, column=2, pady=6, sticky="we"); clear_btn.grid(row=5, column=0, columnspan=3, pady=(0,6), sticky="we")
    ttk.Label(right, text="Отправить в выбранный чат (User Mode):", style="Dark.TLabel").grid(row=6, column=0, columnspan=3, sticky="w", pady=(8,0))
    gui_message_text = scrolledtext.ScrolledText(right, height=3, bg=SEARCH_BG, fg=FG, relief="flat", insertbackground=FG); gui_message_text.grid(row=7, column=0, columnspan=3, sticky="nsew", pady=4)
    ttk.Button(right, text="Спросить ИИ (приватно)", command=lambda: run_async(on_send_from_gui()), style="Dark.TButton").grid(row=8, column=0, columnspan=3, sticky="ew", pady=(0,2))
    ttk.Button(right, text="Отправить ответ в ЧАТ В ФОКУСЕ", command=lambda: run_async(on_send_to_focused_chat()), style="Dark.TButton").grid(row=9, column=0, columnspan=3, sticky="ew")
    status_label = ttk.Label(right, text="Состояние: Остановлен", style="Dark.TLabel", foreground="red"); status_label.grid(row=10, column=0, columnspan=3, sticky="w", pady=(2,6))
    ttk.Label(right, text="Лог:", style="Dark.TLabel").grid(row=11, column=0, columnspan=3, sticky="w")
    log_text = scrolledtext.ScrolledText(right, width=80, height=18, bg=BG, fg=FG, insertbackground=FG, relief="flat"); log_text.tag_config("violet", foreground="#b388ff"); log_text.tag_config("green", foreground="lightgreen"); log_text.tag_config("red", foreground="red"); log_text.tag_config("white", foreground="white"); log_text.tag_config("yellow", foreground="#FFFF88"); log_text.tag_config("grey", foreground="grey"); log_text.grid(row=12, column=0, columnspan=3, sticky="nsew")

    globals().update(locals())
    operation_mode_var = tk.StringVar(value="user")
    def on_tab_change(event):
        selected_tab = notebook.index(notebook.select())
        mode = "user" if selected_tab == 0 else "bot"
        operation_mode_var.set(mode)
        log_message(f"Переключен режим на: {'User Mode' if mode == 'user' else 'Bot Mode'}", level="debug")
    notebook.bind("<<NotebookTabChanged>>", on_tab_change)

    loop_thread = threading.Thread(target=start_background_loop, daemon=True); loop_thread.start(); aio_loop_ready.wait()
    saved_state = load_gui_state()

    def _restore_state_callback():
        update_bots_listbox() # Заполняем список ботов
        update_cleanup_tab_bots() # Заполняем список ботов на вкладке очистки
        if not saved_state: return

        # Восстанавливаем состояние User Mode
        temp_var.set(saved_state.get("temperature", 0.7))
        friend_combo.current(saved_state.get("friend_index", 0))
        see_my_msgs_var.set(saved_state.get("see_my_msgs", False))
        active_chats_to_restore = saved_state.get("active_chats", [])
        if active_chats_to_restore: restore_active_chats(active_chats_to_restore)

        # Восстанавливаем состояние Bot Mode
        selected_bot_indices = saved_state.get("selected_bot_indices", [])
        for idx in selected_bot_indices:
            if bot_listbox.size() > idx:
                bot_listbox.selection_set(idx)
        if selected_bot_indices:
            bot_listbox.see(selected_bot_indices[-1])

        # Восстанавливаем активную вкладку
        selected_tab = saved_state.get("selected_tab", 0)
        notebook.select(selected_tab)
        operation_mode_var.set("user" if selected_tab == 0 else "bot")


    refresh_dialogs_from_async(on_done=_restore_state_callback)
    set_buttons(False)
    root.protocol("WM_DELETE_WINDOW", on_close)
    root.mainloop()

if __name__ == "__main__":
    try:
        main()
    except Exception:
        import traceback
        tb = traceback.format_exc()
        try:
            with open(os.path.join(BASE_DIR, "last_error.log"), "w", encoding="utf-8") as f:
                f.write(tb)
        except Exception: pass
        print("\n========== UNHANDLED ERROR ==========\n")
        print(tb)
        input("\n[Ошибка] Нажмите Enter, чтобы закрыть...")