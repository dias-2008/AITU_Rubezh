"""Сборка и раздача дашборда.

Дашборд — один самодостаточный HTML-файл: данные вшиваются в него при сборке,
дальше он работает без сервера. Открывается двойным кликом с диска.

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
import secrets
import socket
from datetime import datetime
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import grade

ROOT = Path(__file__).parent
TEMPLATE = ROOT / "templates" / "dashboard.html"
BUILD = ROOT / "build"
PLACEHOLDER = "__RUBEZH_DATA__"


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


def fragment(data=None, demo=False):
    """Дашборд без обвязки <html>: title, стили, разметка, скрипт."""
    data = payload(demo) if data is None else data
    body = TEMPLATE.read_text(encoding="utf-8")
    if PLACEHOLDER not in body:
        raise SystemExit(f"В шаблоне нет {PLACEHOLDER} — некуда вставить данные.")
    # </script> внутри строки JSON закрыл бы наш же тег раньше времени.
    blob = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    return body.replace(PLACEHOLDER, blob)


DOCTYPE = """<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
"""


def build(demo=False):
    """Готовый файл, который открывается с диска без всякого сервера.

    Шаблон — фрагмент без <html>: сначала title и стили, потом разметка.
    Режем его по первому <div class="shell"> — всё до него уезжает в <head>.
    """
    BUILD.mkdir(exist_ok=True)
    out = BUILD / "dashboard.html"
    marker = '<div class="shell">'
    head, found, rest = fragment(demo=demo).partition(marker)
    if not found:
        raise SystemExit(f"В шаблоне нет {marker} — не понять, где кончается head.")
    out.write_text(
        DOCTYPE + head + "</head>\n<body>\n" + marker + rest + "\n</body>\n</html>\n",
        encoding="utf-8",
    )
    return out


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
