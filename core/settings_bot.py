import asyncio
from telethon import TelegramClient, events
from app.gui_logger import log_message
from core.config_loader import load_tg_config

# Placeholder for the settings bot client
settings_bot_client = None

async def start_settings_bot(token):
    """
    Starts the settings bot.
    """
    global settings_bot_client
    try:
        api_id, api_hash, _ = load_tg_config()
        session_name = "settings_bot_session.session"
        settings_bot_client = TelegramClient(session_name, api_id, api_hash)

        await settings_bot_client.start(bot_token=token)

        me = await settings_bot_client.get_me()
        log_message(f"✅ [Settings Bot] Бот {me.first_name} (id={me.id}) подключен.", level="focus")

        # Add handlers here
        # settings_bot_client.add_event_handler(start_command_handler, events.NewMessage(pattern='/start'))

    except Exception as e:
        log_message(f"❗️ Ошибка запуска бота для настроек: {e}", level="error")
        if settings_bot_client and settings_bot_client.is_connected():
            await settings_bot_client.disconnect()
        settings_bot_client = None

async def stop_settings_bot():
    """
    Stops the settings bot.
    """
    global settings_bot_client
    if settings_bot_client and settings_bot_client.is_connected():
        await settings_bot_client.disconnect()
        log_message("ℹ️ [Settings Bot] Бот для настроек отключен.", level="info")
    settings_bot_client = None
