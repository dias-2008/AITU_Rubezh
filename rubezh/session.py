"""Долгоживущие сессии браузера: логинишься один раз, дальше инструмент работает сам.

Почему так, а не по API. Moodle AITU отдаёт 403 на /login/token.php и на
/webservice/rest/server.php — их закрыли на nginx, токен получить негде.
У Microsoft регистрация стороннего приложения может упереться в согласие
админа тенанта. Поэтому ходим тем же путём, что и браузер студента:
настоящий профиль Chromium с сохранёнными куками.

    python rubezh.py login lms       # откроется окно, логинишься руками
    python rubezh.py login outlook

Две вещи, без которых «один раз» не работает:

1. `MoodleSession` — сессионная кука, она умирает вместе с окном браузера и в
   профиле не остаётся. Поэтому после входа сохраняем куки отдельным файлом
   state.json и подкладываем их обратно при следующем запуске.

2. Рано или поздно эта кука всё равно протухнет. Но кука Microsoft
   (`ESTSAUTHPERSISTENT`, живёт месяцами, если нажать «Stay signed in») —
   нет. Так что Moodle можно молча переподнять: сходить на /auth/oidc/,
   Microsoft узнает нас и вернёт назад уже залогиненными. Человека не дёргаем.
"""
import json
import sys
import time
from contextlib import contextmanager
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).parent
PROFILES = ROOT / ".sessions"

# Ждём столько, пока студент логинится руками (SSO, 2FA, подтверждение на телефоне).
LOGIN_TIMEOUT_SEC = 600

LMS = "https://lms.astanait.edu.kz"

# Microsoft переезжает с outlook.office.com на outlook.cloud.microsoft и редиректит
# туда сам. Проверять надо оба хоста, иначе рабочая сессия выглядит как незалогиненная.
OUTLOOK_HOSTS = ("outlook.office.com", "outlook.cloud.microsoft")
NL = chr(10)

# Экраны Microsoft, которые нельзя проскочить молча: пока человек их не пройдёт,
# автоматический вход будет упираться в них каждый раз.
MS_INTERRUPTS = [
    ("keep your account secure", "Microsoft просит настроить второй способ входа (MFA)"),
    ("защитить вашу учётную запись", "Microsoft просит настроить второй способ входа (MFA)"),
    ("update your password", "Microsoft требует сменить пароль"),
    ("action required", "Microsoft требует действия от тебя"),
    ("permissions requested", "нужно подтвердить разрешения"),
]


class NeedsHuman(RuntimeError):
    """Молча войти нельзя: Microsoft хочет живого человека."""


def _ms_interrupt(page):
    """Если застряли на экране Microsoft — объяснить, на каком именно."""
    if "login.microsoftonline.com" not in page.url:
        return None
    try:
        text = page.inner_text("body").lower()
    except Exception:
        return None
    for needle, reason in MS_INTERRUPTS:
        if needle in text:
            return reason
    return "Microsoft не пустил без подтверждения"


def _lms_ready(page):
    """M.cfg.userId нулевой у гостя — значит нас развернуло на страницу входа."""
    return bool(page.evaluate(
        "() => !!(window.M && M.cfg && M.cfg.userId && M.cfg.userId > 0)"
    ))


def _lms_revive(page):
    """Молча переподнять сессию Moodle через живую куку Microsoft."""
    page.goto(f"{LMS}/auth/oidc/?source=loginpage", wait_until="domcontentloaded", timeout=90000)
    page.wait_for_load_state("networkidle", timeout=60000)
    reason = _ms_interrupt(page)
    if reason:
        raise NeedsHuman(reason)
    page.goto(f"{LMS}/my/", wait_until="domcontentloaded", timeout=60000)
    return _lms_ready(page)


def _outlook_ready(page):
    """Внутри мы или нет.

    По одному адресу судить нельзя: OWA сначала отдаёт страницу на
    outlook.office.com и только потом уводит на вход. Проверка «мы не на
    login.microsoftonline.com» срабатывает в эту щель и врёт. Поэтому ждём
    настоящий признак: куку X-OWA-CANARY (её ставит уже авторизованный OWA)
    либо отрисованный список писем.
    """
    if not any(host in page.url for host in OUTLOOK_HOSTS):
        return False
    try:
        if any("CANARY" in cookie["name"].upper() for cookie in page.context.cookies()):
            return True
    except Exception:
        pass
    try:
        return page.locator("div[role='option']").count() > 0
    except Exception:
        return False


def _outlook_revive(page):
    page.goto("https://outlook.office.com/mail/", wait_until="domcontentloaded", timeout=90000)  # редиректит на cloud.microsoft
    for _ in range(20):          # OWA дорисовывается заметно дольше, чем грузится
        if _outlook_ready(page):
            return True
        time.sleep(3)
    reason = _ms_interrupt(page)
    if reason:
        raise NeedsHuman(reason)
    return _outlook_ready(page)


def _du_ready(page):
    """Портал — JS-приложение, судить можно только по отрисованному.

    Признак «не вошли» — кнопка входа корпоративной почтой на странице.
    Как её нет и мы всё ещё на портале, значит внутри.
    """
    if "du.astanait.edu.kz" not in page.url:
        return False
    try:
        text = page.inner_text("body").lower()
    except Exception:
        return False
    # Пока страница грузится, тела почти нет — и «кнопки входа не видно» тоже.
    # На этом уже обжигались: пустая страница выглядела как успешный вход.
    return len(text.strip()) > 200 and "sign in with corporate" not in text


def _du_revive(page):
    page.goto("https://du.astanait.edu.kz/", wait_until="domcontentloaded", timeout=90000)
    for _ in range(10):
        if _du_ready(page):
            return True
        time.sleep(3)
    return False


SERVICES = {
    "lms": {
        "title": "Moodle AITU",
        "login_url": f"{LMS}/login/index.php",
        "ready_url": f"{LMS}/my/",
        "ready": _lms_ready,
        "revive": _lms_revive,
        "hint": "Жми «Log in with OpenID Connect» и заходи университетским аккаунтом.",
    },
    "outlook": {
        "title": "Outlook",
        "login_url": "https://outlook.office.com/mail/",
        "ready_url": "https://outlook.office.com/mail/",
        "ready": _outlook_ready,
        "revive": _outlook_revive,
        "hint": "Заходи тем же университетским аккаунтом.",
    },
    "du": {
        "title": "Портал AITU",
        "login_url": "https://du.astanait.edu.kz/",
        "ready_url": "https://du.astanait.edu.kz/",
        "ready": _du_ready,
        "revive": _du_revive,
        "hint": "Жми «Sign in with corporate E-mail» — аккаунт тот же.",
    },
}


def profile_dir(service):
    return PROFILES / service


def _state_file(service):
    return profile_dir(service) / "state.json"


def _save_cookies(service, context):
    """Сессионные куки в профиле не остаются — сохраняем их руками.

    Только куки, и намеренно. `context.storage_state()` кроме кук собирает ещё и
    localStorage, а для этого Playwright открывает по временной странице на каждый
    origin. Если дёргать это в цикле раз в две секунды, окна начинают мигать и
    залогиниться физически невозможно. `context.cookies()` не открывает ничего,
    а больше нам ничего и не нужно — восстанавливаем мы тоже только куки.
    """
    _state_file(service).write_text(
        json.dumps({"cookies": context.cookies()}), encoding="utf-8"
    )


def _restore_cookies(service, context):
    path = _state_file(service)
    if not path.exists():
        return
    try:
        cookies = json.loads(path.read_text(encoding="utf-8")).get("cookies", [])
    except (ValueError, OSError):
        return
    if cookies:
        context.add_cookies(cookies)


# Что не нужно фоновой проверке: она читает данные, а не смотрит на страницу.
LEAN_ARGS = [
    "--disable-gpu",
    "--disable-extensions",
    "--disable-background-networking",
    "--disable-background-timer-throttling",
    "--disable-client-side-phishing-detection",
    "--disable-component-update",
    "--disable-default-apps",
    "--disable-sync",
    "--no-first-run",
    "--mute-audio",
    "--renderer-process-limit=2",
]
LEAN_BLOCKED = {"image", "media", "font"}


@contextmanager
def browser(service, headless=True, lean=False):
    """Профиль сервиса как контекст Playwright, с подложенными куками.

    lean=True — режим фоновой проверки: не грузим картинки, шрифты и медиа и
    просим Chromium не заниматься ничем лишним. Данные от этого не меняются,
    а трафика и памяти уходит заметно меньше.
    """
    if service not in SERVICES:
        raise SystemExit(f"Неизвестный сервис {service!r}. Есть: {', '.join(SERVICES)}")
    path = profile_dir(service)
    path.mkdir(parents=True, exist_ok=True)
    args = ["--disable-blink-features=AutomationControlled"]
    if lean:
        args += LEAN_ARGS
    with sync_playwright() as pw:
        context = pw.chromium.launch_persistent_context(
            str(path),
            headless=headless,
            viewport={"width": 1280, "height": 900},
            args=args,
        )
        if lean:
            context.route(
                "**/*",
                lambda route: route.abort()
                if route.request.resource_type in LEAN_BLOCKED else route.continue_(),
            )
        _restore_cookies(service, context)
        try:
            yield context
        finally:
            context.close()


@contextmanager
def connect(service):
    """Контекст, про который уже известно, что он залогинен.

    Если сессия протухла — пробуем поднять её молча. Если и это не вышло,
    значит без человека никак: просим сходить в login.
    """
    spec = SERVICES[service]
    with browser(service, headless=True) as context:
        page = context.new_page()
        page.goto(spec["ready_url"], wait_until="domcontentloaded", timeout=60000)

        if not spec["ready"](page):
            print(f"Сессия {spec['title']} протухла, поднимаю заново...", file=sys.stderr)
            try:
                revived = spec["revive"](page)
            except NeedsHuman as error:
                raise SystemExit(
                    f"{error}." + NL
                    + f"Пройди это один раз руками: python rubezh.py login {service}" + NL
                    + "После настройки MFA инструмент снова сможет входить сам."
                )
            if not revived:
                raise SystemExit(
                    f"Не смог войти сам. Запусти: python rubezh.py login {service}"
                )
            _save_cookies(service, context)
            print("Готово, вошёл без тебя.", file=sys.stderr)

        yield context, page


def is_logged_in(service):
    """Жива ли сессия — с попыткой молча переподнять её."""
    if not _state_file(service).exists():
        return False
    spec = SERVICES[service]
    try:
        with browser(service, headless=True) as context:
            page = context.new_page()
            page.goto(spec["ready_url"], wait_until="domcontentloaded", timeout=60000)
            if spec["ready"](page):
                return True
            if spec["revive"](page):
                _save_cookies(service, context)
                return True
            return False
    except NeedsHuman:
        return False
    except Exception:
        return False


def login(service):
    """Открывает настоящее окно и ждёт, пока человек залогинится."""
    spec = SERVICES[service]
    print(f"Открываю {spec['title']}. {spec['hint']}")
    print("Окно закроется само, как только увижу, что ты вошёл.")
    print("На экране «Stay signed in?» жми Yes — тогда входить придётся сильно реже.\n")

    with browser(service, headless=False) as context:
        page = context.pages[0] if context.pages else context.new_page()
        page.goto(spec["login_url"], wait_until="domcontentloaded", timeout=120000)

        deadline = time.time() + LOGIN_TIMEOUT_SEC
        while time.time() < deadline:
            if page.is_closed():
                break
            # Снимок кук на каждом круге. Сессионные куки умирают вместе с окном,
            # поэтому нельзя ждать «идеального момента» — сохраняем по дороге.
            try:
                _save_cookies(service, context)
            except Exception:
                pass
            try:
                if spec["ready"](page):
                    _save_cookies(service, context)
                    print(f"Готово, сессия {spec['title']} сохранена.")
                    return True
            except Exception:
                pass  # страница в этот момент могла редиректиться — не мешаем
            time.sleep(2)

    # Окно закрылось или вышло время. Верить своей же проверке «на лету» нельзя:
    # она видит страницу в момент загрузки и врёт. Проверяем начисто, без окна.
    print("Проверяю, что вход действительно сработал...", file=sys.stderr)
    if is_logged_in(service):
        print(f"Готово, сессия {spec['title']} сохранена в {profile_dir(service)}")
        return True
    print(f"Вход не подтвердился. Попробуй ещё раз: python rubezh.py login {service}",
          file=sys.stderr)
    return False
