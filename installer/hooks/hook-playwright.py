# Playwright ходит в Chromium через свой драйвер на Node — папка driver/ внутри
# пакета: node.exe и JS. В pyinstaller-hooks-contrib хука для playwright нет,
# поэтому кладём папку в сборку сами. Сами браузеры сюда не входят: их build.py
# ставит в browsers/ рядом с exe, а paths.py указывает на них через
# PLAYWRIGHT_BROWSERS_PATH.
from PyInstaller.utils.hooks import collect_data_files

datas = collect_data_files("playwright", subdir="driver", include_py_files=True)
