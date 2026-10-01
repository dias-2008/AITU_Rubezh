"""Дашборд на телефоне: компьютер раздаёт, телефон сканирует QR.

    python rubezh.py phone      поднять раздачу и открыть страницу с QR-кодом
    rubezh://phone              то же — кнопкой «Телефон» на дашборде

Телефону нужен адрес, по которому он достанет до компьютера. Обычная локальная
сеть для этого годится плохо: университетский Wi-Fi, как правило, изолирует
устройства друг от друга, а с мобильного интернета до домашнего компьютера не
достать вовсе. Поэтому основной путь — быстрый туннель Cloudflare
(`cloudflared tunnel --url`): без аккаунта, со случайным адресом
*.trycloudflare.com, который работает откуда угодно. Если туннель не поднялся,
открываем раздачу в локальную сеть — это запасной путь.

Цена туннеля: страница идёт через серверы Cloudflare. Поэтому адрес закрыт
секретом, как и в `serve --lan`, и живёт, только пока работает раздача.

Секретов два. Один — в адресе для телефона: по нему отдаются страницы
дашборда и кнопка «Обновить». Второй — у страницы с QR-кодом и кнопкой «Стоп»
на самом компьютере; она к тому же отвечает только запросам с этого
компьютера и не через туннель. Иначе тот, кто увидел адрес телефона, мог бы
выключить раздачу или достать новый QR.

Раздача живёт, пока её не остановят кнопкой или не выключат компьютер.
Второй запуск не поднимает вторую раздачу, а открывает страницу первой.
"""
import json
import os
import platform
import re
import secrets
import shutil
import subprocess
import sys
import tarfile
import threading
import time
import webbrowser
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.request import urlopen

import paths
import web

PAGE = paths.ASSETS / "templates" / "phone.html"
STATE = paths.ROOT / ".sessions" / "phone.json"
BIN = paths.ROOT / "bin"
TUNNEL_LOG = paths.ROOT / ".sessions" / "cloudflared.log"
TUNNEL_WAIT_SEC = 40
RELEASES = "https://github.com/cloudflare/cloudflared/releases/latest/download/"
URL_RE = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")
# Под pythonw у дочерних консольных программ иначе мигает своё окно.
NO_WINDOW = 0x08000000 if paths.WINDOWS else 0


# --- cloudflared ------------------------------------------------------------

def _asset():
    """Имя файла в релизах cloudflared для этой системы."""
    arch = "arm64" if platform.machine().lower() in ("arm64", "aarch64") else "amd64"
    if paths.WINDOWS:
        return f"cloudflared-windows-{arch}.exe"
    if paths.MACOS:
        return f"cloudflared-darwin-{arch}.tgz"
    return f"cloudflared-linux-{arch}"


def cloudflared(say=lambda _state: None):
    """Путь к cloudflared, при необходимости скачав его в bin/.

    Установщик кладёт cloudflared.exe рядом с rubezh.exe, install.bat и
    install.sh скачивают его заранее — сюда же, вызовом этой функции. Качать
    при первом нажатии «Телефон» остаётся только на самый крайний случай.
    """
    name = "cloudflared.exe" if paths.WINDOWS else "cloudflared"
    for exe in (paths.APP / name, BIN / name):
        if exe.exists():
            return str(exe)
    found = shutil.which("cloudflared")
    if found:
        return found
    exe = BIN / name

    import requests
    say("downloading")
    BIN.mkdir(exist_ok=True)
    name = _asset()
    part = BIN / (name + ".part")
    with requests.get(RELEASES + name, stream=True, timeout=30) as reply:
        reply.raise_for_status()
        with part.open("wb") as handle:
            for chunk in reply.iter_content(1 << 16):
                handle.write(chunk)
    if name.endswith(".tgz"):
        with tarfile.open(part) as archive:
            member = archive.getmember("cloudflared")
            with archive.extractfile(member) as src, exe.open("wb") as dst:
                shutil.copyfileobj(src, dst)
        part.unlink()
    else:
        os.replace(part, exe)
    exe.chmod(0o755)
    return str(exe)


# --- Раздача ----------------------------------------------------------------

class Phone:
    def __init__(self):
        self.token = secrets.token_urlsafe(16)        # адрес телефона
        self.admin = secrets.token_urlsafe(16)        # страница с QR на компьютере
        self.port = None
        self.tunnel = None                            # https://….trycloudflare.com
        self.tunnel_state = "starting"                # downloading, up, failed
        self.tunnel_error = ""
        self.lan = None                               # http://10.….:порт — запасной путь
        self.proc = None                              # cloudflared
        self.build = None                             # пересборка, запущенная с телефона
        self.build_failed = False
        self.stopped = threading.Event()
        self.lock = threading.Lock()

    # Ссылки ----------------------------------------------------------------

    def phone_url(self):
        base = self.tunnel or self.lan
        return f"{base}/{self.token}/dashboard.html" if base else None

    def state(self):
        import segno
        url = self.phone_url()
        page = web.BUILD / web.PAGES["home"]
        return {
            "url": url,
            "via": "tunnel" if self.tunnel else ("lan" if self.lan else None),
            "tunnel": self.tunnel_state,
            "error": self.tunnel_error,
            "qr": segno.make(url, error="m").svg_inline(scale=6, border=2) if url else "",
            "building": self.building(),
            "build_failed": self.build_failed,
            "updated": time.strftime("%d.%m.%Y %H:%M", time.localtime(page.stat().st_mtime))
                       if page.exists() else None,
        }

    # Пересборка ------------------------------------------------------------

    def building(self):
        return self.build is not None and self.build.poll() is None

    def refresh(self):
        """Пересобрать дашборд отдельным процессом — как это делает `rubezh.py build`.

        Отдельный процесс, а не поток: сборка ходит в Moodle и портал и может
        поднять Chromium, и её падение не должно ронять раздачу.
        """
        with self.lock:
            if self.building():
                return False
            self.build_failed = False
            self.build = subprocess.Popen(
                [*paths.python(windowless=True), "build"], cwd=str(paths.ROOT),
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, creationflags=NO_WINDOW)
            return True

    def poll_build(self):
        if self.build is not None and not self.building():
            self.build_failed = self.build.returncode != 0

    # Туннель ---------------------------------------------------------------

    def start_tunnel(self):
        def say(state):
            self.tunnel_state = state
        try:
            exe = cloudflared(say)
            say("starting")
            TUNNEL_LOG.parent.mkdir(exist_ok=True)
            log = TUNNEL_LOG.open("wb")
            # Лог в файл, а не в трубу: трубу пришлось бы вычитывать всё время
            # работы, иначе она переполнится и cloudflared встанет.
            self.proc = subprocess.Popen(
                [exe, "tunnel", "--no-autoupdate", "--url", f"http://127.0.0.1:{self.port}"],
                stdin=subprocess.DEVNULL, stdout=log, stderr=log, creationflags=NO_WINDOW)
            log.close()
            deadline = time.time() + TUNNEL_WAIT_SEC
            while time.time() < deadline and not self.stopped.is_set():
                found = URL_RE.search(TUNNEL_LOG.read_text(encoding="utf-8", errors="replace"))
                if found:
                    self.tunnel = found.group(0)
                    say("up")
                    return
                if self.proc.poll() is not None:
                    raise RuntimeError("cloudflared завершился сразу — см. " + str(TUNNEL_LOG))
                time.sleep(0.5)
            raise RuntimeError(f"туннель не поднялся за {TUNNEL_WAIT_SEC} секунд")
        except Exception as error:
            self.tunnel_error = str(error)[:300] or type(error).__name__
            say("failed")
            self.stop_tunnel()
            if not self.stopped.is_set():
                self.start_lan()

    def stop_tunnel(self):
        if self.proc is not None and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(5)
            except subprocess.TimeoutExpired:
                self.proc.kill()

    def start_lan(self):
        """Запасной путь: тот же сервер, но слушает адрес в локальной сети."""
        ip = web._lan_ip()
        if ip.startswith("127."):
            return
        lan = ThreadingHTTPServer((ip, 0), handler(self))
        lan.daemon_threads = True
        threading.Thread(target=lan.serve_forever, daemon=True).start()
        self.lan = f"http://{ip}:{lan.server_port}"

    def stop(self):
        self.stopped.set()


def handler(phone):
    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(web.BUILD), **kwargs)

        def log_message(self, *_args):
            pass

        def end_headers(self):
            self.send_header("Cache-Control", "no-store")
            # Адрес с секретом не должен уходить в Referer шрифтам Google.
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("X-Robots-Tag", "noindex")
            super().end_headers()

        def _send(self, code, body, kind="application/json; charset=utf-8"):
            self.send_response(code)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _json(self, data):
            self._send(200, json.dumps(data, ensure_ascii=False).encode("utf-8"))

        def _local(self):
            """Запрос с этого компьютера напрямую. Туннель тоже приходит с
            127.0.0.1, но cloudflared добавляет свой заголовок."""
            return self.client_address[0] == "127.0.0.1" and "Cf-Connecting-Ip" not in self.headers

        def _route(self):
            """('phone' | 'admin', остаток пути) или None, если секрет не тот."""
            path = self.path.split("?", 1)[0]
            for kind, token in (("phone", phone.token), ("admin", phone.admin)):
                prefix = f"/{token}/"
                if path.startswith(prefix):
                    if kind == "admin" and not self._local():
                        break
                    return kind, path[len(prefix):]
            self._send(404, b"not found", "text/plain")
            return None

        def do_GET(self):
            route = self._route()
            if route is None:
                return
            kind, rest = route
            phone.poll_build()
            if kind == "admin":
                if rest == "":
                    self._send(200, PAGE.read_bytes(), "text/html; charset=utf-8")
                elif rest == "api/state":
                    self._json(phone.state())
                else:
                    self._send(404, b"not found", "text/plain")
                return
            if rest == "api/state":
                state = phone.state()
                self._json({k: state[k] for k in ("building", "build_failed", "updated")})
            elif rest in web.PAGES.values():
                # Только страницы дашборда: никаких списков папки и временных файлов.
                self.path = "/" + rest
                super().do_GET()
            else:
                self._send(404, b"not found", "text/plain")

        def do_POST(self):
            route = self._route()
            if route is None:
                return
            kind, rest = route
            if rest == "api/refresh":
                self._json({"started": phone.refresh()})
            elif kind == "admin" and rest == "api/stop":
                self._json({})
                phone.stop()
            else:
                self._send(404, b"not found", "text/plain")

    return Handler


# --- Запуск -----------------------------------------------------------------

def _running():
    """Адрес страницы уже работающей раздачи или None."""
    try:
        state = json.loads(STATE.read_text(encoding="utf-8"))
        url = f"http://127.0.0.1:{state['port']}/{state['admin']}/"
        with urlopen(url + "api/state", timeout=2) as reply:
            if reply.status == 200:
                return url
    except (OSError, ValueError, KeyError):
        pass
    return None


def run():
    existing = _running()
    if existing:
        print("Раздача уже работает:", existing)
        webbrowser.open(existing)
        return 0

    phone = Phone()
    if not (web.BUILD / web.PAGES["home"]).exists():
        phone.refresh()                       # дашборд ни разу не собирали

    server = ThreadingHTTPServer(("127.0.0.1", 0), handler(phone))
    server.daemon_threads = True
    phone.port = server.server_port
    threading.Thread(target=server.serve_forever, daemon=True).start()
    threading.Thread(target=phone.start_tunnel, daemon=True).start()

    STATE.parent.mkdir(exist_ok=True)
    STATE.write_text(json.dumps({"port": phone.port, "admin": phone.admin}), encoding="utf-8")
    page = f"http://127.0.0.1:{phone.port}/{phone.admin}/"
    print("Раздача для телефона запущена. QR-код:", page)
    print("Ctrl+C или кнопка «Остановить» на странице — выключить.")
    webbrowser.open(page)

    try:
        while not phone.stopped.wait(1):
            pass
    except KeyboardInterrupt:
        pass
    finally:
        phone.stop()
        phone.stop_tunnel()
        server.shutdown()
        server.server_close()
        STATE.unlink(missing_ok=True)
        print("Остановлено.")
    return 0
