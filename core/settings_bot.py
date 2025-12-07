import asyncio
import os
from telethon import TelegramClient, events
from telethon.tl.types import ReplyInlineMarkup, KeyboardButtonRow, KeyboardButtonCallback

from core.paths import TMP_DIR
from core.config_loader import load_tg_config, load_api_config, save_api_config
from core.telegram_handlers import app_state
from app.gui_logger import log_message

client = None
bot_token = None
user_states = {}

async def start_settings_bot(token: str):
    """Starts the settings bot."""
    global client, bot_token
    if app_state.get("settings_bot_running"):
        log_message("Бот для настроек уже запущен.", level="warning")
        return

    bot_token = token
    bot_id = token.split(':', 1)[0]
    session_name = f"settings_bot_{bot_id}.session"
    session_path = os.path.join(TMP_DIR, session_name)

    try:
        api_id, api_hash, _ = load_tg_config()
        client = TelegramClient(session_path, api_id, api_hash)

        @client.on(events.NewMessage(pattern='/start'))
        async def start_handler(event):
            keyboard = [
                [KeyboardButtonCallback("👤 User Mode", b'user_mode_main'), KeyboardButtonCallback("🤖 Bot Mode", b'bot_mode_main')],
                [KeyboardButtonCallback("⚙️ Общие настройки", b'general_settings')],
                [KeyboardButtonCallback("📄 Логи", b'logs_main')]
            ]
            await event.reply(
                "Добро пожаловать! Выберите раздел для настройки:",
                buttons=keyboard
            )

        @client.on(events.CallbackQuery)
        async def callback_handler(event):
            user_id = event.sender_id
            data = event.data.decode('utf-8')

            if data == 'general_settings':
                _, model, _ = load_api_config()
                temp = app_state.get("temp_var", 0.7)

                keyboard = [
                    [KeyboardButtonCallback(f"Модель: {model}", b'change_model')],
                    [KeyboardButtonCallback(f"Температура: {temp:.1f}", b'change_temp')],
                    [KeyboardButtonCallback("⬅️ Назад", b'start')]
                ]
                await event.edit("Общие настройки:", buttons=keyboard)

            elif data == 'change_model':
                user_states[user_id] = 'awaiting_model'
                await event.answer("Пожалуйста, отправьте новое название модели Gemini.", alert=True)

            elif data.startswith('change_temp'):
                if data == 'change_temp':
                    temp = app_state.get("temp_var", 0.7)
                else:
                    try:
                        temp = float(data.split(':')[1])
                    except (ValueError, IndexError):
                        temp = app_state.get("temp_var", 0.7)

                temp = max(0.0, min(2.0, temp))
                app_state["temp_var"] = temp

                keyboard = [
                    [
                        KeyboardButtonCallback("-0.1", f"change_temp:{temp-0.1:.1f}"),
                        KeyboardButtonCallback(f"{temp:.1f}", b'noop'), # No operation
                        KeyboardButtonCallback("+0.1", f"change_temp:{temp+0.1:.1f}")
                    ],
                    [KeyboardButtonCallback("⬅️ Назад", b'general_settings')]
                ]
                await event.edit("Изменить температуру:", buttons=keyboard)

            elif data == 'start':
                if user_id in user_states:
                    del user_states[user_id]
                keyboard = [
                    [KeyboardButtonCallback("👤 User Mode", b'user_mode_main'), KeyboardButtonCallback("🤖 Bot Mode", b'bot_mode_main')],
                    [KeyboardButtonCallback("⚙️ Общие настройки", b'general_settings')],
                    [KeyboardButtonCallback("📄 Логи", b'logs_main')]
                ]
                await event.edit("Добро пожаловать! Выберите раздел для настройки:", buttons=keyboard)

        @client.on(events.NewMessage(func=lambda e: e.sender_id in user_states))
        async def message_handler(event):
            user_id = event.sender_id
            state = user_states.get(user_id)

            if state == 'awaiting_model':
                new_model = event.text.strip()
                try:
                    save_api_config(new_model)
                    await event.reply(f"✅ Модель обновлена на: {new_model}")
                    log_message(f"Модель Gemini изменена на '{new_model}' через бота.", level="info")
                except Exception as e:
                    await event.reply(f"❗️ Ошибка при сохранении модели: {e}")
                finally:
                    del user_states[user_id]
                    # Возвращаем пользователя в меню общих настроек
                    _, model, _ = load_api_config()
                    temp = app_state.get("temp_var", 0.7)
                    keyboard = [
                        [KeyboardButtonCallback(f"Модель: {model}", b'change_model')],
                        [KeyboardButtonCallback(f"Температура: {temp:.1f}", b'change_temp')],
                        [KeyboardButtonCallback("⬅️ Назад", b'start')]
                    ]
                    await client.send_message(user_id, "Общие настройки:", buttons=keyboard)

        await client.start(bot_token=bot_token)
        me = await client.get_me()

        app_state["settings_bot_running"] = True
        log_message(f"✅ Бот для настроек '{me.first_name}' запущен.", level="focus")
        if "update_settings_bot_buttons" in app_state["gui_update_callbacks"]:
            app_state["gui_update_callbacks"]["update_settings_bot_buttons"]()

    except Exception as e:
        log_message(f"❗️ Ошибка запуска бота для настроек: {e}", level="error")
        if client and client.is_connected():
            await client.disconnect()
        app_state["settings_bot_running"] = False
        if "update_settings_bot_buttons" in app_state["gui_update_callbacks"]:
            app_state["gui_update_callbacks"]["update_settings_bot_buttons"]()

async def stop_settings_bot():
    """Stops the settings bot."""
    global client
    if not app_state.get("settings_bot_running") or not client:
        log_message("Бот для настроек не запущен.", level="warning")
        return

    try:
        await client.disconnect()
        log_message("Бот для настроек остановлен.", level="info")
    except Exception as e:
        log_message(f"Ошибка при остановке бота для настроек: {e}", level="error")
    finally:
        app_state["settings_bot_running"] = False
        client = None
        if "update_settings_bot_buttons" in app_state["gui_update_callbacks"]:
            app_state["gui_update_callbacks"]["update_settings_bot_buttons"]()
