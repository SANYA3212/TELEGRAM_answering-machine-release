@echo off
echo Creating virtual environment...
python -m venv .venv
echo Activating virtual environment...
call .venv\Scripts\activate
echo Upgrading pip...
python -m pip install --upgrade pip
echo Installing
pip install httpx
pip install telethon
pip install "httpx[socks]"  
pip install cryptg          
pip install deepgram-sdk
pip install pillow          
python -m pip install --user schedule
pip install openai
pip install telethon httpx
pause