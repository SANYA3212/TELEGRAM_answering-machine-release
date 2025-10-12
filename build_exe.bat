@echo off
setlocal
cd /d "%~dp0"

rem === 1) Создаём venv на Python 3.12 ===
py -3.12 -m venv venv

rem === 2) Ставим зависимости ===
call venv\Scripts\python -m pip install --upgrade pip
call venv\Scripts\pip install pyinstaller telethon httpx

rem === 3) Имена ===
set APP=TGUserbotGemini
set SRC=tg_userbot_gui_gemini.py

rem === 4) Чистим прошлые сборки ===
if exist build rd /s /q build
if exist dist rd /s /q dist
del /q "%APP%.spec" 2>nul

rem === 5) Собираем 1 файл (.exe) с консолью ===
call venv\Scripts\pyinstaller ^
  --noconfirm ^
  --clean ^
  --onefile ^
  --console ^
  --name "%APP%" ^
  "%SRC%"

echo.
if exist "dist\%APP%.exe" (
  echo [OK] Готово: dist\%APP%.exe

  echo Создаю run_with_pause.bat для удобного запуска...
  > run_with_pause.bat echo @echo off
  >> run_with_pause.bat echo cd /d "%%~dp0dist"
  >> run_with_pause.bat echo "%APP%.exe"
  >> run_with_pause.bat echo echo.
  >> run_with_pause.bat echo pause
  echo [OK] Готово: run_with_pause.bat

) else (
  echo [ERROR] Сборка не удалась
)
echo.
pause
endlocal
