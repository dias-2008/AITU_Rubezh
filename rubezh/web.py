"""Сборка и раздача дашборда.

Дашборд — несколько самодостаточных HTML-файлов рядом: главная и по странице на
раздел (задания, оценки, расписание, даты, силлабус). Данные вшиваются в них при
сборке — одним обходом источников на все страницы, — дальше они работают без
сервера и открываются двойным кликом с диска. Разметка у страниц одна: секции
помечены `data-page`, лишние прячутся при загрузке.

Почему так, а не веб-приложение: студент ничего не хостит. Файл лежит у него на
компьютере, как и сессии. Команда `serve` нужна только чтобы открыть дашборд с
телефона по локальной сети — она поднимает обычный http.server, без зависимостей.

    python rubezh.py build       собрать build/dashboard.html
    python rubezh.py serve       раздать, но только этому компьютеру (127.0.0.1)
    python rubezh.py serve --lan открыть для телефона в той же сети

Наружу отдаём только по явному --lan и только по неугадываемому адресу: на
странице оценки и посещаемость, а порт 8000 в университетской сети находится
сканированием за секунды.
"""
import json
import os
import secrets
import socket
from datetime import datetime
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import grade
import paths

ROOT = paths.ROOT
TEMPLATE = paths.ASSETS / "templates" / "dashboard.html"
BUILD = ROOT / "build"
PLACEHOLDER = "__RUBEZH_DATA__"
PAGE_PLACEHOLDER = "__RUBEZH_PAGE__"

# Раздел -> файл. Имена должны совпадать со списком PAGES в шаблоне: по ним
# строится боковое меню, и разъехавшееся имя даст ссылку в никуда.
PAGES = {
    "home": "dashboard.html",
    "tasks": "tasks.html",
    "grades": "grades.html",
    "schedule": "schedule.html",
    "dates": "dates.html",
    "syllabus": "syllabus.html",
}


def payload(demo=False):
    """Данные для дашборда.

    По умолчанию настоящие: пусть лучше будет честно пусто, чем правдоподобно
    неверно. Демо остаётся под флагом — оно нужно, чтобы посмотреть на инструмент
    до всякого логина.
    """
    if not demo:
        import live
        return live.gather()

    import demo as demo_data

    courses = []
    for course in demo_data.courses():
        result = grade.evaluate(course)
        result.pop("course", None)          # сам курс уже рядом, незачем дублировать
        courses.append({
            "code": course["code"], "title": course["title"],
            "credits": course["credits"], "teacher": course["teacher"],
            "items": course["items"], "eval": result,
        })

    return {
        "demo": demo,
        "student": demo_data.STUDENT,
        "group": demo_data.GROUP,
        "term": demo_data.TERM,
        "today": demo_data.TODAY,
        "updated": datetime.now().strftime("%d.%m.%Y %H:%M"),
        "courses": courses,
        "key_dates": demo_data.KEY_DATES,
        "schedule": demo_data.SCHEDULE,
    }


def fragment(data=None, demo=False, page="home"):
    """Дашборд без обвязки <html>: title, стили, разметка, скрипт."""
    data = payload(demo) if data is None else data
    body = TEMPLATE.read_text(encoding="utf-8")
    if PLACEHOLDER not in body:
        raise SystemExit(f"В шаблоне нет {PLACEHOLDER} — некуда вставить данные.")
    # </script> внутри строки JSON закрыл бы наш же тег раньше времени.
    blob = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    return body.replace(PLACEHOLDER, blob).replace(PAGE_PLACEHOLDER, page)


DOCTYPE = """<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
"""


def _write(path, data, demo, page):
    """Одна страница на диск. Замена файла целиком — атомарна.

    Пишем через временный файл: пока идёт вход, открытая вкладка перечитывает
    страницу каждые двадцать секунд и вполне может попасть в середину записи —
    и показать обрубок вместо дашборда.
    """
    marker = '<div class="shell">'
    head, found, rest = fragment(data, demo, page).partition(marker)
    if not found:
        raise SystemExit(f"В шаблоне нет {marker} — не понять, где кончается head.")
    tmp = path.with_suffix(".html.tmp")
    tmp.write_text(
        DOCTYPE + head + "</head>\n<body>\n" + marker + rest + "\n</body>\n</html>\n",
        encoding="utf-8",
    )
    os.replace(tmp, path)
    return path


def build(demo=False):
    """Все страницы дашборда рядом друг с другом, без всякого сервера.

    Разделы — отдельные файлы, а не якоря на одной длинной странице: «Оценки»
    должно открывать оценки, а не прокручивать. Данные собираются ОДИН раз на
    все страницы — иначе шесть страниц означали бы шесть обходов Moodle.

    Главная остаётся `dashboard.html`: на неё смотрят ярлык на рабочем столе,
    обработчик `rubezh://` и открытая вкладка, которая сама перечитывает файл.
    """
    BUILD.mkdir(exist_ok=True)
    data = payload(demo)
    for page, name in PAGES.items():
        _write(BUILD / name, data, demo, page)
    return BUILD / PAGES["home"]


def _lan_ip():
    """Адрес, по которому дашборд откроется с телефона в той же сети."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 80))       # никуда не шлём, нужен только выбранный интерфейс
        return sock.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        sock.close()


class _TokenHandler(SimpleHTTPRequestHandler):
    """Отдаёт файлы только по адресу с секретным префиксом.

    Дашборд — это твоё имя, оценки, посещаемость и список того, что ты
    заваливаешь. В университетской сети порт 8000 находится сканированием за
    секунды, поэтому в режиме --lan адрес обязан быть неугадываемым.
    """
    token = None

    def _strip_token(self):
        if not self.token:
            return True
        prefix = "/" + self.token
        if self.path == prefix:
            self.path = "/"
            return True
        if self.path.startswith(prefix + "/"):
            self.path = self.path[len(prefix):]
            return True
        self.send_error(404)
        return False

    def do_GET(self):
        if self._strip_token():
            super().do_GET()

    def do_HEAD(self):
        if self._strip_token():
            super().do_HEAD()

    def log_message(self, fmt, *args):
        pass          # не сорить в консоль на каждый запрос


def serve(port=8000, lan=False, demo=False):
    """По умолчанию только этот компьютер. Наружу — осознанно, через --lan."""
    out = build(demo)
    token = secrets.token_urlsafe(9) if lan else None
    _TokenHandler.token = token     # атрибут класса: partial создаёт экземпляры сам
    handler = partial(_TokenHandler, directory=str(out.parent))

    host = "0.0.0.0" if lan else "127.0.0.1"
    server = ThreadingHTTPServer((host, port), handler)
    path = f"/{token}/dashboard.html" if token else "/dashboard.html"

    print(f"Дашборд собран: {out}")
    print(f"  на этом компьютере: http://localhost:{port}{path}")
    if lan:
        print(f"  с телефона в этой же сети: http://{_lan_ip()}:{port}{path}")
        print("\n  ВНИМАНИЕ: страница видна всем в этой сети, кто знает адрес,")
        print("  а на ней твои оценки и посещаемость. Адрес одноразовый —")
        print("  при следующем запуске будет другой. Не пересылай его.")
    else:
        print("  снаружи недоступно. Нужен телефон — запусти: "
              f"python rubezh.py serve {port} --lan")
    print("\nCtrl+C — остановить.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nОстановлено.")
    finally:
        server.server_close()
