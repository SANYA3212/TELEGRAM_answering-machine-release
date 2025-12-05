@echo off
echo [+] Starting the application...
if not exist .venv\\Scripts\\python.exe (
    echo [!] Virtual environment not found. Please run setup.bat first.
    pause
    exit /b
)
.venv\\Scripts\\python.exe run.py
pause
