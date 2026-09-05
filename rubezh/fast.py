"""Быстрый путь: те же данные, но без запуска браузера.

Замеры показали, что почти вся цена фоновой проверки — это старт Chromium:
~360 МБ и ~18 секунд, и отключение картинок ничего не меняет. Зато обоим
источникам браузер по сути не нужен, нужны только ключи:

    Moodle — кука MoodleSession и sesskey, дальше обычный POST на
             /lib/ajax/service.php;
    портал — JWT, дальше обычный GET с заголовком Authorization.

Ключи мы уже добываем браузером при логине и складываем в secrets.json. Пока
они живы, ежечасная проверка стоит пары мегабайт и секунды. Как протухли —
watch поднимает браузер, обновляет ключи и снова уходит на быстрый путь.

ВАЖНО: User-Agent обязателен. Перед lms.astanait.edu.kz стоит WAF, и запрос с
дефолтным python-requests получает 403 Access Blocked.
"""
import json
from pathlib import Path

import requests

ROOT = Path(__file__).parent
SECRETS = ROOT / "secrets.json"
SESSIONS = ROOT / ".sessions"

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36")
LMS = "https://lms.astanait.edu.kz"
DU_API = "https://du.astanait.edu.kz:8765"
TIMEOUT = 30


class Stale(RuntimeError):
    """Ключи не подошли — нужен браузер."""


def _read(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def secrets():
    return _read(SECRETS)


def remember(**values):
    data = secrets()
    data.update(values)
    SECRETS.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def _cookies(service, host_part):
    jar = {}
    for cookie in _read(SESSIONS / service / "state.json").get("cookies", []):
        if host_part in (cookie.get("domain") or ""):
            jar[cookie["name"]] = cookie["value"]
    return jar


# --- Moodle ----------------------------------------------------------------

def moodle_call(method, args):
    sesskey = secrets().get("moodle_sesskey")
    jar = _cookies("lms", "astanait")
    if not sesskey or "MoodleSession" not in jar:
        raise Stale("нет sesskey или куки MoodleSession")

    response = requests.post(
        f"{LMS}/lib/ajax/service.php",
        params={"sesskey": sesskey, "info": method},
        json=[{"index": 0, "methodname": method, "args": args}],
        headers={"User-Agent": UA, "Content-Type": "application/json"},
        cookies=jar, timeout=TIMEOUT,
    )
    if response.status_code == 403:
        raise Stale("WAF ответил 403")
    if not response.ok:
        raise Stale(f"HTTP {response.status_code}")
    try:
        payload = response.json()
    except ValueError:
        raise Stale("вместо JSON пришла страница — скорее всего разлогинило")
    if not isinstance(payload, list) or not payload:
        raise Stale("неожиданный ответ")
    item = payload[0]
    if item.get("error"):
        message = ((item.get("exception") or {}).get("errorcode") or "")
        # Протухший ключ отличаем от честной ошибки вызова: во втором случае
        # браузер не поможет, и поднимать его незачем.
        if message in ("invalidsesskey", "servicerequireslogin", "requireloginerror"):
            raise Stale("sesskey протух")
        raise RuntimeError((item.get("exception") or {}).get("message") or "ошибка вызова")
    return item.get("data")


def moodle_snapshot(limit=50):
    """Курсы и дедлайны по HTTP. Бросает Stale, если нужен браузер."""
    courses = moodle_call(
        "core_course_get_enrolled_courses_by_timeline_classification",
        {"offset": 0, "limit": 50, "classification": "all", "sort": "fullname"},
    ) or {}
    events = moodle_call(
        "core_calendar_get_action_events_by_timesort",
        {"limitnum": limit, "limittononsuspendedevents": True},
    ) or {}
    return (
        {str(c["id"]): c.get("fullname") or c.get("shortname") or str(c["id"])
         for c in courses.get("courses", [])},
        events.get("events", []),
    )


# --- Портал ----------------------------------------------------------------

def du_get(path):
    token = secrets().get("du_token")
    if not token:
        raise Stale("нет JWT портала")
    response = requests.get(
        DU_API + path,
        headers={"Authorization": f"Bearer {token}", "User-Agent": UA},
        timeout=TIMEOUT,
    )
    # Шлюз портала на неавторизованный запрос отвечает 404, а не 401.
    if response.status_code in (401, 403, 404):
        raise Stale(f"портал ответил {response.status_code} — токен протух")
    if not response.ok:
        raise RuntimeError(f"HTTP {response.status_code}")
    try:
        return response.json()
    except ValueError:
        raise Stale("вместо JSON пришла страница")


def alive(service):
    """Годятся ли ключи прямо сейчас — по HTTP, без Chromium.

    Нужно там, где решается, показывать человеку окно входа или нет. Раньше это
    решалось через `session.is_logged_in`, а он поднимает браузер на каждый
    сервис: до появления окна проходила минута, в которую с дашборда не видно
    ровно ничего. Тот же вопрос и тот же ответ здесь стоят секунду.
    """
    try:
        if service == "lms":
            moodle_call("core_calendar_get_action_events_by_timesort", {"limitnum": 1})
        elif service == "du":
            du_get("/astanait-student-module/api/v1/student/profile/principal")
        else:
            return False
        return True
    except Stale:
        return False
    except Exception:
        # Сеть или сам сервер — вход тут не поможет, окно открывать незачем.
        return True


def du_snapshot(group):
    if not group:
        raise Stale("группа неизвестна")
    body = du_get(f"/astanait-schedule-module/api/v1/schedule/groupName/{group}")
    body = body.get("body") if isinstance(body, dict) else body
    lessons = len(body) if isinstance(body, list) else 0
    try:
        du_get("/astanait-office-module/api/v1/academic-department"
               "/assessment-report/summary-sheet-by-for-transcript-for-student")
        transcript = True
    except RuntimeError:
        transcript = False        # 400 держится, пока нет академической истории
    return lessons, transcript
