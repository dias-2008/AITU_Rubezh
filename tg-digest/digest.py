#!/usr/bin/env python
"""Дайджест важных сообщений из университетских Telegram-групп.

  python digest.py setup     — мастер: ключи, модель, вход в Telegram, чаты, планировщик
  python digest.py chats     — показать твои группы и их id (для config.json)
  python digest.py run       — собрать новые сообщения, выжать важное, прислать в бота
  python digest.py run --dry — то же самое, но напечатать в консоль и не двигать state
"""
import argparse
import asyncio
import html
import json
import os
import re
import sys
from pathlib import Path

import requests
from telethon import TelegramClient, functions

import llm

ROOT = llm.ROOT

# У каждого человека свой профиль: своя сессия Telegram, свои чаты, свой указатель
# прочитанного. Общими остаются только код и .env в корне (ключи и токен бота).
PROFILES = ROOT / "profiles"

INSTRUCTION = """Ты — помощник первокурсника университета. На вход даны новые сообщения из его группы.

Твоя задача: выбрать только то, что студенту реально важно, и выбросить весь флуд.

ВАЖНО (обязательно включай):
- дедлайны, даты сдачи, сроки
- задания, домашка, лабы, проекты, что нужно сделать
- изменения в расписании, отмена/перенос пар, аудитории
- экзамены, коллоквиумы, СРС, аттестации, пересдачи
- объявления преподавателей, кураторов и старосты
- оргмоменты: сбор денег, документы, справки, регистрация на что-либо
- встречи, мероприятия, конференции, хакатоны, олимпиады, соревнования — с датой и местом
- возможности: стажировки, вакансии, гранты, конкурсы, отборы
- учебные материалы: методички, путеводители, шпаргалки, презентации, любые файлы и ссылки

НЕ ВАЖНО (выбрасывай):
- приветствия, благодарности, "+", "ок", смайлики, стикеры
- личные разговоры, шутки, мемы, споры не по делу
- повторы одного и того же

Правила ответа:
- Пиши по-русски.
- Без markdown-разметки: не используй **, ##, ` и т.п. Только обычный текст, эмодзи и ссылки.
- Каждый пункт — одна строка: суть + дата/срок, если есть + номер сообщения.
- В КОНЦЕ КАЖДОГО ПУНКТА обязательно поставь номер сообщения в виде #12345 — по нему
  студент откроет это место прямо в чате. Если пункт собран из нескольких сообщений,
  укажи все номера подряд: #12345 #12346
- Бери номера только из входных данных, не выдумывай их.
- Сортируй по срочности: сначала то, у чего ближе дедлайн.
- Если у сообщения было фото или документ — так и напиши, студенту нужно открыть и посмотреть.
- Ничего не выдумывай. Если срок не указан — не пиши срок.
- Если в этой пачке нет ничего важного, ответь ровно одним словом: НЕТ

Формат:
🔴 Дедлайны и задания
- ...

🟡 Расписание и оргмоменты
- ...

🟢 Полезное
- ...

Пустые разделы просто не пиши.

Сообщения даны в формате «#номер Автор: текст».
"""

NL = chr(10)
MSG_REF = re.compile(r"#(\d+)")

# Инструкция в начале теряется под сотней сообщений: модель начинает пересказывать чат
# вместо выжимки и роняет номера. Поэтому ключевые правила повторяем ПОСЛЕ сообщений.
REMINDER = """

=== КОНЕЦ СООБЩЕНИЙ ===

Напоминание. Эти правила важнее всего, что выше:
1. Это НЕ пересказ чата. Шутки, флуд, споры, обсуждения игр и городов, «кто где был» —
   выбрасывай целиком, даже если этого много.
2. Оставляй: дедлайны, задания, экзамены и аттестации, изменения расписания, объявления
   преподавателей и кураторов, оргмоменты, встречи и мероприятия с датой, конференции и
   хакатоны, стажировки и конкурсы, а также любые учебные файлы и материалы.
   Событие или присланный файл — это ВАЖНО, даже если там нет слова «дедлайн».
3. Если в пачке есть хотя бы один присланный файл, документ, фото с объявлением или анонс
   события — пачка НЕ пустая, отвечать НЕТ нельзя. Присланный документ полезен и позже,
   даже если само событие уже прошло: вынеси его в 🟢 Полезное.
4. Слово НЕТ — это ответ целиком, вместо всего остального. Никогда не пиши НЕТ внутри
   раздела: раздел без пунктов просто пропусти вместе с заголовком.
5. В конце каждого пункта поставь номера сообщений в виде #12345, только реальные.
6. Без markdown. Разделы: 🔴 Дедлайны и задания / 🟡 Расписание и оргмоменты / 🟢 Полезное
"""

JUNK = re.compile(
    r"^(\+|-|ок|окей|ok|хорошо|спасибо|спс|благодарю|рахмет|рақмет|да|нет|ага|угу|ясно|понял|поняла)[.!)\s]*$",
    re.IGNORECASE,
)
NO_LETTERS = re.compile(r"^[^\w]*$", re.UNICODE)


load_env = llm.load_env  # общий ридер .env, чтобы не расходились


def _log_to_file(path):
    """Под планировщиком консоли нет — пишем прогон в digest.log профиля.

    Раньше это делал run.bat с перенаправлением вывода и run_hidden.vbs, чтобы
    спрятать окно. Теперь задача запускает pythonw / digestw.exe напрямую:
    у них `sys.stdout` равен None, и всё, что печатается, ушло бы в никуда.
    """
    if sys.stdout is not None and sys.stderr is not None:
        return
    from datetime import datetime
    handle = open(path, "a", encoding="utf-8", buffering=1)
    handle.write(f"========== {datetime.now():%Y-%m-%d %H:%M} ==========" + chr(10))
    sys.stdout = sys.stdout or handle
    sys.stderr = sys.stderr or handle


def profile_dir(name):
    """Папка профиля. Создаётся при первом обращении вместе с пустым config.json."""
    directory = PROFILES / name
    directory.mkdir(parents=True, exist_ok=True)
    config = directory / "config.json"
    if not config.exists():
        template = json.loads((llm.ASSETS / "config.example.json").read_text(encoding="utf-8"))
        template["chats"] = []
        config.write_text(json.dumps(template, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Создан профиль {name}: {config}", file=sys.stderr)
    return directory


def load_json(path, default):
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return default


def msg_link(chat_id, username, msg_id, topic_id=None):
    if username:
        base = f"https://t.me/{username}"
    else:
        internal = str(chat_id)
        internal = internal[4:] if internal.startswith("-100") else internal.lstrip("-")
        base = f"https://t.me/c/{internal}"
    # В группах-форумах ссылка на сообщение включает тему, иначе Telegram её не откроет.
    return f"{base}/{topic_id}/{msg_id}" if topic_id else f"{base}/{msg_id}"


def describe_media(message):
    if message.photo:
        return "[фото]"
    if message.document:
        name = ""
        for attr in getattr(message.document, "attributes", []):
            name = getattr(attr, "file_name", "") or name
        return f"[файл: {name}]" if name else "[файл]"
    if message.poll:
        return "[опрос]"
    return ""


def sender_name(message):
    who = message.sender
    if who is None:
        return "Канал"
    parts = [getattr(who, "first_name", "") or "", getattr(who, "last_name", "") or ""]
    full = " ".join(p for p in parts if p)
    return full or getattr(who, "title", None) or getattr(who, "username", None) or "Кто-то"


def is_junk(text, media):
    if media:
        return False
    text = text.strip()
    return not text or len(text) < 3 or bool(JUNK.match(text)) or bool(NO_LETTERS.match(text))


def linkify(summary, chat_id, username, topic_id=None):
    """Превращает #12345 из ответа модели в кликабельную ссылку на сообщение в чате.

    Ссылки строим сами, а не просим у модели: модель их регулярно теряет или выдумывает.
    """
    escaped = html.escape(summary)
    return MSG_REF.sub(
        lambda m: f'<a href="{msg_link(chat_id, username, int(m.group(1)), topic_id)}">↗</a>',
        escaped,
    )


def split_message(text, limit=3800):
    chunks, current = [], ""
    for line in text.split("\n"):
        # Строка длиннее лимита сама по себе: режем принудительно, иначе Telegram
        # отклонит всё сообщение и дайджест не дойдёт вообще.
        while len(line) > limit:
            if current.strip():
                chunks.append(current)
            current = ""
            chunks.append(line[:limit])
            line = line[limit:]
        if len(current) + len(line) + 1 > limit:
            if current.strip():  # пустой кусок Telegram тоже не примет
                chunks.append(current)
            current = ""
        current += line + "\n"
    if current.strip():
        chunks.append(current)
    return chunks


def send_to_bot(token, chat_id, text):
    for chunk in split_message(text):
        response = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": chunk, "parse_mode": "HTML",
                  "disable_web_page_preview": True},
            timeout=60,
        )
        if not response.ok:
            raise RuntimeError(
                f"Бот не смог отправить ({response.status_code}): {response.text[:300]}\n"
                "Скорее всего ты ещё не нажал Start в своём боте — открой его в Telegram и нажми."
            )


async def cmd_chats(client, config_path=None):
    """Группы и темы, где состоит человек. Печатает и возвращает список.

    Сам список ничего не выбирает: из него берёт строки либо человек руками
    (в config.json), либо мастер `setup`, который спросит номера.
    """
    found = []

    def show(entry):
        found.append(entry)
        indent = "      " if "topic_id" in entry else "    "
        print(f"{indent}{len(found):>3}. {entry['title']}")

    if config_path:
        print(f"{NL}Твои группы и каналы. Это только список — сам он ничего не выбирает.{NL}"
              f"Скопируй нужные строки в поле \"chats\" файла:{NL}  {config_path}{NL}")
    async for dialog in client.iter_dialogs():
        if not (dialog.is_group or dialog.is_channel):
            continue
        show({"id": dialog.id, "title": dialog.title})

        # Группа-форум: показываем темы отдельно, чтобы можно было взять только свой курс,
        # а не всё вперемешку с чужими объявлениями.
        if not getattr(dialog.entity, "forum", False):
            continue
        try:
            topics = await client(functions.messages.GetForumTopicsRequest(
                peer=dialog.entity, offset_date=None, offset_id=0, offset_topic=0, limit=100))
        except Exception as error:
            print(f'      # темы получить не удалось: {error}')
            continue
        for topic in topics.topics:
            topic_id = getattr(topic, "id", None)
            if topic_id is None:
                continue
            show({"id": dialog.id, "topic_id": topic_id,
                  "title": f"{dialog.title} / {getattr(topic, 'title', '?')}"})
    if config_path:
        print(NL + "Строки для config.json:")
        for entry in found:
            print("    " + json.dumps(entry, ensure_ascii=False) + ",")
    print()
    return found


async def cmd_run(client, config, state_path, dry, fake):
    state = load_json(state_path, {})
    max_messages = config.get("max_messages_per_chat", 500)
    max_chars = config.get("max_chars_per_message", 600)
    max_requests = config.get("max_requests_per_run", 20)

    # --fake подменяет провайдера заглушкой: весь конвейер прогоняется, модель не вызывается.
    llm_config = {**config, "backend": "fake"} if fake else config

    sections = []
    new_state = dict(state)
    requests_made = 0

    for chat in config["chats"]:
        # Проверяем лимит до чтения чата, иначе сдвинем state по непрочитанному.
        if requests_made >= max_requests:
            print(f"Лимит {max_requests} запросов исчерпан, остальные чаты — в следующий раз",
                  file=sys.stderr)
            break

        chat_id, title = chat["id"], chat["title"]
        topic_id = chat.get("topic_id")
        # У каждой темы форума свой указатель: иначе одна тема съедала бы прогресс другой.
        state_key = f"{chat_id}:{topic_id}" if topic_id else str(chat_id)
        last_id = state.get(state_key, 0)

        entity = await client.get_entity(chat_id)
        username = getattr(entity, "username", None)

        extra = {"reply_to": topic_id} if topic_id else {}
        messages = []
        async for message in client.iter_messages(entity, min_id=last_id, limit=max_messages, **extra):
            messages.append(message)
        messages.reverse()

        lines = []
        for message in messages:
            text = (message.text or "").strip()
            media = describe_media(message)
            if is_junk(text, media):
                continue
            # Переносы строк схлопываем: одно сообщение обязано занимать одну строку,
            # иначе многострочное объявление читается моделью как несколько разных.
            body = re.sub(r"\s+", " ", " ".join(filter(None, [media, text]))).strip()[:max_chars]
            lines.append(f"#{message.id} {sender_name(message)}: {body}")

        print(f"{title}: {len(messages)} новых, {len(lines)} после фильтра", file=sys.stderr)
        if not lines:
            if messages:  # был только мусор — двигаем, разбирать нечего
                new_state[state_key] = messages[-1].id
            continue

        # Название чата идёт в stdin, а не в инструкцию: инструкция уходит аргументом
        # в claude.cmd, и пользовательские данные там появляться не должны.
        try:
            summary = llm.summarize(INSTRUCTION,
                                    f"Группа: {title}\n\n" + "\n".join(lines) + REMINDER,
                                    llm_config)
        except Exception as error:
            # Один упавший чат не должен убивать весь прогон, и главное — не двигаем
            # state, иначе эти сообщения будут потеряны навсегда.
            print(f"{title}: модель не ответила ({error}) — вернёмся к нему в следующий раз",
                  file=sys.stderr)
            continue

        requests_made += 1
        new_state[state_key] = messages[-1].id  # только после успешного разбора
        if summary and summary.strip().upper().rstrip(".") != "НЕТ":
            sections.append(f"📚 {html.escape(title)}\n\n"
                            f"{linkify(summary, chat_id, username, topic_id)}")

    report = "\n\n———\n\n".join(sections) if sections else None
    if report is None:
        print("Ничего важного не нашлось.", file=sys.stderr)

    if dry:
        print("\n" + (report or "(пусто)"))
        return

    if report:
        me = await client.get_me()
        send_to_bot(os.environ["BOT_TOKEN"], me.id, report)
    state_path.write_text(json.dumps(new_state, ensure_ascii=False, indent=2), encoding="utf-8")


async def main():
    parser = argparse.ArgumentParser(description="Дайджест университетских Telegram-групп")
    parser.add_argument("command", choices=["setup", "chats", "run"])
    parser.add_argument("--dry", action="store_true", help="напечатать в консоль, ничего не отправлять")
    parser.add_argument("--fake", action="store_true",
                        help="не вызывать модель вообще — прогнать сбор и фильтр бесплатно")
    parser.add_argument("--profile", default="default",
                        help="чей дайджест: своя сессия, свои чаты, свой прогресс")
    args = parser.parse_args()

    if args.command == "setup":
        import setup
        return setup.main(args.profile)

    directory = profile_dir(args.profile)
    llm.set_usage_log(directory / "usage.log")
    _log_to_file(directory / "digest.log")
    load_env()

    config = load_json(directory / "config.json", {})
    if args.command == "run" and not config.get("chats"):
        sys.exit(f"В профиле {args.profile} не выбраны чаты. Сначала: "
                 f"python digest.py chats --profile {args.profile}")

    client = TelegramClient(str(directory / "telegram"),
                            int(os.environ["TG_API_ID"]), os.environ["TG_API_HASH"])
    await client.connect()
    try:
        if not await client.is_user_authorized():
            # Под планировщиком интерактивного входа быть не может: вместо ожидания
            # ввода телефона и трейсбека говорим человеку, что именно сделать.
            if args.command == "run":
                sys.exit(f"Профиль {args.profile} не авторизован в Telegram. Вход делает "
                         f"владелец аккаунта вручную: python digest.py chats --profile {args.profile}")
            await client.start()  # телефон, код из Telegram, при необходимости 2FA
        if args.command == "chats":
            await cmd_chats(client, directory / "config.json")
        else:
            await cmd_run(client, config, directory / "state.json", args.dry, args.fake)
    finally:
        await client.disconnect()


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    sys.exit(asyncio.run(main()))
