import os
import sys

def get_base_dir():
    """Определяет базовую директорию приложения, работает как для .py, так и для .exe (PyInstaller)."""
    try:
        if getattr(sys, "frozen", False):
            return os.path.dirname(sys.executable)
        else:
            # Для запуска из исходников, BASE_DIR должен быть корнем проекта, а не 'core'
            return os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    except Exception:
        return os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

BASE_DIR = get_base_dir()

# Определяем пути к основным папкам
APP_DIR     = os.path.join(BASE_DIR, "app")
CONFIG_DIR  = os.path.join(BASE_DIR, "config")
CORE_DIR    = os.path.join(BASE_DIR, "core")
DATA_DIR    = os.path.join(BASE_DIR, "data")
LOGS_DIR    = os.path.join(BASE_DIR, "logs")

# Пути к подпапкам данных
JSON_HISTORY_DIR = os.path.join(DATA_DIR, "json")
DB_DIR           = os.path.join(DATA_DIR, "db")
TMP_DIR          = os.path.join(DATA_DIR, "tmp")

# Пути к файлам конфигурации
API_FILE      = os.path.join(CONFIG_DIR, "api_text_model.json")
TG_FILE       = os.path.join(CONFIG_DIR, "telegram_api.json")
PROMPT_FILE   = os.path.join(CONFIG_DIR, "SYSTEM_PROMPT.json")
DEEPGRAM_FILE = os.path.join(CONFIG_DIR, "deepgram_api.json")
BOTS_FILE     = os.path.join(CONFIG_DIR, "bots.json")

# Пути к временным файлам и логам
STATE_FILE    = os.path.join(TMP_DIR, "gui_state.json")
LOG_FILE      = os.path.join(LOGS_DIR, "console.log")
ERROR_LOG_FILE = os.path.join(LOGS_DIR, "last_error.log")

# Создаем все необходимые директории при импорте модуля
def create_dirs():
    """Создает все необходимые директории для работы приложения."""
    for path in [APP_DIR, CONFIG_DIR, CORE_DIR, DATA_DIR, LOGS_DIR,
                 JSON_HISTORY_DIR, DB_DIR, TMP_DIR]:
        os.makedirs(path, exist_ok=True)

# Вызываем функцию создания директорий, чтобы они были готовы к использованию
create_dirs()
