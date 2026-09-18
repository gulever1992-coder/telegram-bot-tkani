@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo Бот запускается. Не закрывайте это окно, пока бот должен работать. Остановка: Ctrl+C.
py -3.10 bot.py
pause
