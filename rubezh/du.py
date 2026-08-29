"""Клиент университетского портала du.astanait.edu.kz.

В отличие от Moodle, тут нормальный REST API и по нему отдаётся то, чего в Moodle
может не быть вовсе: расписание группы, транскрипт, выбор дисциплин.

Авторизация — JWT в заголовке `Authorization: Bearer ...`. Токен портал кладёт в
localStorage под ключом `token`, поэтому берём его прямо со страницы: сессия
браузера у нас уже есть, отдельный логин не нужен.

Порт **8765** обязателен — сам сайт на 443, а API на 8765. Без токена шлюз
отвечает 404, а не 401, так что «не найдено» здесь обычно значит «не авторизован».
"""
import time

SITE = "https://du.astanait.edu.kz"
API = "https://du.astanait.edu.kz:8765"


class DUError(RuntimeError):
    pass


class DU:
    def __init__(self, context):
        self.page = context.new_page()
        self.page.goto(f"{SITE}/", wait_until="domcontentloaded", timeout=90000)
        self.token = self._wait_for_token()

    def _wait_for_token(self):
        """Портал кладёт токен в localStorage не сразу — ждём."""
        for _ in range(20):
            token = self.page.evaluate("() => window.localStorage.getItem('token')")
            if token:
                return token
            time.sleep(2)
        raise DUError("Портал не отдал токен. Запусти: python rubezh.py login du")

    def get(self, path):
        response = self.page.request.get(
            API + path,
            headers={"Authorization": f"Bearer {self.token}"},
            timeout=60000,
        )
        if response.status == 404:
            raise DUError(f"404 на {path} — обычно это протухший токен, а не опечатка")
        if not response.ok:
            raise DUError(f"HTTP {response.status} на {path}")
        try:
            return response.json()
        except Exception:
            raise DUError(f"не JSON на {path}")

    # --- то, ради чего всё затевалось ---------------------------------------

    def profile(self):
        return self.get("/astanait-student-module/api/v1/student/profile/principal")

    def group(self):
        """Номер группы. В профиле его нет, зато портал сам просит расписание
        по нему при загрузке — подслушиваем собственный запрос страницы."""
        import re
        seen = []
        self.page.on("request", lambda r: seen.append(r.url) if "schedule/groupName/" in r.url else None)
        self.page.goto(f"{SITE}/", wait_until="domcontentloaded", timeout=90000)
        for _ in range(10):
            if seen:
                return re.search(r"groupName/([^/?&]+)", seen[0]).group(1)
            self.page.wait_for_timeout(1500)
        return None

    def schedule(self, group):
        return self.get(f"/astanait-schedule-module/api/v1/schedule/groupName/{group}")

    def transcript(self):
        return self.get(
            "/astanait-office-module/api/v1/academic-department"
            "/assessment-report/summary-sheet-by-for-transcript-for-student"
        )

    def gpa(self):
        return self.get(
            "/astanait-office-module/api/v1/academic-department"
            "/assessment-report/transcript-gpa-for-student"
        )
