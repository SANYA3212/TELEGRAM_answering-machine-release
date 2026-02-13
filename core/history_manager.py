import json
import os
import re
from core.paths import JSON_HISTORY_DIR

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
        bot_chat_dir = os.path.join(JSON_HISTORY_DIR, f"bot_{bot_id}")
        os.makedirs(bot_chat_dir, exist_ok=True)
        return os.path.join(bot_chat_dir, f"{_sanitize_filename(chat_title)}.json")
    else: # user mode
        return os.path.join(JSON_HISTORY_DIR, f"{_sanitize_filename(chat_title)}.json")

def load_history(chat_title: str, mode: str, bot_token: str = None):
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

def clear_chat_history(chat_title: str, mode: str, bot_token: str = None):
    history_file_path = _history_path(chat_title, mode=mode, bot_token=bot_token)
    if os.path.exists(history_file_path):
        os.remove(history_file_path)

def clear_bot_history(bot_token: str):
    bot_id = bot_token.split(':', 1)[0]
    bot_history_dir = os.path.join(JSON_HISTORY_DIR, f"bot_{bot_id}")
    if os.path.isdir(bot_history_dir):
        for filename in os.listdir(bot_history_dir):
            file_path = os.path.join(bot_history_dir, filename)
            if os.path.isfile(file_path):
                os.remove(file_path)
