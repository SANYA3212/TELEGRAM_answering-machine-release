# Telegram Userbot & Bot

This application serves as a powerful bridge between your Telegram account and the Google Gemini API, allowing you to automate responses, manage chats, and create interactive bots with advanced AI capabilities. It features a user-friendly graphical interface (GUI) built with Tkinter and supports two primary modes of operation: User Mode and Bot Mode.

## Key Features

- **Dual Mode Operation:**
  - **👤 User Mode:**  Automate your personal Telegram account to act as a userbot. The application can automatically reply to incoming messages in specified chats, using the Gemini API to generate human-like responses.
  - **🤖 Bot Mode:** Create and manage multiple Telegram bots. Each bot operates independently with its own token, system prompt, and conversation history, making it a versatile tool for building custom AI assistants.
- **Gemini API Integration:**  Leverages the power of Google's Gemini models (e.g., `gemini-1.5-flash`) to provide intelligent and context-aware message generation.
- **Deepgram Integration:**  Includes voice message transcription using Deepgram's AI speech-to-text service, allowing the bot to understand and respond to audio messages.
- **GUI for Easy Management:**  A comprehensive Tkinter-based GUI allows you to:
  - Manage active chats for the userbot.
  - Add, configure, and remove Telegram bots.
  - Customize system prompts for both modes.
  - View real-time logs of all operations.
  - Send messages directly from the application.
- **Built-in Scheduler:**  A task scheduler is integrated to handle time-based reminders and actions. You can ask the bot to "remind you to..." or "write to X in N minutes."
- **Persistent Chat History:**  Conversation histories are saved locally in `.json` files, ensuring that the AI can maintain context across multiple interactions.
- **Portable Executable:** The project can be easily built into a single, portable `.exe` file for Windows, making it simple to distribute and run without needing a Python environment.

## Setup Instructions

### For Users (Running the Executable)

1. **Download the Application:**
   - Obtain the `AlisaChanUserbot.exe` file from the [releases page](https://github.com/ToilOfficial/TELEGRAM_answering-machine-release/releases).
2. **Run `setup.bat`:**
   - Before running the main application, you must first run `setup.bat`. This script will create the necessary virtual environment and install all the required dependencies.
3. **Configure the Application:**
   - After running the setup, several `.json` configuration files will be created in the same directory. You must fill these out with your API keys and other required information. See the **Configuration** section below for details.
4. **Run the Application:**
   - Double-click `tg_userbot_gui_gemini.bat` to start the application.

### For Developers (Building from Source)

1. **Clone the Repository:**
   ```bash
   git clone https://github.com/ToilOfficial/TELEGRAM_answering-machine-release.git
   cd TELEGRAM_answering-machine-release
   ```
2. **Run `setup.bat`:**
   - This will create a Python virtual environment in a `.venv` folder and install all the dependencies listed in `requirements.txt`.
3. **Configure the Application:**
   - Create and fill in the required `.json` files as described in the **Configuration** section below.
4. **Run the Application:**
   - To run the application from the source code, execute the following command:
     ```bash
     .venv\Scripts\python.exe tg_userbot_gui_gemini.py
     ```
5. **Build the Executable (Optional):**
   - If you wish to build the single-file executable, run the `build_exe.bat` script. The final `.exe` will be located in the `dist` folder.

## Configuration

You will need to configure the following `.json` files before running the application:

### `telegram_api.json`

This file stores your personal Telegram API credentials.

- `api_id`: Your Telegram API ID.
- `api_hash`: Your Telegram API Hash.
- `session_file`: The name of the session file to be created (e.g., `userbot_session.session`).

> **Where to get a Telegram API key:**
> You can obtain your `api_id` and `api_hash` from [my.telegram.org](https://my.telegram.org).

### `api_text_model.json`

This file configures the connection to the Google Gemini API.

- `provider`: Should be set to `"gemini"`.
- `base_url`: The base URL for the Gemini API (defaults to `https://generativelanguage.googleapis.com`).
- `api_key`: Your Google Gemini API key.
- `model`: The specific Gemini model you want to use (e.g., `gemini-1.5-flash`).
- `rpm_limit`: The request-per-minute limit for the API (defaults to 45).

> **Where to get a Google Gemini API key:**
> You can obtain your API key from the [Google AI Studio](https://aistudio.google.com/app/apikey).

### `deepgram_api.json`

This file is for the Deepgram API key, used for transcribing voice messages.

- `api_key`: Your Deepgram API key.

> **Where to get a Deepgram API key:**
> You can sign up for a free API key at [Deepgram](https://deepgram.com/).

### `bots.json`

This file is used in **Bot Mode** to manage your Telegram bots. It is a list of bot objects, where each object contains a bot's token, description, and system prompt. You can manage this file through the GUI.

## Usage

### 👤 User Mode

In this mode, the application uses your personal Telegram account to automatically respond to messages in selected chats.

1. **Select the "User Mode" Tab:**
   - This is the default mode when the application starts.
2. **Select Chats:**
   - The left panel ("Все чаты") lists all your recent Telegram chats.
   - Select one or more chats and click the `>>` button to move them to the "Активные чаты" list.
3. **Configure the Persona:**
   - Use the "С кем общаемся" dropdown to define the persona the AI should adopt for a specific chat. This helps the AI tailor its responses.
4. **Start the Bridge:**
   - Click the "Запустить" button to start the userbot. It will now automatically reply to new messages in the active chats.

### 🤖 Bot Mode

In this mode, you can run multiple Telegram bots simultaneously.

1. **Select the "Bot Mode" Tab:**
2. **Add a Bot:**
   - Paste your bot's token into the "Токен нового бота" field and click "Добавить".
   - You can get a bot token by talking to the [@BotFather](https://t.me/botfather) on Telegram.
3. **Configure the Bot:**
   - Select a bot from the "Сохраненные боты" list.
   - You can then set a custom name/description and a detailed system prompt for the bot. The system prompt is crucial for defining the bot's personality and behavior.
   - Click "Сохранить изменения для бота" to save the configuration.
4. **Start the Bot(s):**
   - Select one or more bots from the list and click "Запустить". Each selected bot will come online and start responding to messages.
