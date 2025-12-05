import asyncio
import json
import os
import re
import threading
import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext
import importlib

from telethon import TelegramClient, events

import core.scheduler as scheduler
from core.paths import STATE_FILE, TMP_DIR
from core.config_loader import (ensure_api_config, ensure_tg_config, ensure_deepgram_config,
                                ensure_bots_config, load_prompt_config, load_bots, save_bots, load_api_config)
from core.history_manager import load_history, save_history, clear_chat_history, clear_bot_history
from core.telegram_handlers import get_dialogs, multi_chat_handler, bot_message_handler, app_state
from app.gui_logger import log_message, init_logger, clear_log as clear_log_widget
from core.api_clients import gemini_generate

# ===================== Глобальное состояние GUI =====================
client = None
handler_ref = None
scheduler_task = None
aio_loop = None
aio_loop_ready = threading.Event()

# ===================== Async event loop =====================
def start_background_loop():
    global aio_loop
    aio_loop = asyncio.new_event_loop()
    asyncio.set_event_loop(aio_loop)
    aio_loop_ready.set()
    aio_loop.run_forever()

def run_async(coro):
    aio_loop_ready.wait()
    return asyncio.run_coroutine_threadsafe(coro, aio_loop)

# ===================== Telegram Listeners =====================
async def start_user_listeners():
    global handler_ref, scheduler_task, client
    from core.config_loader import load_tg_config

    importlib.reload(scheduler)
    app_state["running_bots"].clear()
    api_id, api_hash, session = load_tg_config()
    client = TelegramClient(session, api_id, api_hash)
    await client.start()

    if scheduler_task and not scheduler_task.done():
        scheduler_task.cancel()
    db_id = "user_mode"
    scheduler.init_db(db_id)
    scheduler_task = asyncio.create_task(scheduler.scheduler_loop(db_id, client, log_message))
    me = await client.get_me()

    app_state["active_chat_id_map"] = { v["entity"].id: k for k, v in app_state["active_chat_entities"].items() }
    entities = [v["entity"] for v in app_state["active_chat_entities"].values()]

    handler_ref = multi_chat_handler
    client.add_event_handler(handler_ref, events.NewMessage(chats=entities))

    clear_log_widget()
    log_message(f"✅ [User Mode] Подключено как {me.first_name} (id={me.id})", level="focus")
    _, model_name, _ = load_api_config()
    log_message(f"🤖 Провайдер: gemini | Модель: {model_name}", level="info")
    log_message(f"🚀 Мост запущен для {len(entities)} чатов. Жду сообщения.", level="info")

async def start_bot_listeners(bot_token: str):
    from core.config_loader import load_tg_config
    importlib.reload(scheduler)
    bot_id = bot_token.split(':', 1)[0]
    session_name = f"bot_{bot_id}.session"
    session_path = os.path.join(TMP_DIR, session_name)
    api_id, api_hash, _ = load_tg_config()

    bot_client = TelegramClient(session_path, api_id, api_hash)
    bot_desc = "N/A"
    try:
        bot_data = next((b for b in app_state["bots_list"] if b.get('token') == bot_token), None)
        if bot_data: bot_desc = bot_data.get('desc', 'N/A')

        await bot_client.start(bot_token=bot_token)
        me = await bot_client.get_me()

        handler = lambda evt: bot_message_handler(evt, bot_token=bot_token, db_id=f"bot_{bot_id}")
        bot_client.add_event_handler(handler, events.NewMessage(incoming=True))

        db_id = f"bot_{bot_id}"
        scheduler.init_db(db_id)
        bot_scheduler_task = asyncio.create_task(scheduler.scheduler_loop(db_id, bot_client, log_message))

        app_state["running_bots"][bot_token] = {"client": bot_client, "handler": handler, "scheduler_task": bot_scheduler_task}

        log_message(f"✅ [Bot Mode] Бот {me.first_name} (id={me.id}) подключен.", level="focus")
        app_state["gui_update_callbacks"]["update_bots_listbox"]()

    except Exception as e:
        log_message(f"❗️ Ошибка запуска бота '{bot_desc}': {e}", level="error")
        if "The authorization key has expired" in str(e) and os.path.exists(session_path):
             os.remove(session_path)
             log_message(f"   Файл сессии {session_name} удален. Попробуйте перезапустить бота.", level="warning")
        if bot_client.is_connected():
            await bot_client.disconnect()
        if bot_token in app_state["running_bots"]:
            del app_state["running_bots"][bot_token]
        app_state["gui_update_callbacks"]["update_bots_listbox"]()

async def stop_listeners(tokens_to_stop=None):
    global scheduler_task

    if tokens_to_stop is None:
        tokens_to_stop = list(app_state["running_bots"].keys())
        if client and client.is_connected():
            await client.disconnect()
            if scheduler_task and not scheduler_task.done():
                scheduler_task.cancel()
                scheduler_task = None
            log_message("Клиент пользователя остановлен.", level="info")

    for token in tokens_to_stop:
        if token in app_state["running_bots"]:
            try:
                bot_info = app_state["running_bots"].pop(token)
                bot_client = bot_info['client']
                if bot_client.is_connected():
                    await bot_client.disconnect()

                bot_scheduler_task = bot_info.get('scheduler_task')
                if bot_scheduler_task and not bot_scheduler_task.done():
                    bot_scheduler_task.cancel()

                bot_desc = next((b.get('desc', 'N/A') for b in app_state["bots_list"] if b.get('token') == token), token[:5])
                log_message(f"Бот '{bot_desc}' остановлен.", level="info")

            except Exception as e:
                log_message(f"Ошибка при остановке бота с токеном {token[:5]}...: {e}", level="error")

    app_state["gui_update_callbacks"]["update_bots_listbox"]()
    if not app_state["running_bots"] and not (client and client.is_connected()):
        log_message("⛔ Все мосты остановлены.", level="error")
        app_state["bots_running"] = False


# ===================== GUI Main Application =====================
class TelegramBridgeApp:
    def __init__(self, root):
        self.root = root
        self.chat_entities = []
        self.filtered_chats = []
        app_state["active_chat_entities"] = {}
        app_state["gui_update_callbacks"] = {"update_bots_listbox": self.update_bots_listbox}
        self.setup_ui()
        self.load_initial_data()

    def setup_ui(self):
        BG, SEARCH_BG, CHAT_COLOR, BLUE, FG, FRIEND_GREEN = "#171717", "#3e3e3e", "#c714c9", "#1e66ff", "#ffffff", "#17a556"
        self.root.title("TG Userbot & Bot — Gemini Bridge")
        self.root.configure(bg=BG)
        self.root.option_add("*selectBackground", "#0f0f0f"); self.root.option_add("*selectForeground", FRIEND_GREEN)
        # ... (rest of UI setup)
        self.create_styles(BG, FG, SEARCH_BG, FRIEND_GREEN, BLUE)
        main_frame = ttk.Frame(self.root, padding=8, style="Dark.TLabel")
        main_frame.pack(fill="both", expand=True)
        main_frame.columnconfigure(0, weight=3); main_frame.columnconfigure(1, weight=3); main_frame.rowconfigure(0, weight=1)

        notebook_frame = ttk.Frame(main_frame, style="Dark.TLabel")
        notebook_frame.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        self.notebook = ttk.Notebook(notebook_frame, style="TNotebook")
        self.notebook.pack(fill="both", expand=True)

        self.create_user_tab(self.notebook, BG, SEARCH_BG, CHAT_COLOR, BLUE, FG, FRIEND_GREEN)
        self.create_bot_tab(self.notebook, BG, SEARCH_BG, BLUE, FG, FRIEND_GREEN)
        self.create_cleanup_tab(self.notebook, BG, FRIEND_GREEN)
        self.create_right_panel(main_frame, BG, SEARCH_BG, BLUE, FG, FRIEND_GREEN)

        self.operation_mode_var = tk.StringVar(value="user")
        self.notebook.bind("<<NotebookTabChanged>>", self.on_tab_change)

    def create_styles(self, BG, FG, SEARCH_BG, FRIEND_GREEN, BLUE):
        style = ttk.Style(); style.theme_use("clam")
        style.configure(".", background=BG, foreground=FG, fieldbackground=BG)
        style.configure("Dark.TLabel", background=BG, foreground=FG)
        style.configure("Dark.TButton", background=BG, foreground=FG, relief="flat", padding=6)
        style.map("Dark.TButton", background=[("disabled","#121212"),("active","#1f1f1f"),("pressed","#232323")], foreground=[("disabled","#8a8a8a")])
        style.configure("Dark.TCheckbutton", background=BG, foreground=FG)
        style.configure("Friend.TCombobox", fieldbackground="#101010", background="#101010", foreground=FRIEND_GREEN, bordercolor="#0a0a0a", lightcolor="#0d0d0d", darkcolor="#0a0a0a", arrowcolor=FRIEND_GREEN)
        style.map("Friend.TCombobox", fieldbackground=[("readonly","#101010"),("focus","#0e0e0e"),("active","#0e0e0e")], background=[("readonly","#101010"),("focus","#0e0e0e"),("active","#0e0e0e")], foreground=[("!disabled",FRIEND_GREEN)], arrowcolor=[("!disabled",FRIEND_GREEN)])
        style.configure("TNotebook", background=BG, borderwidth=0); style.configure("TNotebook.Tab", background=BG, foreground=FG, padding=[5, 2], relief="flat"); style.map("TNotebook.Tab", background=[("selected", SEARCH_BG)], expand=[("selected", [1, 1, 1, 0])])

    def create_user_tab(self, notebook, BG, SEARCH_BG, CHAT_COLOR, BLUE, FG, FRIEND_GREEN):
        user_tab = ttk.Frame(notebook, style="Dark.TLabel", padding=2); user_tab.columnconfigure(0, weight=2); user_tab.columnconfigure(1, weight=0); user_tab.columnconfigure(2, weight=2); user_tab.rowconfigure(0, weight=1)
        notebook.add(user_tab, text="👤 User Mode")
        left = ttk.Frame(user_tab, style="Dark.TLabel"); left.grid(row=0, column=0, sticky="nsew", padx=(0, 4)); left.rowconfigure(2, weight=1); left.columnconfigure(0, weight=1)
        ttk.Label(left, text="Все чаты:", style="Dark.TLabel").grid(row=0, column=0, sticky="w")
        self.chat_search = tk.Entry(left, bg=SEARCH_BG, fg=FG, insertbackground=FG, relief="flat"); self.chat_search.grid(row=1, column=0, sticky="we", pady=4); self.chat_search.bind("<KeyRelease>", self.on_search)
        self.chat_listbox = tk.Listbox(left, bg=BG, fg=CHAT_COLOR, selectbackground=BLUE, selectforeground=FG, activestyle="none", exportselection=False, width=32, selectmode="extended"); self.chat_listbox.grid(row=2, column=0, sticky="nsew")
        mid_buttons = ttk.Frame(user_tab, style="Dark.TLabel"); mid_buttons.grid(row=0, column=1, sticky="ns", padx=4); mid_buttons.rowconfigure(0, weight=1); mid_buttons.rowconfigure(1, weight=1)
        ttk.Button(mid_buttons, text=">>", command=self.on_add_chat, style="Dark.TButton", width=4).pack(pady=(150, 5))
        ttk.Button(mid_buttons, text="<<", command=self.on_remove_chat, style="Dark.TButton", width=4).pack(pady=5)
        active_chats_frame = ttk.Frame(user_tab, style="Dark.TLabel"); active_chats_frame.grid(row=0, column=2, sticky="nsew", padx=(0, 8)); active_chats_frame.rowconfigure(1, weight=3); active_chats_frame.rowconfigure(3, weight=2); active_chats_frame.columnconfigure(0, weight=1)
        ttk.Label(active_chats_frame, text="Активные чаты:", style="Dark.TLabel").grid(row=0, column=0, sticky="w")
        self.active_chats_listbox = tk.Listbox(active_chats_frame, bg=BG, fg=FRIEND_GREEN, selectbackground=BLUE, selectforeground=FG, activestyle="none", exportselection=False, width=32, selectmode="extended"); self.active_chats_listbox.grid(row=1, column=0, sticky="nsew", pady=(4,0)); self.active_chats_listbox.bind("<<ListboxSelect>>", self.on_active_chat_select)
        ttk.Label(active_chats_frame, text="Доп. промпт для чата:", style="Dark.TLabel").grid(row=2, column=0, sticky="w", pady=(8,0))
        self.custom_prompt_text = scrolledtext.ScrolledText(active_chats_frame, height=5, bg=SEARCH_BG, fg=FG, relief="flat", insertbackground=FG); self.custom_prompt_text.grid(row=3, column=0, sticky="nsew", pady=4)
        ttk.Button(active_chats_frame, text="Сохранить промпт", command=self.on_save_prompt, style="Dark.TButton").grid(row=4, column=0, sticky="ew")

    def create_bot_tab(self, notebook, BG, SEARCH_BG, BLUE, FG, FRIEND_GREEN):
        bot_tab = ttk.Frame(notebook, style="Dark.TLabel", padding=2)
        bot_tab.columnconfigure(0, weight=1)
        bot_tab.columnconfigure(1, weight=1)
        bot_tab.rowconfigure(1, weight=1)
        notebook.add(bot_tab, text="🤖 Bot Mode")

        bot_list_frame = ttk.Frame(bot_tab, style="Dark.TLabel")
        bot_list_frame.grid(row=0, column=0, rowspan=2, sticky="nsew", padx=(0, 5))
        bot_list_frame.columnconfigure(0, weight=1)
        bot_list_frame.rowconfigure(2, weight=1)

        add_bot_frame = ttk.Frame(bot_list_frame, style="Dark.TLabel")
        add_bot_frame.grid(row=0, column=0, sticky="ew", pady=(0, 5))
        add_bot_frame.columnconfigure(0, weight=1)
        ttk.Label(add_bot_frame, text="Токен нового бота:", style="Dark.TLabel").grid(row=0, column=0, columnspan=2, sticky="w")
        self.bot_token_entry = tk.Entry(add_bot_frame, bg=SEARCH_BG, fg=FG, insertbackground=FG, relief="flat")
        self.bot_token_entry.grid(row=1, column=0, sticky="ew", pady=(0, 5))
        bot_buttons_frame = ttk.Frame(add_bot_frame, style="Dark.TLabel")
        bot_buttons_frame.grid(row=1, column=1, sticky="w", padx=(5,0))
        ttk.Button(bot_buttons_frame, text="Вставить", command=self.on_paste_token, style="Dark.TButton").pack(side="left", padx=(0, 5))
        ttk.Button(bot_buttons_frame, text="Добавить", command=self.on_add_bot, style="Dark.TButton").pack(side="left")

        ttk.Label(bot_list_frame, text="Сохраненные боты:", style="Dark.TLabel").grid(row=1, column=0, sticky="w", pady=(5,0))
        self.bot_listbox = tk.Listbox(bot_list_frame, bg=BG, fg=FRIEND_GREEN, selectbackground=BLUE, selectforeground=FG, activestyle="none", exportselection=False, width=40, selectmode="extended")
        self.bot_listbox.grid(row=2, column=0, sticky="nsew")
        self.bot_listbox.bind("<<ListboxSelect>>", self.on_bot_select)
        ttk.Button(bot_list_frame, text="Удалить выбранного бота", command=self.on_delete_bot, style="Dark.TButton").grid(row=3, column=0, sticky="ew", pady=(5,0))

        bot_edit_frame = ttk.Frame(bot_tab, style="Dark.TLabel")
        bot_edit_frame.grid(row=0, column=1, rowspan=2, sticky="nsew")
        bot_edit_frame.columnconfigure(0, weight=1)
        bot_edit_frame.rowconfigure(3, weight=1)
        ttk.Label(bot_edit_frame, text="Имя/описание бота:", style="Dark.TLabel").grid(row=0, column=0, sticky="w")
        self.bot_desc_entry = tk.Entry(bot_edit_frame, bg=SEARCH_BG, fg=FG, insertbackground=FG, relief="flat")
        self.bot_desc_entry.grid(row=1, column=0, sticky="ew", pady=(0, 10))
        ttk.Label(bot_edit_frame, text="Системный промпт бота:", style="Dark.TLabel").grid(row=2, column=0, sticky="w")
        self.bot_prompt_text = scrolledtext.ScrolledText(bot_edit_frame, height=10, bg=SEARCH_BG, fg=FG, relief="flat", insertbackground=FG)
        self.bot_prompt_text.grid(row=3, column=0, sticky="nsew", pady=4)
        ttk.Button(bot_edit_frame, text="Сохранить изменения для бота", command=self.on_save_bot, style="Dark.TButton").grid(row=4, column=0, sticky="ew", pady=(5,0))

    def create_cleanup_tab(self, notebook, BG, FRIEND_GREEN):
        cleanup_tab = ttk.Frame(notebook, style="Dark.TLabel", padding=10)
        cleanup_tab.columnconfigure(0, weight=1)
        notebook.add(cleanup_tab, text="🧹 Очистка")
        cleanup_frame = ttk.Frame(cleanup_tab, style="Dark.TLabel", padding=10)
        cleanup_frame.pack(anchor="n", fill="x")
        cleanup_frame.columnconfigure(0, weight=1)
        ttk.Label(cleanup_frame, text="Очистка истории для бота", style="Dark.TLabel").grid(row=0, column=0, sticky="w")
        self.cleanup_bot_combo = ttk.Combobox(cleanup_frame, state="readonly", style="Friend.TCombobox")
        self.cleanup_bot_combo.grid(row=1, column=0, sticky="ew", pady=5)
        self.style_combobox_dropdown(self.cleanup_bot_combo, bg="#101010", fg="#ffffff", sel_bg="#0f0f0f", sel_fg=FRIEND_GREEN)
        ttk.Button(cleanup_frame, text="УДАЛИТЬ ИСТОРИЮ ВЫБРАННОГО БОТА", command=self.on_clear_bot_history, style="Dark.TButton").grid(row=2, column=0, sticky="ew", pady=5)

    def create_right_panel(self, main_frame, BG, SEARCH_BG, BLUE, FG, FRIEND_GREEN):
        right = ttk.Frame(main_frame, style="Dark.TLabel"); right.grid(row=0, column=1, sticky="nsew"); right.columnconfigure(0, weight=1); right.columnconfigure(1, weight=1); right.columnconfigure(2, weight=1); right.rowconfigure(12, weight=1)
        ttk.Label(right, text="С кем общаемся (для User Mode):", style="Dark.TLabel").grid(row=0, column=0, columnspan=3, sticky="w")
        self.friend_combo = ttk.Combobox(right, width=50, state="readonly", style="Friend.TCombobox"); self.friend_combo.grid(row=1, column=0, columnspan=3, sticky="we", pady=(0,6)); self.style_combobox_dropdown(self.friend_combo, bg="#101010", fg="#ffffff", sel_bg="#0f0f0f", sel_fg=FRIEND_GREEN); self.friend_combo.bind("<<ComboboxSelected>>", self.on_friend_change)
        self.see_my_msgs_var = tk.BooleanVar(value=False); ttk.Checkbutton(right, text="Видеть мои сообщения (User Mode)", variable=self.see_my_msgs_var, style="Dark.TCheckbutton").grid(row=2, column=0, columnspan=2, sticky="w", pady=(0,6))
        self.verbose_logging_var = tk.BooleanVar(value=False); ttk.Checkbutton(right, text="Подробные логи", variable=self.verbose_logging_var, style="Dark.TCheckbutton").grid(row=2, column=2, sticky="w", pady=(0,6))
        ttk.Label(right, text="Температура модели:", style="Dark.TLabel").grid(row=3, column=0, sticky="w")
        self.temp_var = tk.DoubleVar(value=0.7); tk.Scale(right, from_=0.0, to=2.0, resolution=0.1, orient="horizontal", variable=self.temp_var, showvalue=False, bg=BG, fg=FG, highlightthickness=0, troughcolor=BLUE, activebackground=BLUE, relief="flat", bd=0, command=self.on_temp_change).grid(row=3, column=1, sticky="we", pady=2)
        self.temp_value_label = ttk.Label(right, text=f"{self.temp_var.get():.1f}", style="Dark.TLabel"); self.temp_value_label.grid(row=3, column=2, sticky="w")
        self.start_btn = ttk.Button(right, text="Запустить", command=self.on_start, style="Dark.TButton"); self.stop_btn = ttk.Button(right, text="Остановить", command=self.on_stop_button_click, style="Dark.TButton"); self.restart_btn = ttk.Button(right, text="Перезагрузить", command=self.on_restart, style="Dark.TButton"); self.clear_btn = ttk.Button(right, text="Очистить историю (User Mode)", command=self.on_clear_user_history, style="Dark.TButton")
        self.start_btn.grid(row=4, column=0, pady=6, sticky="we"); self.stop_btn.grid(row=4, column=1, pady=6, sticky="we"); self.restart_btn.grid(row=4, column=2, pady=6, sticky="we"); self.clear_btn.grid(row=5, column=0, columnspan=3, pady=(0,6), sticky="we")
        ttk.Label(right, text="Отправить в выбранный чат (User Mode):", style="Dark.TLabel").grid(row=6, column=0, columnspan=3, sticky="w", pady=(8,0))
        self.gui_message_text = scrolledtext.ScrolledText(right, height=3, bg=SEARCH_BG, fg=FG, relief="flat", insertbackground=FG); self.gui_message_text.grid(row=7, column=0, columnspan=3, sticky="nsew", pady=4)
        ttk.Button(right, text="Спросить ИИ (приватно)", command=lambda: run_async(self.on_send_from_gui()), style="Dark.TButton").grid(row=8, column=0, columnspan=3, sticky="ew", pady=(0,2))
        ttk.Button(right, text="Отправить ответ в ЧАТ В ФОКУСЕ", command=lambda: run_async(self.on_send_to_focused_chat()), style="Dark.TButton").grid(row=9, column=0, columnspan=3, sticky="ew")
        self.status_label = ttk.Label(right, text="Состояние: Остановлен", style="Dark.TLabel", foreground="red"); self.status_label.grid(row=10, column=0, columnspan=3, sticky="w", pady=(2,6))
        ttk.Label(right, text="Лог:", style="Dark.TLabel").grid(row=11, column=0, columnspan=3, sticky="w")
        self.log_text = scrolledtext.ScrolledText(right, width=80, height=18, bg=BG, fg=FG, insertbackground=FG, relief="flat"); self.log_text.tag_config("violet", foreground="#b388ff"); self.log_text.tag_config("green", foreground="lightgreen"); self.log_text.tag_config("red", foreground="red"); self.log_text.tag_config("white", foreground="white"); self.log_text.tag_config("yellow", foreground="#FFFF88"); self.log_text.tag_config("grey", foreground="grey"); self.log_text.grid(row=12, column=0, columnspan=3, sticky="nsew")

    def load_initial_data(self):
        ensure_api_config(); ensure_tg_config(); ensure_deepgram_config(); ensure_bots_config()
        app_state["system_prompt"], app_state["friends"], app_state["noname"] = load_prompt_config()
        app_state["bots_list"] = load_bots()
        self.friend_combo['values'] = [f"{n} — {d}" for n, d in app_state["friends"]] + [f"{app_state['noname'][0]} — {app_state['noname'][1]}"]
        self.friend_combo.current(0)

        loop_thread = threading.Thread(target=start_background_loop, daemon=True); loop_thread.start(); aio_loop_ready.wait()

        self.refresh_dialogs_from_async(on_done=self.restore_gui_state)
        self.set_buttons(False)

    def on_start(self):
        mode = self.operation_mode_var.get()
        if mode == "user":
            if app_state["bots_running"]:
                messagebox.showinfo("Info", "Мост уже запущен."); return
            if not app_state["active_chat_entities"]:
                messagebox.showwarning("Ошибка", "Добавьте хотя бы один чат в список активных.")
                return
            self.set_buttons(True)
            run_async(start_user_listeners())
            app_state["bots_running"] = True
        elif mode == "bot":
            selections = self.bot_listbox.curselection()
            if not selections:
                messagebox.showwarning("Ошибка", "Выберите хотя бы одного бота для запуска.")
                return
            for i in selections:
                bot_token = app_state["bots_list"][i].get('token')
                if not bot_token:
                    log_message(f"У бота {app_state['bots_list'][i].get('desc')} нет токена, пропускаем.", "warning")
                    continue
                if bot_token in app_state["running_bots"]:
                    log_message(f"Бот {app_state['bots_list'][i].get('desc')} уже запущен, пропускаем.", "warning")
                    continue
                run_async(start_bot_listeners(bot_token))
            if app_state["running_bots"]:
                app_state["bots_running"] = True
                self.set_buttons(True)

    def on_stop_button_click(self):
        mode = self.operation_mode_var.get()
        if mode == 'user':
            run_async(stop_listeners())
        else:
            selections = self.bot_listbox.curselection()
            if not selections:
                messagebox.showwarning("Внимание", "Выберите ботов для остановки.")
                return
            tokens_to_stop = [app_state["bots_list"][i]['token'] for i in selections if app_state["bots_list"][i]['token'] in app_state["running_bots"]]
            if not tokens_to_stop:
                messagebox.showinfo("Информация", "Ни один из выбранных ботов не запущен.")
                return
            run_async(stop_listeners(tokens_to_stop))
        if not app_state["running_bots"]:
            self.set_buttons(False)

    def on_restart(self):
        run_async(stop_listeners())
        app_state["bots_running"] = False
        self.set_buttons(False)
        if self.operation_mode_var.get() == "user":
            self.refresh_dialogs_from_async(clear_selection=True)
        log_message("🔄 Перезагрузка завершена. Готов к запуску.", "info")

    def on_close(self):
        self.save_gui_state()
        run_async(stop_listeners())
        if aio_loop: aio_loop.call_soon_threadsafe(aio_loop.stop)
        self.root.destroy()

    def set_buttons(self, run):
        self.start_btn.configure(state='disabled' if run else 'normal')
        self.stop_btn.configure(state='normal' if run else 'disabled')
        self.status_label.configure(text="Состояние: Запущен" if run else "Состояние: Остановлен",
                                   foreground="lightgreen" if run else "red")

    def save_gui_state(self):
        try:
            active_chats_data = []
            for chat_name in self.active_chats_listbox.get(0, "end"):
                friend_index = app_state["active_chat_entities"].get(chat_name, {}).get("friend_index", 0)
                active_chats_data.append({"name": chat_name, "friend_index": friend_index})
            selected_bot_indices = []
            if self.bot_listbox and self.bot_listbox.curselection():
                selected_bot_indices = self.bot_listbox.curselection()
            state = {
                "active_chats": active_chats_data,
                "temperature": self.temp_var.get(),
                "see_my_msgs": self.see_my_msgs_var.get(),
                "selected_tab": self.notebook.index(self.notebook.select()),
                "selected_bot_indices": selected_bot_indices
            }
            with open(STATE_FILE, "w", encoding="utf-8") as f:
                json.dump(state, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"Failed to save GUI state: {e}")

    def load_gui_state(self):
        if not os.path.exists(STATE_FILE): return None
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f: return json.load(f)
        except Exception as e:
            print(f"Failed to load GUI state: {e}"); return None

    def restore_gui_state(self):
        self.update_bots_listbox()
        self.update_cleanup_tab_bots()
        saved_state = self.load_gui_state()
        if not saved_state: return
        self.temp_var.set(saved_state.get("temperature", 0.7))
        self.see_my_msgs_var.set(saved_state.get("see_my_msgs", False))
        active_chats_to_restore = saved_state.get("active_chats", [])
        if active_chats_to_restore: self.restore_active_chats(active_chats_to_restore)
        selected_bot_indices = saved_state.get("selected_bot_indices", [])
        for idx in selected_bot_indices:
            if self.bot_listbox.size() > idx: self.bot_listbox.selection_set(idx)
        if selected_bot_indices:
            self.bot_listbox.see(selected_bot_indices[-1]); self.on_bot_select()
        selected_tab = saved_state.get("selected_tab", 0)
        self.notebook.select(selected_tab)
        self.operation_mode_var.set("user" if selected_tab == 0 else "bot")

    def refresh_dialogs_from_async(self, clear_selection=False, on_done=None):
        def _done(fut):
            try:
                ds = fut.result()
                if self.root: self.root.after(0, self.update_chat_list, ds, clear_selection)
                log_message("Список чатов обновлён.", level="info")
                if on_done: on_done()
            except Exception as e: log_message(f"[Refresh Error] {e}", level="error")
        fut = run_async(get_dialogs())
        fut.add_done_callback(_done)

    def update_chat_list(self, dialogs, clear_selection=False):
        self.chat_entities = dialogs; self.filtered_chats = dialogs
        if not self.chat_listbox: return
        self.chat_listbox.delete(0, 'end')
        for i, (name, _) in enumerate(self.filtered_chats, 1): self.chat_listbox.insert('end', f"{i}. {name}")
        if clear_selection: self.chat_listbox.selection_clear(0, 'end')

    def update_bots_listbox(self):
        if not self.bot_listbox: return
        selected_indices = self.bot_listbox.curselection()
        self.bot_listbox.delete(0, 'end')
        for i, bot_data in enumerate(app_state["bots_list"]):
            token = bot_data.get('token'); desc = bot_data.get('desc', f'Bot {i+1}')
            status = " (Running)" if token in app_state["running_bots"] else ""
            token_preview = token[:12] if token else "NO_TOKEN"
            display_text = f"{desc} ({token_preview}...){status}"
            self.bot_listbox.insert('end', display_text)
            if status: self.bot_listbox.itemconfig(i, {'fg': '#40C040'})
        for idx in selected_indices:
            if idx < self.bot_listbox.size(): self.bot_listbox.selection_set(idx)

    def on_add_chat(self):
        selected_indices = self.chat_listbox.curselection()
        if not selected_indices: return
        current_active_chats = self.active_chats_listbox.get(0, "end")
        selected_friend_index = self.friend_combo.current()
        for i in selected_indices:
            chat_name, chat_entity = self.filtered_chats[i]
            if chat_name not in current_active_chats:
                app_state["active_chat_entities"][chat_name] = {"entity": chat_entity, "friend_index": selected_friend_index}
                self.active_chats_listbox.insert("end", chat_name)

    def on_remove_chat(self):
        selected_indices = self.active_chats_listbox.curselection()
        if not selected_indices: return
        for i in sorted(selected_indices, reverse=True):
            chat_name = self.active_chats_listbox.get(i)
            self.active_chats_listbox.delete(i)
            if chat_name in app_state["active_chat_entities"]: del app_state["active_chat_entities"][chat_name]

    def on_active_chat_select(self, _=None):
        sel = self.active_chats_listbox.curselection()
        if not sel: return
        chat_name = self.active_chats_listbox.get(sel[0])
        friend_index = app_state["active_chat_entities"].get(chat_name, {}).get("friend_index", 0)
        if friend_index >= len(app_state["friends"]): friend_index = 0
        self.friend_combo.current(friend_index)
        _, custom_prompt, _ = load_history(chat_name, mode='user')
        self.custom_prompt_text.delete('1.0', 'end')
        self.custom_prompt_text.insert('1.0', custom_prompt)

    def on_save_prompt(self):
        sel = self.active_chats_listbox.curselection()
        if not sel:
            messagebox.showwarning("Ошибка", "Сначала выберите чат в списке активных."); return
        chat_title = self.active_chats_listbox.get(sel[0])
        prompt_content = self.custom_prompt_text.get("1.0", "end-1c").strip()
        history, _, path = load_history(chat_title, mode='user')
        save_history(path, history, prompt_content)
        log_message(f"✅ Доп. промпт для чата '{chat_title}' сохранен.", level="info")

    def on_clear_user_history(self):
        sel = self.active_chats_listbox.curselection()
        if not sel:
            messagebox.showwarning("Ошибка", "Выбери активный чат."); return
        title = self.active_chats_listbox.get(sel[0])
        if not messagebox.askyesno("Подтверждение", f"Вы уверены, что хотите полностью удалить историю для чата '{title}'?"):
            return
        try:
            clear_chat_history(title, mode='user')
            self.on_active_chat_select()
            clear_log_widget()
            log_message(f"[Info] Файл истории для '{title}' был удален.", level="info")
        except Exception as e:
            messagebox.showerror("Ошибка", f"Не удалось удалить файл истории: {e}")

    async def on_send_from_gui(self):
        sel = self.active_chats_listbox.curselection()
        if not sel:
            messagebox.showwarning("Ошибка", "Сначала выберите чат для отправки."); return
        chat_title = self.active_chats_listbox.get(sel[0])
        text = self.gui_message_text.get("1.0", "end-1c").strip()
        if not text:
            messagebox.showwarning("Ошибка", "Введите текст сообщения."); return

        log_message(f"~> Отправка в [{chat_title}]: {text}", level="warning")
        chat_data = app_state["active_chat_entities"].get(chat_title, {})
        friend_index = chat_data.get("friend_index", len(app_state["friends"]))
        friend_name = app_state["noname"][0] if friend_index >= len(app_state["friends"]) else app_state["friends"][friend_index][0]
        history, custom_prompt, hist_path = load_history(chat_title, mode='user')
        history.append({"role": "user", "content": text})
        save_history(hist_path, history, custom_prompt)

        try:
            reply = await gemini_generate(history, friend_name, self.temp_var.get(), custom_prompt, app_state["system_prompt"])
        except Exception as e:
            log_message(f"[Send GUI Msg Error] {e}", level="error")
            return
        if reply:
            log_message(f"<~ Ответ для [{chat_title}]: {reply}", level="warning")
            history.append({"role": "assistant", "content": reply})
            save_history(hist_path, history, custom_prompt)
            self.gui_message_text.delete('1.0', 'end')
            log_message("   (переписка сохранена в историю)", level="info")

    async def on_send_to_focused_chat(self):
        if not app_state["bots_running"] or not client:
            messagebox.showerror("Ошибка", "Мост не запущен."); return
        sel = self.active_chats_listbox.curselection()
        if not sel:
            messagebox.showwarning("Ошибка", "Сначала выберите чат для отправки."); return
        chat_title = self.active_chats_listbox.get(sel[0])
        chat_entity = app_state["active_chat_entities"].get(chat_title, {}).get("entity")
        if not chat_entity:
            messagebox.showerror("Ошибка", "Не удалось найти объект чата."); return
        text = self.gui_message_text.get("1.0", "end-1c").strip()
        if not text:
            messagebox.showwarning("Ошибка", "Введите текст сообщения."); return

        log_message(f"~> Отправка в TELEGRAM [{chat_title}]: {text}", level="warning")
        friend_index = app_state["active_chat_entities"].get(chat_title, {}).get("friend_index", len(app_state["friends"]))
        friend_name = app_state["noname"][0] if friend_index >= len(app_state["friends"]) else app_state["friends"][friend_index][0]
        history, custom_prompt, hist_path = load_history(chat_title, mode='user')
        history.append({"role": "user", "content": text})

        try:
            reply = await gemini_generate(history, friend_name, self.temp_var.get(), custom_prompt, app_state["system_prompt"])
        except Exception as e:
            log_message(f"[Send TG Msg Error] {e}", level="error"); return
        if reply:
            log_message(f"<~ Ответ для [{chat_title}]: {reply}", level="warning")
            history.append({"role": "assistant", "content": reply})
            save_history(hist_path, history, custom_prompt)
            self.gui_message_text.delete('1.0', 'end')
            await client.send_message(chat_entity, reply)
            log_message("   (сообщение отправлено в Telegram)", level="info")

    def on_search(self, _):
        q = self.chat_search.get().lower()
        if not self.chat_listbox: return
        self.chat_listbox.delete(0, 'end')
        self.filtered_chats = [(n, e) for n, e in self.chat_entities if q in n.lower()]
        for i, (name, _) in enumerate(self.filtered_chats, 1):
            self.chat_listbox.insert('end', f"{i}. {name}")

    def on_friend_change(self, _):
        sel = self.active_chats_listbox.curselection()
        if not sel: return
        chat_name = self.active_chats_listbox.get(sel[0])
        new_friend_index = self.friend_combo.current()
        if chat_name in app_state["active_chat_entities"]:
            app_state["active_chat_entities"][chat_name]["friend_index"] = new_friend_index

    def on_add_bot(self):
        token = self.bot_token_entry.get().strip()
        if not re.match(r'^\d+:[a-zA-Z0-9_-]+$', token):
            messagebox.showerror("Ошибка", "Неверный формат токена."); return
        if any(b.get('token') == token for b in app_state["bots_list"]):
            messagebox.showwarning("Внимание", "Этот бот уже добавлен."); return
        new_bot_desc = f"Bot {len(app_state['bots_list']) + 1}"
        app_state["bots_list"].append({'token': token, 'desc': new_bot_desc, 'system_prompt': ''})
        save_bots(app_state["bots_list"])
        self.update_bots_listbox(); self.update_cleanup_tab_bots()
        self.bot_token_entry.delete(0, 'end')
        log_message(f"🤖 Добавлен новый бот: {new_bot_desc}", level="info")

    def on_delete_bot(self):
        selections = self.bot_listbox.curselection()
        if not selections:
            messagebox.showwarning("Внимание", "Выберите бота для удаления."); return
        indices_to_delete = sorted(selections, reverse=True)
        tokens_to_stop = []
        for i in indices_to_delete:
            token = app_state["bots_list"][i].get('token')
            if token and token in app_state["running_bots"]: tokens_to_stop.append(token)
        def perform_delete():
            for i in indices_to_delete:
                removed_bot = app_state["bots_list"].pop(i)
                log_message(f"🤖 Удален бот: {removed_bot.get('desc', 'N/A')}", level="info")
            save_bots(app_state["bots_list"])
            self.update_bots_listbox(); self.update_cleanup_tab_bots(); self.on_bot_select()
        if tokens_to_stop:
            future = run_async(stop_listeners(tokens_to_stop))
            future.add_done_callback(lambda f: perform_delete())
        else: perform_delete()

    def on_paste_token(self):
        try:
            self.bot_token_entry.delete(0, 'end')
            self.bot_token_entry.insert(0, self.root.clipboard_get())
        except tk.TclError: messagebox.showwarning("Буфер обмена", "Не удалось получить текст.")

    def on_save_bot(self):
        selections = self.bot_listbox.curselection()
        if not selections:
            messagebox.showwarning("Внимание", "Сначала выберите бота."); return
        bot_index = selections[0]
        new_desc = self.bot_desc_entry.get().strip()
        new_prompt = self.bot_prompt_text.get("1.0", "end-1c").strip()
        if not new_desc:
            messagebox.showerror("Ошибка", "Имя/описание бота не может быть пустым."); return
        app_state["bots_list"][bot_index]['desc'] = new_desc
        app_state["bots_list"][bot_index]['system_prompt'] = new_prompt
        save_bots(app_state["bots_list"])
        self.update_bots_listbox()
        self.bot_listbox.selection_set(bot_index)
        log_message(f"✅ Данные для бота '{new_desc}' сохранены.", level="info")

    def update_cleanup_tab_bots(self):
        if not self.cleanup_bot_combo: return
        bot_names = [bot.get('desc', f"Bot {i+1}") for i, bot in enumerate(app_state["bots_list"])]
        self.cleanup_bot_combo['values'] = bot_names
        if bot_names: self.cleanup_bot_combo.current(0)

    def on_clear_bot_history(self):
        selected_bot_index = self.cleanup_bot_combo.current()
        if selected_bot_index == -1:
            messagebox.showwarning("Внимание", "Пожалуйста, выберите бота."); return
        bot_data = app_state["bots_list"][selected_bot_index]
        bot_token = bot_data.get('token')
        if not messagebox.askyesno("Подтверждение", f"УДАЛИТЬ ВСЮ историю для '{bot_data.get('desc')}'?"): return
        try:
            clear_bot_history(bot_token)
            log_message(f"✅ Вся история для бота '{bot_data.get('desc')}' удалена.", level="info")
            messagebox.showinfo("Успех", "История переписок удалена.")
        except Exception as e:
            messagebox.showerror("Ошибка", f"Не удалось удалить историю: {e}")

    def on_bot_select(self, _=None):
        selections = self.bot_listbox.curselection()
        if not selections:
            self.bot_desc_entry.delete(0, 'end'); self.bot_prompt_text.delete('1.0', 'end'); return
        bot_index = selections[0]
        bot_data = app_state["bots_list"][bot_index]
        self.bot_desc_entry.delete(0, 'end'); self.bot_desc_entry.insert(0, bot_data.get('desc', ''))
        self.bot_prompt_text.delete('1.0', 'end'); self.bot_prompt_text.insert('1.0', bot_data.get('system_prompt', ''))

    def on_temp_change(self, val):
        try: v = float(val)
        except: v = 0.7
        app_state["temp_var"] = v
        if self.temp_value_label: self.temp_value_label.config(text=f"{v:.1f}")

    def style_combobox_dropdown(self, cb, bg, fg, sel_bg, sel_fg):
        def _apply(_=None):
            try:
                popdown = cb.tk.call("ttk::combobox::PopdownWindow", str(cb))
                lb = cb.nametowidget(f"{popdown}.f.l")
                lb.configure(background=bg, foreground=fg, selectbackground=sel_bg, selectforeground=sel_fg)
            except Exception: pass
        cb.bind("<Button-1>", lambda e: cb.after(10, _apply), add="+")

    def restore_active_chats(self, active_chats_data):
        for chat_data in active_chats_data:
            name = chat_data.get("name")
            friend_index = chat_data.get("friend_index", 0)
            if not name: continue
            for chat_name, chat_entity in self.chat_entities:
                if chat_name == name:
                    if name not in self.active_chats_listbox.get(0, "end"):
                        self.active_chats_listbox.insert("end", name)
                        app_state["active_chat_entities"][name] = {"entity": chat_entity, "friend_index": friend_index}
                    break

    def on_tab_change(self, event):
        selected_tab = self.notebook.index(self.notebook.select())
        mode = "user" if selected_tab == 0 else "bot"
        self.operation_mode_var.set(mode)
        log_message(f"Переключен режим на: {'User Mode' if mode == 'user' else 'Bot Mode'}", level="debug")

def main():
    root = tk.Tk()
    app = TelegramBridgeApp(root)
    init_logger(app.log_text, root, app.verbose_logging_var)
    root.protocol("WM_DELETE_WINDOW", app.on_close)
    root.mainloop()

if __name__ == "__main__":
    main()
