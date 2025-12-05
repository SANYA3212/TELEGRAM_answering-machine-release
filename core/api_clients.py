import asyncio
import base64
import httpx
import json
import re
import time
from collections import deque
from deepgram import DeepgramClient, FileSource
from deepgram.options import PrerecordedOptions
from core.config_loader import load_api_config, load_deepgram_config
from app.gui_logger import log_message

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

async def gemini_generate(history, friend_name: str, temperature: float, custom_prompt: str, system_prompt: str):
    endpoint, model, rpm = load_api_config()
    full_system_prompt = f"{system_prompt}\n\n{custom_prompt}\n\nСейчас ты общаешься с: {friend_name}."

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
