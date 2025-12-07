import json
import os
from core.paths import API_FILE, TG_FILE, PROMPT_FILE, DEEPGRAM_FILE, BOTS_FILE, SETTINGS_BOT_FILE

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
        "api_key": "",
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
    name = (model or "").strip()
    if not name:
        return ""
    if name.startswith("models/"):
        name = name.split("/", 1)[1]
    if name.endswith(":generateContent"):
        name = name[: -len(":generateContent")]
    return name.strip()

def load_api_config(just_get_api_key=False):
    ensure_api_config()
    with open(API_FILE, "r", encoding="utf-8") as f:
        cfg = json.load(f)

    api_key  = (cfg.get("api_key") or "").strip()
    if just_get_api_key:
        return api_key, None, None
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
    for suffix in ("/v1beta/models", "/v1beta", "/v1/models", "/v1"):
        if base_url.endswith(suffix):
            base_url = base_url[: -len(suffix)].rstrip("/")

    if not base_url:
        raise RuntimeError("Поле base_url в api_text_model.json не должно быть пустым.")

    endpoint = f"{base_url}/v1beta/models/{model}:generateContent?key={api_key}"
    return endpoint, model, rpm

def save_api_config(new_model_name):
    """
    Saves the new model name to the API config file.
    """
    ensure_api_config()
    with open(API_FILE, "r", encoding="utf-8") as f:
        cfg = json.load(f)

    cfg['model'] = new_model_name

    with open(API_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)

def load_tg_config():
    from core.paths import TMP_DIR
    ensure_tg_config()
    with open(TG_FILE, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    api_id   = int(cfg.get("api_id") or 0)
    api_hash = (cfg.get("api_hash") or "").strip()
    session  = (cfg.get("session_file") or "userbot_session.session").strip()

    if not os.path.isabs(session):
        session = os.path.join(TMP_DIR, session)

    if not api_id or not api_hash:
        raise RuntimeError("Заполни api_id и api_hash в telegram_api.json")
    return api_id, api_hash, session

def ensure_bots_config():
    if os.path.exists(BOTS_FILE):
        return
    with open(BOTS_FILE, "w", encoding="utf-8") as f:
        json.dump([], f, ensure_ascii=False, indent=2)

def load_bots():
    ensure_bots_config()
    with open(BOTS_FILE, "r", encoding="utf-8") as f:
        bots = json.load(f)
    for bot in bots:
        if "system_prompt" not in bot:
            bot["system_prompt"] = ""
    return bots

def save_bots(bots_list):
    with open(BOTS_FILE, "w", encoding="utf-8") as f:
        json.dump(bots_list, f, ensure_ascii=False, indent=2)

def ensure_settings_bot_config():
    if os.path.exists(SETTINGS_BOT_FILE):
        return
    with open(SETTINGS_BOT_FILE, "w", encoding="utf-8") as f:
        json.dump({"bot_token": ""}, f, ensure_ascii=False, indent=2)

def load_settings_bot_config():
    ensure_settings_bot_config()
    with open(SETTINGS_BOT_FILE, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    return (cfg.get("bot_token") or "").strip()

def save_settings_bot_config(token):
    with open(SETTINGS_BOT_FILE, "w", encoding="utf-8") as f:
        json.dump({"bot_token": token}, f, ensure_ascii=False, indent=2)

# ===================== SYSTEM_PROMPT.json =====================
def _default_system_prompt() -> str:
    return ""

def _default_friends():
    return [
        {"name": "admin", "desc": "создатель веселый рассеяный общяйся как удобно ему!"},
        {"name": "1", "desc": "лучший друг — можно шутить и поливать гадостями и расслабляться"},
        {"name": "2", "desc": ""},
        {"name": "3", "desc": ""},
        {"name": "4", "desc": ""}
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
        print("Ключ 'system_prompt' не найден или пуст в SYSTEM_PROMPT.json. Используется промпт по умолчанию.")
        system_prompt = _default_system_prompt()

    friends_items = js.get("friends")
    if not isinstance(friends_items, list) or not friends_items:
        friends_items = _default_friends()
    friends = [(str(i.get("name") or "Noname"), str(i.get("desc") or "")) for i in friends_items]
    noname_obj = js.get("noname") or {"name": "Noname", "desc": "собеседник не в списке — общайся по контексту"}
    noname = (str(noname_obj.get("name") or "Noname"), str(noname_obj.get("desc") or ""))
    return system_prompt, friends, noname
