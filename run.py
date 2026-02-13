import sys
import os

# Добавляем корень проекта в sys.path, чтобы импорты работали корректно
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

from app.main import main
from core.paths import ERROR_LOG_FILE

if __name__ == "__main__":
    try:
        main()
    except Exception:
        import traceback
        tb = traceback.format_exc()
        try:
            # Записываем ошибку в лог-файл в новой структуре
            with open(ERROR_LOG_FILE, "w", encoding="utf-8") as f:
                f.write(tb)
        except Exception as log_e:
            print(f"Failed to write to error log: {log_e}")

        print("\n========== UNHANDLED ERROR ==========\n")
        print(tb)
        input("\n[Критическая ошибка] Нажмите Enter, чтобы закрыть...")
