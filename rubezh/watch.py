"""Проверка изменений и уведомление в Telegram.

Смысл: до 07.09 в системах пусто, и караулить это руками бессмысленно. Скрипт
ходит по Moodle и порталу, сравнивает с прошлым разом и пишет, только если
что-то правда изменилось. Молчание — это «ничего нового», а не «всё сломалось».

    python rubezh.py watch --dry    посмотреть, что бы отправилось
    python rubezh.py watch          отправить в Telegram

Раз в час через планировщик Windows, скрыто (см. README):

    schtasks /create /tn "AITU Rubezh" /tr "c:\\Projects\\University\\rubezh\\watch.bat" ^
             /sc daily /st 09:00
"""
import html
import json
from datetime import datetime
from pathlib import Path

import du
import fast
import moodle
import notify
import session

ROOT = Path(__file__).parent
STATE = ROOT / "state.json"
LOG = ROOT / "watch.log"
LOG_KEEP = 400          # строк; файл не должен расти бесконечно


def log(line):
    """Под планировщиком консоли нет, и без файла падения не видно вообще."""
    try:
        old = LOG.read_text(encoding="utf-8").splitlines() if LOG.exists() else []
        old.append(f"{datetime.now():%Y-%m-%d %H:%M}  {line}")
        LOG.write_text("\n".join(old[-LOG_KEEP:]) + "\n", encoding="utf-8")
    except OSError:
        pass            # логи не повод ронять проверку


DEADLINE_LIMIT = 50      # Moodle отвечает ошибкой на limitnum больше 50


def _gather(snap, key, fn):
    """Собрать один кусок данных и пометить, что он действительно собран.

    Без этой пометки провалившийся вызов не отличить от «стало пусто», и
    очередной сбой уходит пользователю как «все дедлайны сняли».
    """
    try:
        fn()
        snap["collected"].append(key)
    except Exception as error:
        snap.setdefault("errors", {})[key] = str(error)[:200]


def _events(snap, events):
    for event in events:
        snap["deadlines"][str(event.get("id"))] = {
            "name": event.get("name"),
            "when": event.get("timesort"),
            "course": ((event.get("course") or {}).get("fullname") or ""),
        }


def _moodle_part(snap):
    """Сначала по HTTP. Браузер поднимаем, только если ключи протухли."""
    try:
        courses, events = fast.moodle_snapshot(limit=DEADLINE_LIMIT)
        snap["sessions"]["lms"] = True
        snap["courses"] = courses
        _events(snap, events)
        snap["collected"] += ["courses", "deadlines"]
        snap.setdefault("via", {})["lms"] = "http"
        return
    except fast.Stale:
        pass                                  # ключи мертвы — ниже обновим браузером

    with session.browser("lms", headless=True, lean=True) as ctx:
        client = moodle.Moodle(ctx)           # бросит MoodleError, если сессия мертва
        snap["sessions"]["lms"] = True        # сюда дошли — значит, сессия живая
        snap.setdefault("via", {})["lms"] = "браузер"
        # Ключи в кэш, чтобы следующий час обошёлся без Chromium.
        fast.remember(moodle_sesskey=client.sesskey)
        session._save_cookies("lms", ctx)

        def courses():
            data = client.call(
                "core_course_get_enrolled_courses_by_timeline_classification",
                {"offset": 0, "limit": 50, "classification": "all", "sort": "fullname"},
            ) or {}
            snap["courses"] = {
                str(c["id"]): c.get("fullname") or c.get("shortname") or str(c["id"])
                for c in data.get("courses", [])
            }

        _gather(snap, "courses", courses)
        _gather(snap, "deadlines",
                lambda: _events(snap, client.deadlines(limit=DEADLINE_LIMIT)))


def _du_part(snap):
    group = fast.secrets().get("du_group")
    try:
        lessons, transcript = fast.du_snapshot(group)
        snap["sessions"]["du"] = True
        snap["group"] = group
        snap["schedule"] = lessons
        snap["transcript"] = transcript
        snap["collected"] += ["schedule", "transcript"]
        snap.setdefault("via", {})["du"] = "http"
        return
    except fast.Stale:
        pass

    with session.browser("du", headless=True, lean=True) as ctx:
        client = du.DU(ctx)                   # бросит DUError, если токена нет
        snap["sessions"]["du"] = True
        snap.setdefault("via", {})["du"] = "браузер"
        group = client.group() or group
        snap["group"] = group
        fast.remember(du_token=client.token, du_group=group)

        def schedule():
            if not group:
                raise RuntimeError("портал не назвал группу")
            body = client.schedule(group)
            body = body.get("body") if isinstance(body, dict) else body
            snap["schedule"] = len(body) if isinstance(body, list) else 0

        def transcript():
            try:
                client.transcript()
                snap["transcript"] = True     # 400 держится, пока нет академической истории
            except du.DUError:
                snap["transcript"] = False

        _gather(snap, "schedule", schedule)
        _gather(snap, "transcript", transcript)


def snapshot():
    """Текущее состояние обеих систем. Упавший источник помечаем, а не роняем всё."""
    snap = {"at": datetime.now().isoformat(timespec="seconds"), "sessions": {},
            "collected": [], "courses": {}, "deadlines": {}, "schedule": None,
            "transcript": False, "group": None}
    for name, part in (("lms", _moodle_part), ("du", _du_part)):
        try:
            part(snap)
        except Exception as error:           # сеть, протухшая кука, изменившаяся вёрстка
            # Не затираем True: до сессии мы дошли, упало что-то после неё.
            snap["sessions"].setdefault(name, False)
            snap.setdefault("errors", {})[name] = str(error)[:200]
    return snap


def esc(value):
    """Экранировать чужой текст перед вставкой в сообщение.

    Сообщения уходят с parse_mode=HTML. Название курса вида «Лаб <не сдана>»
    или амперсанд в теме ломают разбор, Telegram отвечает 400, и уведомление
    пропадает — а инструмент по замыслу молчит, когда новостей нет, так что
    потерю не заметить. Соседний tg-digest делает ровно так же (html.escape).
    """
    return html.escape(str(value), quote=False)


def compare(old, new):
    """Человеческие строки об изменениях. Пусто — значит ничего не поменялось."""
    lines, urgent = [], False

    for name, title in (("lms", "Moodle"), ("du", "портал")):
        was = old.get("sessions", {}).get(name)
        now = new["sessions"].get(name)
        if was and not now:
            lines.append(f"🔑 Сессия <b>{title}</b> протухла. Нужно войти руками: "
                         f"<code>python rubezh.py login {name}</code>")
            urgent = True
        elif was is False and now:
            lines.append(f"✅ Сессия <b>{title}</b> снова живая.")

    # Сравниваем только то, что оба раза удалось собрать. Пустой список из-за
    # сбоя ничем не отличается от честного «стало пусто», и без этой проверки
    # любая осечка уходит пользователю как «все курсы пропали».
    def both(key):
        return key in old.get("collected", []) and key in new.get("collected", [])

    if both("courses"):
        appeared = set(new["courses"]) - set(old.get("courses", {}))
        gone = set(old.get("courses", {})) - set(new["courses"])
        for key in sorted(appeared):
            lines.append(f"📚 Новый курс: <b>{esc(new['courses'][key])}</b>")
        for key in sorted(gone):
            lines.append(f"➖ Курс пропал: {esc(old['courses'][key])}")

    if both("deadlines"):
        old_dl, new_dl = old.get("deadlines", {}), new["deadlines"]
        for key in sorted(set(new_dl) - set(old_dl)):
            item = new_dl[key]
            lines.append(f"🆕 Дедлайн: <b>{esc(item['name'])}</b> — {_when(item['when'])}"
                         + (f" ({esc(item['course'])})" if item["course"] else ""))
        for key in sorted(set(new_dl) & set(old_dl)):
            if new_dl[key]["when"] != old_dl[key]["when"]:
                lines.append(f"⏰ Перенос: <b>{esc(new_dl[key]['name'])}</b> "
                             f"{_when(old_dl[key]['when'])} → {_when(new_dl[key]['when'])}")
                urgent = True
        for key in sorted(set(old_dl) - set(new_dl)):
            lines.append(f"✔️ Больше не висит: {esc(old_dl[key]['name'])}")

    if both("schedule"):
        was_sch, now_sch = old.get("schedule"), new.get("schedule")
        if not was_sch and now_sch:
            lines.append(f"🗓 Появилось расписание группы {esc(new.get('group'))}: {now_sch} занятий")
        elif was_sch and now_sch and was_sch != now_sch:
            lines.append(f"🗓 Расписание изменилось: было {was_sch}, стало {now_sch}")

    if both("transcript") and new.get("transcript") and not old.get("transcript"):
        lines.append("📊 Транскрипт открылся — появились оценки на портале")

    return lines, urgent


def _when(ts):
    if not ts:
        return "без срока"
    return datetime.fromtimestamp(ts).strftime("%d.%m %H:%M")


def run(dry=False):
    old = {}
    if STATE.exists():
        try:
            old = json.loads(STATE.read_text(encoding="utf-8"))
        except ValueError:
            pass

    new = snapshot()
    lines, urgent = compare(old, new)

    # Самый первый запуск: сравнивать не с чем, поэтому не сыплем «новый дедлайн»
    # на всё подряд, а один раз сообщаем, что взяли на карандаш.
    if not old:
        lines = [f"👋 AITU Rubezh следит за изменениями.",
                 f"Сейчас: курсов {len(new['courses'])}, дедлайнов {len(new['deadlines'])}, "
                 f"занятий в расписании {new.get('schedule') if new.get('schedule') is not None else '—'}.",
                 "Дальше напишу, только когда что-то поменяется."]
        urgent = False

    via = ",".join(f"{k}:{v}" for k, v in sorted((new.get("via") or {}).items())) or "—"
    errs = ("; ошибки: " + ", ".join(new.get("errors", {}))) if new.get("errors") else ""
    log(f"через {via}; собрано {len(new['collected'])}/4; изменений {len(lines)}{errs}")

    if not lines:
        print(f"Изменений нет ({new['at']}).")
        if new.get("errors"):
            print("  недоступно:", ", ".join(new["errors"]))
    else:
        head = "<b>AITU Rubezh</b>" + (" — нужно внимание" if urgent else "")
        text = head + "\n\n" + "\n".join(lines)
        if dry:
            print(text.replace("<b>", "").replace("</b>", "")
                      .replace("<code>", "").replace("</code>", ""))
        else:
            try:
                notify.send(text)
            except SystemExit as error:
                # Состояние намеренно НЕ сохраняем: изменение ещё не доставлено,
                # и если записать его как «известное», пользователь о нём не узнает
                # уже никогда. Пусть повторится на следующем прогоне.
                log(f"НЕ ОТПРАВЛЕНО: {str(error)[:120]}")
                print(f"Не отправлено: {error}")
                return 1
            print(f"Отправлено в Telegram: {len(lines)} изменений.")

    if not dry:
        STATE.write_text(json.dumps(new, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0
