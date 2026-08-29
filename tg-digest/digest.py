#!/usr/bin/env python
"""Дайджест важных сообщений из университетских Telegram-групп.

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
from telethon import TelegramClient

import llm

ROOT = Path(__file__).parent
CONFIG_PATH = ROOT / "config.json"
STATE_PATH = ROOT / "state.json"
SESSION = str(ROOT / "university")

INSTRUCTION = """Ты — помощник первокурсника университета. На вход даны новые сообщения из его группы.

Твоя задача: выбрать только то, что студенту реально важно, и выбросить весь флуд.

ВАЖНО (обязательно включай):
- дедлайны, даты сдачи, сроки
- задания, домашка, лабы, проекты, что нужно сделать
- изменения в расписании, отмена/перенос пар, аудитории
- экзамены, коллоквиумы, СРС, аттестации, пересдачи
- объявления преподавателей и старосты
- оргмоменты: сбор денег, документы, справки, регистрация на что-либо
- ссылки на материалы, таблицы, формы

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

MSG_REF = re.compile(r"#(\d+)")

# Инструкция в начале теряется под сотней сообщений: модель начинает пересказывать чат
# вместо выжимки и роняет номера. Поэтому ключевые правила повторяем ПОСЛЕ сообщений.
REMINDER = """

=== КОНЕЦ СООБЩЕНИЙ ===

Напоминание. Эти правила важнее всего, что выше:
1. Это НЕ пересказ чата. Шутки, флуд, споры, обсуждения игр и городов, «кто где был» —
   выбрасывай целиком, даже если этого много.
2. Оставляй только: дедлайны, задания, экзамены и аттестации, изменения расписания,
   объявления преподавателей и старосты, оргмоменты, ссылки на учебные материалы.
3. Если ничего из пункта 2 нет — ответь ровно одним словом: НЕТ
4. В конце каждого пункта поставь номера сообщений в виде #12345, только реальные.
5. Без markdown. Разделы: 🔴 Дедлайны и задания / 🟡 Расписание и оргмоменты / 🟢 Полезное
"""

JUNK = re.compile(
    r"^(\+|-|ок|окей|ok|хорошо|спасибо|спс|благодарю|рахмет|рақмет|да|нет|ага|угу|ясно|понял|поняла)[.!)\s]*$",
    re.IGNORECASE,
)
NO_LETTERS = re.compile(r"^[^\w]*$", re.UNICODE)


def load_env():
    """Простой .env-ридер, чтобы не тащить python-dotenv."""
    env = ROOT / ".env"
    if not env.exists():
        sys.exit("Нет файла .env — скопируй .env.example в .env и заполни.")
    for line in env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def load_json(path, default):
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return default


def msg_link(chat_id, username, msg_id):
    if username:
        return f"https://t.me/{username}/{msg_id}"
    internal = str(chat_id)
    internal = internal[4:] if internal.startswith("-100") else internal.lstrip("-")
    return f"https://t.me/c/{internal}/{msg_id}"


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


def linkify(summary, chat_id, username):
    """Превращает #12345 из ответа модели в кликабельную ссылку на сообщение в чате.

    Ссылки строим сами, а не просим у модели: модель их регулярно теряет или выдумывает.
    """
    escaped = html.escape(summary)
    return MSG_REF.sub(
        lambda m: f'<a href="{msg_link(chat_id, username, int(m.group(1)))}">↗</a>',
        escaped,
    )


def split_message(text, limit=3800):
    chunks, current = [], ""
    for line in text.split("\n"):
        if len(current) + len(line) + 1 > limit:
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


async def cmd_chats(client):
    print("\nТвои группы и каналы. Скопируй нужные строки в config.json -> chats:\n")
    async for dialog in client.iter_dialogs():
        if dialog.is_group or dialog.is_channel:
            print(f'    {{"id": {dialog.id}, "title": {json.dumps(dialog.title, ensure_ascii=False)}}},')
    print()


async def cmd_run(client, config, dry, fake):
    state = load_json(STATE_PATH, {})
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
        last_id = state.get(str(chat_id), 0)

        entity = await client.get_entity(chat_id)
        username = getattr(entity, "username", None)

        messages = []
        async for message in client.iter_messages(entity, min_id=last_id, limit=max_messages):
            messages.append(message)
        messages.reverse()

        if messages:
            new_state[str(chat_id)] = messages[-1].id

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
            continue

        # Название чата идёт в stdin, а не в инструкцию: инструкция уходит аргументом
        # в claude.cmd, и пользовательские данные там появляться не должны.
        summary = llm.summarize(INSTRUCTION,
                                f"Группа: {title}\n\n" + "\n".join(lines) + REMINDER,
                                llm_config)
        requests_made += 1
        if summary and summary.strip().upper().rstrip(".") != "НЕТ":
            sections.append(f"📚 {html.escape(title)}\n\n{linkify(summary, chat_id, username)}")

    report = "\n\n———\n\n".join(sections) if sections else None
    if report is None:
        print("Ничего важного не нашлось.", file=sys.stderr)

    if dry:
        print("\n" + (report or "(пусто)"))
        return

    if report:
        me = await client.get_me()
        send_to_bot(os.environ["BOT_TOKEN"], me.id, report)
    STATE_PATH.write_text(json.dumps(new_state, ensure_ascii=False, indent=2), encoding="utf-8")


async def main():
    parser = argparse.ArgumentParser(description="Дайджест университетских Telegram-групп")
    parser.add_argument("command", choices=["chats", "run"])
    parser.add_argument("--dry", action="store_true", help="напечатать в консоль, ничего не отправлять")
    parser.add_argument("--fake", action="store_true",
                        help="не вызывать модель вообще — прогнать сбор и фильтр бесплатно")
    args = parser.parse_args()

    load_env()
    client = TelegramClient(SESSION, int(os.environ["TG_API_ID"]), os.environ["TG_API_HASH"])
    await client.start()
    try:
        if args.command == "chats":
            await cmd_chats(client)
        else:
            if not CONFIG_PATH.exists():
                sys.exit("Нет config.json — скопируй config.example.json и впиши свои группы.")
            await cmd_run(client, load_json(CONFIG_PATH, {}), args.dry, args.fake)
    finally:
        await client.disconnect()


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    asyncio.run(main())
