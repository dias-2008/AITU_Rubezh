"""Мастер настройки дайджеста в браузере: python digest.py setup

Те же шаги, что в setup.py, только кнопками на странице: консольный мастер
спрашивал api_id, номер телефона и номера групп вопросами в терминале, и
студенту, который терминал видит впервые, это было не по силам. Консольный
остался — `python digest.py setup --cli`.

Как и мастер rubezh, страницу отдаёт http.server только этому компьютеру
(127.0.0.1), на случайном порту и по неугадываемому адресу: на странице
вводятся ключи Telegram и код входа в аккаунт. Закрыли вкладку — процесс
через несколько секунд выходит сам.

Вход в Telegram идёт через Telethon, а он асинхронный и держит соединение
между шагами «прислать код» → «ввести код» → «пароль 2FA». Поэтому клиент
живёт в своём цикле событий в отдельном потоке, а обработчики запросов
отдают ему корутины и ждут ответа.
"""
import asyncio
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

import requests

import llm
import setup

PAGE = llm.ASSETS / "setup.html"
IDLE_SEC = 150
BYE_SEC = 8
MAX_BODY = 4096
NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0
SAMPLE = "\n".join([
    "[ссылка1] Дана: Завтра матанализа не будет, перенос на пятницу 14:00",
    "[ссылка2] Айгерим: спасибо",
    "[ссылка3] Дана: СРС по физике сдать до 5 сентября",
])


class Telegram:
    """Telethon-клиент в своём цикле событий: шаги входа идут разными запросами."""

    def __init__(self, directory):
        self.directory = directory
        self.loop = asyncio.new_event_loop()
        threading.Thread(target=self.loop.run_forever, daemon=True).start()
        self.client = None
        self.phone = None
        self.code_hash = None

    def run(self, coro, timeout=90):
        return asyncio.run_coroutine_threadsafe(coro, self.loop).result(timeout)

    async def _client(self):
        if self.client is None:
            from telethon import TelegramClient
            self.client = TelegramClient(str(self.directory / "telegram"),
                                         int(os.environ["TG_API_ID"]), os.environ["TG_API_HASH"])
        if not self.client.is_connected():
            await self.client.connect()
        return self.client

    async def authorized(self):
        return await (await self._client()).is_user_authorized()

    async def send_code(self, phone):
        client = await self._client()
        sent = await client.send_code_request(phone)
        self.phone, self.code_hash = phone, sent.phone_code_hash

    async def sign_in(self, code=None, password=None):
        """True — вошли, 'password' — нужен пароль 2FA."""
        from telethon.errors import SessionPasswordNeededError
        client = await self._client()
        if password is not None:
            await client.sign_in(password=password)
            return True
        try:
            await client.sign_in(self.phone, code, phone_code_hash=self.code_hash)
        except SessionPasswordNeededError:
            return "password"
        return True

    async def chats(self):
        import digest
        return await digest.cmd_chats(await self._client())

    async def close(self):
        if self.client is not None:
            await self.client.disconnect()


class Wizard:
    def __init__(self, profile):
        self.profile = profile
        self.directory = llm.ROOT / "profiles" / profile
        self.directory.mkdir(parents=True, exist_ok=True)
        self.config_path = self.directory / "config.json"
        llm.set_usage_log(self.directory / "usage.log")
        self.token = secrets.token_urlsafe(16)
        self.busy = threading.Lock()
        self.leave_at = time.time() + IDLE_SEC
        self.tg = Telegram(self.directory)
        self.logged_in = None             # спрашиваем Telegram один раз, дальше помним
        self.need_password = False
        self.found = []                   # группы с последнего «Показать группы»
        self.done = set()

    def touch(self, seconds=IDLE_SEC):
        self.leave_at = time.time() + seconds

    def gone(self):
        return not self.busy.locked() and time.time() > self.leave_at

    # --- Состояние --------------------------------------------------------

    def keys(self):
        return bool(os.environ.get("TG_API_ID") and os.environ.get("TG_API_HASH"))

    def state(self):
        llm.load_env(required=False)
        if not os.environ.get("BOT_TOKEN") and setup._rubezh_token():
            setup._save_env("BOT_TOKEN", setup._rubezh_token())   # тот же бот, что у rubezh
        if self.logged_in is None and self.keys():
            try:
                self.logged_in = self.tg.run(self.tg.authorized(), timeout=30)
            except Exception:
                self.logged_in = None     # сети нет — спросим в следующий раз
        config = setup.load_config(self.config_path)
        providers = {name: bool(llm.PROVIDERS[name][0]()) for name in ("claude-cli", "gemini-api", "ollama")}
        return {
            "keys": self.keys(),
            "bot": bool(os.environ.get("BOT_TOKEN")),
            "bot_name": self.bot_name(),
            "shared_bot": bool(setup._rubezh_token()),
            "backend": config.get("backend", "auto"),
            "providers": providers,
            "gemini_key": bool(os.environ.get("GEMINI_API_KEY")),
            "logged_in": bool(self.logged_in),
            "need_password": self.need_password,
            "code_sent": bool(self.tg.code_hash),
            "chats": [c["title"] for c in config.get("chats", [])],
            "scheduled": self.scheduled(),
            "schedulable": setup.desktop is not None,
            "done": sorted(self.done),
        }

    def bot_name(self):
        token = os.environ.get("BOT_TOKEN")
        if not token:
            return ""
        if getattr(self, "_bot", (None, ""))[0] == token:
            return self._bot[1]
        try:
            reply = requests.get(f"https://api.telegram.org/bot{token}/getMe", timeout=15)
            name = reply.json().get("result", {}).get("username", "") if reply.ok else ""
        except requests.RequestException:
            return ""
        self._bot = (token, name)
        return name

    def scheduled(self):
        if setup.desktop is None:
            return False
        try:
            return setup.desktop.schedule_status(*self.task()) is not None
        except setup.desktop.Unsupported:
            return False

    def task(self):
        if self.profile == "default":
            return setup.TASK_NAME, setup.TASK_LABEL
        return f"{setup.TASK_NAME} ({self.profile})", f"{setup.TASK_LABEL}.{self.profile}"

    # --- Шаги: (получилось ли, что сказать) ---------------------------------

    def act_keys(self, body):
        api_id = str(body.get("api_id") or "").strip()
        api_hash = str(body.get("api_hash") or "").strip()
        if not api_id.isdigit():
            return False, "api_id — это число, например 1234567."
        if len(api_hash) < 20 or not api_hash.isalnum():
            return False, "api_hash — длинная строка из букв и цифр, скопируй её целиком."
        setup._save_env("TG_API_ID", api_id)
        setup._save_env("TG_API_HASH", api_hash)
        self.tg = Telegram(self.directory)    # новые ключи — новый клиент
        self.logged_in = None
        return True, "Ключи сохранены."

    def act_bot(self, body):
        token = str(body.get("token") or "").strip()
        if ":" not in token or len(token) <= 30:
            return False, "Не похоже на токен. Он длинный, с двоеточием посередине."
        try:
            reply = requests.get(f"https://api.telegram.org/bot{token}/getMe", timeout=20)
        except requests.RequestException:
            return False, "Не достучался до Telegram — проверь интернет."
        if not reply.ok:
            return False, "Telegram не знает такой токен. Скопируй его из @BotFather целиком."
        setup._save_env("BOT_TOKEN", token)
        return True, f"Токен подошёл — это бот @{reply.json()['result'].get('username', '')}."

    def act_backend(self, body):
        backend = str(body.get("backend") or "")
        if backend not in ("auto", "claude-cli", "gemini-api", "ollama"):
            return False, "Нет такого варианта."
        config = setup.load_config(self.config_path)
        key = str(body.get("gemini_key") or "").strip()
        if backend == "gemini-api" and key:
            setup._save_env("GEMINI_API_KEY", key)
        if backend == "ollama":
            model = _ollama_pick()
            if model:
                config["ollama_model"] = model
        config["backend"] = backend
        setup.save_config(self.config_path, config)
        try:
            out = llm.summarize("Выпиши важное для студента, одна строка на пункт.", SAMPLE, config)
        except (Exception, SystemExit) as error:
            return False, f"Сохранил, но пробный вызов не прошёл: {str(error)[:250]}"
        return True, "Работает. Пробная выжимка:\n" + (out or "").strip()[:400]

    def act_code(self, body):
        phone = "".join(ch for ch in str(body.get("phone") or "") if ch.isdigit() or ch == "+")
        if len(phone) < 10:
            return False, "Номер целиком, с кодом страны: +7 701 …"
        if not phone.startswith("+"):
            phone = "+" + phone
        try:
            self.tg.run(self.tg.send_code(phone))
        except Exception as error:
            return False, f"Telegram не прислал код: {str(error)[:200]}"
        self.need_password = False
        return True, "Код пришёл в Telegram — в чат «Telegram» на телефоне или компьютере."

    def act_signin(self, body):
        from telethon import errors
        code = "".join(ch for ch in str(body.get("code") or "") if ch.isdigit())
        password = body.get("password")
        try:
            if password is not None:
                result = self.tg.run(self.tg.sign_in(password=str(password)))
            else:
                result = self.tg.run(self.tg.sign_in(code=code))
        except errors.PhoneCodeInvalidError:
            return False, "Код не подошёл. Проверь цифры."
        except errors.PhoneCodeExpiredError:
            self.tg.code_hash = None
            return False, "Код устарел — запроси новый."
        except errors.PasswordHashInvalidError:
            return False, "Пароль не подошёл."
        except Exception as error:
            return False, f"Не вошёл: {str(error)[:200]}"
        if result == "password":
            self.need_password = True
            return True, "У аккаунта включён облачный пароль (2FA) — введи его."
        self.need_password = False
        self.logged_in = True
        return True, "Вошёл. Сессия сохранена на этом компьютере."

    def act_list(self, _body):
        self.found = self.tg.run(self.tg.chats(), timeout=180)
        return True, f"Групп и тем: {len(self.found)}."

    def act_chats(self, body):
        picked = body.get("picked") or []
        if not isinstance(picked, list):
            return False, "Не понял выбор."
        chosen = [self.found[i] for i in picked if isinstance(i, int) and 0 <= i < len(self.found)]
        if not chosen:
            return False, "Отметь хотя бы одну группу или тему."
        config = setup.load_config(self.config_path)
        config["chats"] = chosen
        setup.save_config(self.config_path, config)
        return True, "Выбрано: " + ", ".join(c["title"] for c in chosen)

    def act_schedule(self, body):
        name, label = self.task()
        if body.get("off"):
            setup.desktop.schedule_off(name, label)
            return True, "Выключено."
        setup.desktop.schedule_on(name, ["run", "--profile", self.profile], label, tool="digest")
        return True, "Дайджест будет собираться сам раз в час."

    def act_now(self, _body):
        """Собрать дайджест сейчас — отдельным процессом, как это делает планировщик."""
        import paths
        subprocess.Popen([*paths.python(windowless=True, tool="digest"), "run", "--profile", self.profile],
                         cwd=str(llm.ROOT), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, creationflags=NO_WINDOW)
        return True, ("Собираю. Дайджест придёт от бота через минуту-другую. Не пришёл — открой бота "
                      "и нажми Start: без этого он не может тебе писать.")

    ACTIONS = {"keys": act_keys, "bot": act_bot, "backend": act_backend, "code": act_code,
               "signin": act_signin, "list": act_list, "chats": act_chats,
               "schedule": act_schedule, "now": act_now}

    def do(self, name, body):
        if name not in self.ACTIONS:
            return {"ok": False, "message": "Нет такого шага."}
        if not self.busy.acquire(blocking=False):
            return {"ok": False, "message": "Подожди — ещё идёт другой шаг."}
        try:
            try:
                ok, message = self.ACTIONS[name](self, body)
            except SystemExit as error:
                ok, message = False, str(error)
            except Exception as error:
                ok, message = False, str(error)[:300] or type(error).__name__
            if ok:
                self.done.add(name)
        finally:
            self.busy.release()
        reply = {"ok": ok, "message": message, "state": self.state()}
        if name == "list" and ok:
            chosen = {(c["id"], c.get("topic_id")) for c in setup.load_config(self.config_path).get("chats", [])}
            reply["found"] = [dict(c, picked=(c["id"], c.get("topic_id")) in chosen) for c in self.found]
        return reply


def _ollama_pick():
    """Модель по железу — тот же machine.py, что советует модель для почты в rubezh."""
    try:
        import machine
    except ImportError:
        return None
    return machine.ready() or machine.model()


def _ollama_advice():
    try:
        import machine
    except ImportError:
        return None
    return machine.advice()


def handler(wiz):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

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
            elif route == "api/llm":
                self._json(_ollama_advice() or {})       # по запросу: будит Ollama
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


def run(profile="default"):
    llm.load_env(required=False)
    wiz = Wizard(profile)
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler(wiz))
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_port}/{wiz.token}/"

    print("=== tg-digest: настройка ===")
    print("Мастер открыт в браузере. Если вкладка не появилась, открой этот адрес:")
    print("  " + url)
    print("Это окно закроется само, когда закроешь вкладку.")
    webbrowser.open(url)
    try:
        while not wiz.gone():
            time.sleep(2)
    except KeyboardInterrupt:
        pass
    try:
        wiz.tg.run(wiz.tg.close(), timeout=10)
    except Exception:
        pass
    server.shutdown()
    return 0
