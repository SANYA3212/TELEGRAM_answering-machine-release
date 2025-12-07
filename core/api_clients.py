import asyncio
import base64
import httpx
import json
import re
import time
from collections import deque
import google.generativeai as genai
from deepgram import DeepgramClient, PrerecordedOptions
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
        dg_client = DeepgramClient(api_key=api_key)

        media_buffer.seek(0)
        audio_bytes = media_buffer.read()

        payload = {"buffer": audio_bytes}

        options = PrerecordedOptions(
            model="nova-2",
            smart_format=True,
            language="ru"
        )

        response = await asyncio.to_thread(
            dg_client.listen.rest.v("1").transcribe_file, payload, options
        )

        transcript = response.results.channels[0].alternatives[0].transcript
        return transcript

    except Exception as e:
        log_message(f"[Deepgram Error] {e}", level="error")
        return None

async def gemini_generate(history, friend_name: str, temperature: float, custom_prompt: str, system_prompt: str):
    try:
        _, model_name, rpm = load_api_config()
        await acquire_rate_slot(rpm)

        full_system_prompt = f"{system_prompt}\n\n{custom_prompt}\n\nСейчас ты общаешься с: {friend_name}."

        model = genai.GenerativeModel(
            model_name,
            system_instruction=full_system_prompt
        )

        contents = _history_to_gemini_contents(history)

        generation_config = genai.types.GenerationConfig(
            temperature=float(temperature),
            top_p=0.95,
            max_output_tokens=1024
        )

        response = await model.generate_content_async(
            contents=contents,
            generation_config=generation_config,
            safety_settings=_gemini_safety_settings()
        )

        log_message(f"[API Response Raw] {response}", level="debug")

        if not response.candidates:
            log_message("[Gemini Safety Blocked] No candidates returned.", level="error")
            return ""

        return response.text.strip()

    except Exception as e:
        log_message(f"[Gemini Generate Error] {e}", level="error")
        return ""

async def gemini_parse_task(text: str):
    try:
        _, model_name, rpm = load_api_config()
        await acquire_rate_slot(rpm)

        model = genai.GenerativeModel(model_name)

        prompt = (
            "Ты — ИИ-парсер для планировщика задач. Твоя задача — извлечь из текста три параметра: "
            "кому адресована задача (addressee), что нужно сделать (text) и через сколько минут (minutes). "
            "Если адресат не указан, используй 'мне'. Если время не указано, верни 0. "
            "Ответ должен быть ТОЛЬКО в формате JSON. Пример: "
            '{"addressee": "Петя", "text": "подойти к компьютеру", "minutes": 15}'
        )

        contents = [
            {"role": "user", "parts": [{"text": prompt}]},
            {"role": "model", "parts": [{"text": "OK"}]},
            {"role": "user", "parts": [{"text": text}]}
        ]

        generation_config = genai.types.GenerationConfig(
            temperature=0.0,
            max_output_tokens=200
        )

        response = await model.generate_content_async(
            contents=contents,
            generation_config=generation_config,
            safety_settings=_gemini_safety_settings("BLOCK_ONLY_HIGH")
        )

        if not response.candidates:
            log_message("[Gemini Safety Blocked] No candidates returned for task parsing.", level="error")
            return None

        raw_text = response.text
        match = re.search(r'\{.*\}', raw_text, re.DOTALL)
        if not match: return None

        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            return None
    except Exception as e:
        log_message(f"[Gemini Parse Error] {e}", level="error")
        return None

def get_available_models():
    """
    Fetches and filters available Gemini models that support text generation.
    """
    try:
        api_key, _, _ = load_api_config(just_get_api_key=True)
        if not api_key:
            log_message("API key for Gemini not found. Cannot fetch models.", level="error")
            return []

        genai.configure(api_key=api_key)

        models = []
        for m in genai.list_models():
            if 'generateContent' in m.supported_generation_methods:
                models.append(m.name.replace("models/", ""))
        return models
    except Exception as e:
        log_message(f"[Model Fetch Error] Failed to get available models: {e}", level="error")
        return []
