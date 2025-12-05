import os
from core.paths import LOG_FILE

# Глобальные переменные для GUI логгера
_log_text_widget = None
_root = None
_verbose_logging_var = None

def init_logger(log_widget, root, verbose_var):
    global _log_text_widget, _root, _verbose_logging_var
    _log_text_widget = log_widget
    _root = root
    _verbose_logging_var = verbose_var

def log_message(text: str, level: str = "info"):
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"[{level.upper()}] {text}\n")
    except Exception as e:
        print(f"Failed to write to log file: {e}")

    if not _root or not _log_text_widget:
        print(text)
        return

    tag_map = {
        "info": "green", "error": "red", "focus": "violet", "debug": "grey",
        "warning": "yellow", "user": "white"
    }
    tag = tag_map.get(level, "white")

    def _append_to_gui():
        if level == 'debug' and not (_verbose_logging_var and _verbose_logging_var.get()):
            return
        _log_text_widget.configure(state='normal')
        _log_text_widget.insert('end', text + '\n', tag)
        _log_text_widget.see('end')
        _log_text_widget.configure(state='disabled')

    _root.after(0, _append_to_gui)

def clear_log():
    if not _log_text_widget: return
    _log_text_widget.configure(state='normal')
    _log_text_widget.delete('1.0', 'end')
    _log_text_widget.configure(state='disabled')

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
