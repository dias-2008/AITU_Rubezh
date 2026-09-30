"""Проверка изменений и уведомление в Telegram.

Смысл: до 07.09 в системах пусто, и караулить это руками бессмысленно. Скрипт
ходит по Moodle и порталу, сравнивает с прошлым разом и пишет, только если
что-то правда изменилось. Молчание — это «ничего нового», а не «всё сломалось».

    python rubezh.py watch --dry    посмотреть, что бы отправилось
    python rubezh.py watch          отправить в Telegram

Раз в час без человека — задачу ставит одна команда (Windows: schtasks,
macOS: launchd; см. desktop.py):

    python rubezh.py schedule
"""
import html
import json
from datetime import datetime
from pathlib import Path

import du
import fast
import outlook
import moodle
import notify
import paths
import sections
import session

ROOT = paths.ROOT
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

# Что вообще должно собираться за прогон — чтобы в логе не было «5 из 4».
EXPECTED = ("courses", "deadlines", "notes", "schedule", "transcript", "mail")


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


def _notes(snap):
    """Тексты и материалы со страниц курсов — то, чего календарь не знает.

    Читаем каждый курс, а не только текущую неделю: преподаватель может
    дописать в прошлую или заранее выложить следующую. Один упавший курс не
    должен ронять остальные — но и «собрано» тогда не ставим, иначе его блоки
    ушли бы как пропавшие.
    """
    if "courses" not in snap["collected"]:
        raise RuntimeError("список курсов не собран — без него блоки не с чем сопоставить")
    notes, failed = {}, []
    for cid, title in snap["courses"].items():
        try:
            notes.update(sections.digest(cid, title, sections.read(cid)))
        except Exception as error:
            failed.append(f"{title}: {str(error)[:80]}")
    if failed:
        raise RuntimeError("; ".join(failed))
    snap["notes"] = notes


def _moodle_part(snap):
    """Сначала по HTTP. Браузер поднимаем, только если ключи протухли."""
    try:
        courses, events = fast.moodle_snapshot(limit=DEADLINE_LIMIT)
        snap["sessions"]["lms"] = True
        snap["courses"] = courses
        _events(snap, events)
        snap["collected"] += ["courses", "deadlines"]
        snap.setdefault("via", {})["lms"] = "http"
        _gather(snap, "notes", lambda: _notes(snap))
        return
    except fast.Stale:
        pass                                  # ключи мертвы — ниже обновим браузером

    with session.connect("lms") as (ctx, _page):   # сперва попробует поднять молча
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
        # Куки уже сохранены выше — страницы курсов читаются по HTTP.
        _gather(snap, "notes", lambda: _notes(snap))


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

    with session.connect("du") as (ctx, _page):
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


def _mail_part(snap, old):
    """Университетская почта.

    Модель запускаем, ТОЛЬКО если появились новые письма: gemma4:e4b весит почти
    десять гигабайт, и гонять её каждый час впустую — ровно та трата ресурсов,
    которой мы избегали, уходя от запуска Chromium.
    """
    messages = outlook.fetch(limit=12, full=False)
    snap["mail"] = [m["title"] for m in messages]
    snap["collected"].append("mail")

    # Сравниваем с последним удачным чтением почты, а не с прошлым снимком:
    # снимок мог не дойти до Outlook вовсе, и тогда «новыми» окажутся все письма
    # подряд либо, наоборот, ни одного.
    seen = set(known(old).get("mail") or (old or {}).get("mail") or [])
    fresh = [m for m in messages if m["title"] not in seen]
    if fresh and seen:             # на первом запуске не разбираем всю историю
        snap["mail_findings"] = outlook.digest(fresh)
    else:
        snap["mail_findings"] = known(old).get("mail_findings") or (old or {}).get("mail_findings", [])


def snapshot(old=None):
    """Текущее состояние систем. Упавший источник помечаем, а не роняем всё."""
    snap = {"at": datetime.now().isoformat(timespec="seconds"), "sessions": {},
            "collected": [], "courses": {}, "deadlines": {}, "schedule": None,
            "transcript": False, "group": None}
    parts = (("lms", _moodle_part), ("du", _du_part),
             ("outlook", lambda sn: _mail_part(sn, old)))
    for name, part in parts:
        try:
            part(snap)
        except (Exception, SystemExit) as error:   # сеть, кука, вёрстка, требование войти руками
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


# Ключ состояния -> флаг в `collected`, который говорит, что его правда собрали.
TRACKED = {"courses": "courses", "deadlines": "deadlines", "notes": "notes", "schedule": "schedule",
           "transcript": "transcript", "mail": "mail", "mail_findings": "mail"}


def known(state):
    """Последнее, что мы ДЕЙСТВИТЕЛЬНО видели, по каждому ключу отдельно."""
    return dict((state or {}).get("known") or {})


def remember(old, new):
    """Обновить это «последнее виденное» — только по собранным ключам.

    Раньше сравнение шло с предыдущим снимком целиком, и это молча теряло
    новости. Пока сессия лежала, снимки приходили пустыми; когда она
    поднималась, новое сравнивать было не с чем (в старом снимке ключ не
    собран), и сравнение честно пропускалось, чтобы не сыпать «всё пропало».
    Итог: курсы и задания, появившиеся за время простоя, тихо записывались в
    состояние как известные — и человек о них не узнавал никогда. Ровно того
    уведомления, которого ждут, и не приходило.

    Теперь помним последнее удачное чтение каждого ключа отдельно от снимка:
    простой перестаёт съедать новости, а сравнение по-прежнему не выдумывает
    пропаж из-за сбоя.
    """
    base = known(old)
    for key, flag in TRACKED.items():
        if flag in new.get("collected", []):
            base[key] = new.get(key)
    return base


def nag(old, new):
    """Напомнить про лежащую сессию — раз в сутки, пока её не поднимут.

    «Сессия протухла» уходит один раз, в момент падения. Дальше проверка молчит,
    и молчание читается как «ничего нового» — хотя на самом деле это «я ничего
    не вижу». Пропустил одно сообщение вечером — и неделю не знаешь, что в
    Moodle появились задания. Поэтому пока сессия лежит, раз в сутки напоминаем,
    а состояние держим в `down`: когда упала и когда напомнили в последний раз.
    """
    lines, down = [], dict((old or {}).get("down") or {})
    now = datetime.now()
    for name, title in (("lms", "Moodle"), ("du", "портал")):
        if new["sessions"].get(name):
            down.pop(name, None)                # поднялась — забываем
            continue
        entry = down.setdefault(name, {"since": now.isoformat(timespec="seconds")})
        try:
            since = datetime.fromisoformat(entry["since"])
            told = datetime.fromisoformat(entry["told"]) if entry.get("told") else since
        except ValueError:
            since = told = now
        days = (now - since).days
        if days >= 1 and (now - told).total_seconds() >= 20 * 3600:
            entry["told"] = now.isoformat(timespec="seconds")
            lines.append(
                f"🔒 Сессия <b>{title}</b> лежит {days}-й день. Новое оттуда не приходит "
                f"вообще: <code>python rubezh.py login {name}</code> — или кнопка "
                f"«Войти» на дашборде."
            )
    new["down"] = down
    return lines


def compare(old, new):
    """Человеческие строки об изменениях. Пусто — значит ничего не поменялось."""
    lines, urgent = [], False
    base = known(old)

    for name, title in (("lms", "Moodle"), ("du", "портал"), ("outlook", "Outlook")):
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
        return key in base and key in new.get("collected", [])

    if both("courses"):
        appeared = set(new["courses"]) - set(base.get("courses") or {})
        gone = set(base.get("courses") or {}) - set(new["courses"])
        for key in sorted(appeared):
            lines.append(f"📚 Новый курс: <b>{esc(new['courses'][key])}</b>")
        for key in sorted(gone):
            lines.append(f"➖ Курс пропал: {esc(base['courses'][key])}")

    if both("deadlines"):
        old_dl, new_dl = base.get("deadlines") or {}, new["deadlines"]
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

    # Блоки на страницах курсов. Ключ — cmid, поэтому видно и новый текст, и
    # правку старого. Именно так пропадал семинар «подготовьте темы…», у
    # которого нет срока и которого нет в календаре.
    if both("notes"):
        old_n, new_n = base.get("notes") or {}, new["notes"]
        for key in sorted(set(new_n) - set(old_n)):
            lines.append(_note_line("📝 Новое в курсе", new_n[key]))
        for key in sorted(set(new_n) & set(old_n)):
            if (new_n[key].get("text"), new_n[key].get("name")) != (old_n[key].get("text"), old_n[key].get("name")):
                lines.append(_note_line("✏️ Изменилось в курсе", new_n[key]))

    if both("schedule"):
        was_sch, now_sch = base.get("schedule"), new.get("schedule")
        if not was_sch and now_sch:
            lines.append(f"🗓 Появилось расписание группы {esc(new.get('group'))}: {now_sch} занятий")
        elif was_sch and now_sch and was_sch != now_sch:
            lines.append(f"🗓 Расписание изменилось: было {was_sch}, стало {now_sch}")

    for finding in new.get("mail_findings", []):
        if finding not in (base.get("mail_findings") or []):
            lines.append(f"📧 Из почты: {esc(finding)}")
            urgent = True

    if both("transcript") and new.get("transcript") and not base.get("transcript"):
        lines.append("📊 Транскрипт открылся — появились оценки на портале")

    return lines, urgent


def _note_line(prefix, note):
    """Одна строка про блок курса: название со ссылкой или начало текста."""
    where = f"{prefix} <b>{esc(note.get('course'))}</b>, {esc(note.get('section'))}: "
    text = " ".join((note.get("text") or "").split())
    if note.get("name"):
        body = esc(note["name"])
        if note.get("url"):
            body = f'<a href="{esc(note["url"])}">{body}</a>'
        # Описание задания или страницы — то, ради чего его читаем: «подготовьте…»
        if text:
            body += " — " + esc(text[:200] + ("…" if len(text) > 200 else ""))
        return where + body
    return where + esc(text[:280] + ("…" if len(text) > 280 else ""))


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

    new = snapshot(old)
    lines, urgent = compare(old, new)
    new["known"] = remember(old, new)   # что видели — помним по ключам, а не снимком
    reminders = nag(old, new)           # и не молчим, пока сессия лежит
    if reminders:
        lines += reminders
        urgent = True

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
    log(f"через {via}; собрано {len(new['collected'])}/{len(EXPECTED)}; изменений {len(lines)}{errs}")

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
        # Дашборд пересобираем здесь же. Иначе файл на диске остаётся таким,
        # каким его собрали в прошлый раз: в Telegram приходит «новое задание»,
        # человек открывает страницу — а там пусто, потому что она недельной
        # давности. Проверка всё равно уже сходила за данными, это секунды.
        try:
            import web
            web.build()
        except Exception as error:
            log(f"дашборд не пересобрался: {str(error)[:120]}")
    return 0
