"""Мастер первого запуска в браузере: python rubezh.py setup

Те же шаги, что в wizard.py, только кнопками на странице, а не вопросами в
консоли: консоль пугает, и половина студентов закрывала её на втором шаге.
Консольный мастер остался — `python rubezh.py setup --cli`, например по SSH.

Страница — templates/setup.html. Её отдаёт http.server только этому
компьютеру (127.0.0.1) на случайном порту и по неугадываемому адресу: кнопки
на ней логинят, пишут токен бота и ставят задачу в планировщик, и любая
другая вкладка браузера не должна суметь нажать их за человека.

Сервер живёт, пока открыта вкладка: она отмечается раз в 20 секунд. Закрыли
вкладку — через несколько секунд процесс выходит сам, чтобы окно консоли
после установщика не висело вечно.
"""
import json
import os
import secrets
import shutil
import subprocess
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import paths
import wizard

PAGE = paths.ASSETS / "templates" / "setup.html"
TASK = ("AITU Rubezh", "kz.aitu.rubezh.watch")   # имя задачи Windows и id launchd
IDLE_SEC = 150       # фоновые вкладки Chrome будят таймеры раз в минуту, берём с запасом
BYE_SEC = 8          # вкладку закрыли или перезагрузили: ждём, не вернётся ли
MAX_BODY = 4096


def _group_dirs():
    return sorted(d for d in (paths.APP / "groups").glob("*") if d.is_dir())


# --- Шаги: (получилось ли, что сказать человеку) ----------------------------

def act_browser(_body):
    if paths.FROZEN:
        return False, "Chromium не найден рядом с программой — установка повреждена, переустанови."
    done = subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"])
    if done.returncode != 0:
        return False, "Не скачалось — проверь интернет и попробуй ещё раз."
    return True, "Chromium скачан."


def act_login(service):
    def act(_body):
        import session
        if session.login(service):
            return True, "Вошёл, сессия сохранена."
        return False, "Вход не подтвердился: окно закрыли или прошло 10 минут. Попробуй ещё раз."
    return act


def act_group(body):
    group = next((g for g in _group_dirs() if g.name == body.get("group")), None)
    if group is None:
        return False, "Такой группы нет в groups/."
    names = []
    for src in group.glob("*.json"):
        shutil.copy2(src, paths.ROOT / src.name)
        names.append(src.name)
    return True, "Положил: " + ", ".join(names)


def act_shortcut(_body):
    import desktop
    return True, f"Ярлык: {desktop.shortcut()}"


def act_start_menu(_body):
    import desktop
    return True, f"Ярлык: {desktop.start_menu_shortcut()}"


def act_protocol(_body):
    import desktop
    desktop.register_protocol()
    return True, "Кнопка «Войти» на дашборде включена."


def act_token(body):
    import notify
    token = str(body.get("token") or "").strip()
    if ":" not in token or len(token) <= 30:
        return False, "Не похоже на токен. Он длинный, с двоеточием посередине."
    notify.save_token(token)
    return True, "Токен сохранён."


def act_telegram(body):
    """Найти, кому писать, и включить проверку раз в час — как шаг консольного мастера."""
    import desktop
    import notify
    notify.load_env()
    if not notify.chat_id() or body.get("chat_id"):
        notify.link(body.get("chat_id") or None)
    desktop.schedule_on(TASK[0], ["watch"], TASK[1])
    desktop.run_now(*TASK)
    return True, "Проверка раз в час включена. Первое сообщение придёт через минуту-другую."


def act_finish(_body):
    import web
    out = web.build()
    webbrowser.open(out.resolve().as_uri())
    return True, "Дашборд собран и открыт."


ACTIONS = {
    "browser": act_browser,
    "lms": act_login("lms"),
    "du": act_login("du"),
    "outlook": act_login("outlook"),
    "group": act_group,
    "shortcut": act_shortcut,
    "start_menu": act_start_menu,
    "protocol": act_protocol,
    "token": act_token,
    "telegram": act_telegram,
    "finish": act_finish,
}


# --- Сервер -----------------------------------------------------------------

class Wizard:
    def __init__(self):
        self.token = secrets.token_urlsafe(16)
        self.busy = threading.Lock()          # один шаг за раз: два окна входа в один профиль не уживаются
        self.done = set()                     # то, что не проверить с диска: ярлык, схема, Outlook
        self.leave_at = time.time() + IDLE_SEC
        self.finished = threading.Event()

    def touch(self, seconds=IDLE_SEC):
        self.leave_at = time.time() + seconds

    def gone(self):
        return not self.busy.locked() and time.time() > self.leave_at

    def state(self):
        import desktop
        import fast
        import notify
        notify.load_env()
        try:
            scheduled = desktop.schedule_status(*TASK) is not None
        except desktop.Unsupported:
            scheduled = False
        groups = _group_dirs()
        have = sorted({f.name for g in groups for f in g.glob("*.json")
                       if (paths.ROOT / f.name).exists()})
        return {
            "browser": any(wizard._browsers_dir().glob("chromium-*")),
            "frozen": paths.FROZEN,
            # Ярлык и схему rubezh:// в установленной версии уже положил установщик.
            "installed": paths.FROZEN and paths.WINDOWS,
            "desktop": paths.WINDOWS or paths.MACOS,
            "windows": paths.WINDOWS,
            "lms": fast.alive("lms"),
            "du": fast.alive("du"),
            "groups": [g.name for g in groups],
            "group_have": have,
            "group_mine": fast.secrets().get("du_group") or "",
            "bot": bool(os.environ.get("BOT_TOKEN")),
            "chat": bool(notify.chat_id()),
            "scheduled": scheduled,
            "done": sorted(self.done),
        }

    def do(self, name, body):
        if name not in ACTIONS:
            return {"ok": False, "message": "Нет такого шага."}
        if not self.busy.acquire(blocking=False):
            return {"ok": False, "message": "Подожди — ещё идёт другой шаг."}
        try:
            try:
                ok, message = ACTIONS[name](body)
            except SystemExit as error:           # notify и session так объясняют, что не так
                ok, message = False, str(error)
            except Exception as error:            # один упавший шаг не должен ронять мастер
                ok, message = False, str(error)[:300] or type(error).__name__
            if ok:
                self.done.add(name)
        finally:
            self.busy.release()
        if ok and name == "finish":
            self.finished.set()
            return {"ok": True, "message": message}
        return {"ok": ok, "message": message, "state": self.state()}


def handler(wiz):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass                                  # консоль — для вывода входа, не для пингов

        def _send(self, code, body, kind="application/json; charset=utf-8"):
            self.send_response(code)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, data):
            self._send(200, json.dumps(data, ensure_ascii=False).encode("utf-8"))

        def _route(self):
            prefix = f"/{wiz.token}/"
            if not self.path.startswith(prefix):
                self._send(404, b"not found", "text/plain")
                return None
            wiz.touch()
            return self.path[len(prefix):]

        def do_GET(self):
            route = self._route()
            if route == "":
                self._send(200, PAGE.read_bytes(), "text/html; charset=utf-8")
            elif route == "api/state":
                self._json(wiz.state())
            elif route is not None:
                self._send(404, b"not found", "text/plain")

        def do_POST(self):
            route = self._route()
            if route is None:
                return
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_BODY:
                self._send(413, b"too large", "text/plain")
                return
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
            except ValueError:
                body = {}
            if route == "api/ping":
                self._json({})
            elif route == "api/bye":
                wiz.touch(BYE_SEC)
                self._json({})
            elif route.startswith("api/do/"):
                self._json(wiz.do(route[len("api/do/"):], body if isinstance(body, dict) else {}))
            else:
                self._send(404, b"not found", "text/plain")

    return Handler


def run():
    wiz = Wizard()
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler(wiz))
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_port}/{wiz.token}/"

    print("=== AITU Rubezh: первый запуск ===")
    print("Мастер открыт в браузере. Если вкладка не появилась, открой этот адрес:")
    print("  " + url)
    print("Окно не закрывай до конца настройки — оно закроется само.")
    webbrowser.open(url)

    while not wiz.finished.wait(2):
        if wiz.gone():
            print("Вкладку мастера закрыли. Продолжить: ярлык «Настройка AITU Rubezh» "
                  "или python rubezh.py setup")
            break
    time.sleep(1)                 # дать ответу на «Готово» уйти до выхода
    server.shutdown()
    return 0
