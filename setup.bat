@echo off
echo [+] Setting up Python virtual environment...
python -m venv .venv
echo [+] Installing dependencies from requirements.txt...
.venv\\Scripts\\pip.exe install -r requirements.txt
echo [+] Setup complete!
pause
