#!/bin/sh
# AITU Rubezh: установка из исходников на macOS (и Linux).
#
#   git clone https://github.com/dias-2008/AITU_Rubezh.git
#   cd AITU_Rubezh && sh install.sh
#
# Ставит зависимости в .venv рядом, скачивает Chromium для входа и запускает
# мастер первого запуска. Всё дальнейшее (ярлык, кнопка «Войти», проверка раз
# в час через launchd) мастер делает сам. Повторный запуск безопасен.
set -e
cd "$(dirname "$0")"

if ! command -v python3 >/dev/null 2>&1; then
  echo "Нужен Python 3.11 или новее: https://www.python.org/downloads/  (или: brew install python)"
  exit 1
fi
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' || {
  echo "Python слишком старый: $(python3 --version). Нужен 3.11+."; exit 1; }

[ -d .venv ] || python3 -m venv .venv
. .venv/bin/activate
python -m pip install --quiet --upgrade pip
python -m pip install --quiet -r rubezh/requirements.txt -r tg-digest/requirements.txt
python -m playwright install chromium
# туннель для кнопки «Телефон» — заранее, чтобы QR-код появлялся сразу
python -c "import sys; sys.path.insert(0, 'rubezh'); import phone; print('cloudflared:', phone.cloudflared())" || echo "cloudflared не скачался — скачается при первом нажатии «Телефон»"

cd rubezh
exec python rubezh.py setup
