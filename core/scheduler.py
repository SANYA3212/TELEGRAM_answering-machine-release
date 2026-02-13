import sqlite3
import time
import asyncio
import os
from core.paths import DB_DIR

def get_db_path(db_id):
    """Возвращает путь к файлу БД планировщика."""
    return os.path.join(DB_DIR, f"scheduler_{db_id}.db")

def init_db(db_id):
    """Инициализирует БД для указанного ID (user_mode или ID бота)."""
    db_file = get_db_path(db_id)
    os.makedirs(os.path.dirname(db_file), exist_ok=True)
    with sqlite3.connect(db_file) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY,
                target_chat_id INTEGER NOT NULL,
                addressee TEXT,
                text TEXT,
                execution_time INTEGER
            )
        """)
        conn.commit()

def add_task(db_id, target_chat_id, addressee, text, execution_time):
    """Добавляет новую задачу в БД."""
    db_file = get_db_path(db_id)
    with sqlite3.connect(db_file) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO tasks (target_chat_id, addressee, text, execution_time) VALUES (?, ?, ?, ?)",
            (target_chat_id, addressee, text, execution_time)
        )
        conn.commit()

async def scheduler_loop(db_id, client, log_func):
    """Основной цикл планировщика, который проверяет и выполняет задачи."""
    db_file = get_db_path(db_id)
    while True:
        try:
            if not client or not client.is_connected():
                await asyncio.sleep(15)
                continue

            with sqlite3.connect(db_file) as conn:
                cursor = conn.cursor()
                current_time = int(time.time())

                cursor.execute("SELECT id, target_chat_id, addressee, text FROM tasks WHERE execution_time <= ?", (current_time,))
                tasks_to_execute = cursor.fetchall()

                if tasks_to_execute:
                    for task in tasks_to_execute:
                        task_id, target_chat_id, addressee, text = task
                        try:
                            message = f"@{addressee}, напоминаю: {text}" if addressee != 'мне' else f"Напоминаю: {text}"
                            await client.send_message(target_chat_id, message)
                            log_func(f"Scheduler ({db_id}): Executed task {task_id}", "info")
                            cursor.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
                            conn.commit()
                        except Exception as e:
                            log_func(f"Scheduler ({db_id}): Failed to execute task {task_id}. Error: {e}", "error")
        except Exception as e:
            log_func(f"Scheduler ({db_id}): An error occurred in the scheduler loop: {e}", "error")

        await asyncio.sleep(10)
