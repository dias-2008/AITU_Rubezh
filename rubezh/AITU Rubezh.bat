@echo off
rem Пересобирает дашборд свежими данными и открывает в браузере.
cd /d "%~dp0"
python rubezh.py open
if errorlevel 1 pause
