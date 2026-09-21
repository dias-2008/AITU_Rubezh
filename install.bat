@echo off
rem AITU Rubezh: установка из исходников на Windows. Обычным пользователям
rem проще взять установщик из Releases; этот файл — для тех, кто клонировал репозиторий.
rem Ставит зависимости в .venv рядом, скачивает Chromium и запускает мастер.
cd /d "%~dp0"
where py >nul 2>nul || where python >nul 2>nul || (
  echo Нужен Python 3.11 или новее: https://www.python.org/downloads/ - при установке отметь "Add python.exe to PATH".
  pause & exit /b 1
)
if not exist .venv ( py -3 -m venv .venv 2>nul || python -m venv .venv )
call .venv\Scripts\activate.bat
python -m pip install --quiet --upgrade pip
python -m pip install --quiet -r rubezh\requirements.txt -r tg-digest\requirements.txt
python -m playwright install chromium
cd rubezh
python rubezh.py setup
pause
