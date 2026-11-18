"""Личный агент в Telegram (Telethon userbot).
Команды пишешь текстом себе в «Избранное». ИИ разбирает команду и вызывает функции пересылки.
Умеет: правила автопересылки медиа из каналов в тему (раздел) мульти-группы,
список/удаление правил, пересылка последних N медиа из канала.
"""
import asyncio
import json
import os
import random

import httpx
from telethon import TelegramClient, events, functions, utils
from telethon.sessions import StringSession

API_ID = int(os.environ["API_ID"])
API_HASH = os.environ["API_HASH"]
SESSION = os.environ["SESSION_STRING"]
GEMINI_KEY = os.environ["GEMINI_API_KEY"]
MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
DELAY = float(os.getenv("FORWARD_DELAY", "1.5"))  # пауза между пересылками, защита от лимитов
RULES_FILE = "rules.json"
MARK = "🤖 "

client = TelegramClient(StringSession(SESSION), API_ID, API_HASH)
pending = None  # действие, ждущее подтверждения "да"

SYSTEM = """Ты разбираешь команды для Telegram-агента. Верни ТОЛЬКО JSON вида {"action": "...", "args": {...}}.
Действия:
- add_rule: args {source: название/@username канала-источника, target: название группы-приёмника, topic: название раздела (темы) или null, media: "video" | "photo" | "all"}. Автопересылка новых медиа.
- list_rules: args {}
- remove_rule: args {id: число}
- forward_history: args {source: название канала, target: название группы, topic: название раздела или null, media: "video"|"photo"|"all", limit: число}. Переслать последние N медиа из канала один раз.
- say: args {text: ответ пользователю}, если команда непонятна или это не про пересылку.
Если media не указано, ставь "all". Если раздел не указан, topic = null."""


def load_rules():
    if os.path.exists(RULES_FILE):
        with open(RULES_FILE, encoding="utf-8") as f:
            return json.load(f)
    return []


def save_rules(rules):
    with open(RULES_FILE, "w", encoding="utf-8") as f:
        json.dump(rules, f, ensure_ascii=False, indent=2)


async def say(text):
    await client.send_message("me", MARK + text)


async def ask_llm(text):
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent"
    body = {
        "contents": [{"parts": [{"text": SYSTEM + "\n\nКоманда: " + text}]}],
        "generationConfig": {"responseMimeType": "application/json", "temperature": 0},
    }
    async with httpx.AsyncClient(timeout=60) as h:
        r = await h.post(url, json=body, headers={"x-goog-api-key": GEMINI_KEY})
    r.raise_for_status()
    return json.loads(r.json()["candidates"][0]["content"]["parts"][0]["text"])


async def resolve_chat(query):
    q = query.strip()
    if q.startswith("@") or "t.me/" in q:
        return await client.get_entity(q)
    async for d in client.iter_dialogs():
        if q.lower() in (d.name or "").lower():
            return d.entity
    return None


async def resolve_topic(target, title, create=True):
    """Возвращает id темы. None = общая тема (General)."""
    if not title:
        return None
    res = await client(functions.channels.GetForumTopicsRequest(
        channel=target, offset_date=None, offset_id=0, offset_topic=0, limit=100))
    for t in res.topics:
        if getattr(t, "title", "").lower() == title.lower():
            return t.id
    if not create:
        return None
    upd = await client(functions.channels.CreateForumTopicRequest(
        channel=target, title=title, random_id=random.randrange(2**31)))
    for u in upd.updates:
        m = getattr(u, "message", None)
        if m is not None:
            return m.id
    return None


def media_kind(msg):
    if msg.photo:
        return "photo"
    if msg.video or msg.video_note:
        return "video"
    if msg.document and (msg.document.mime_type or "").startswith("video"):
        return "video"
    return None


def wanted(msg, media):
    k = media_kind(msg)
    return k is not None and media in ("all", k)


async def forward(msg, target_id, topic_id):
    await client(functions.messages.ForwardMessagesRequest(
        from_peer=await msg.get_input_chat(),
        id=[msg.id],
        random_id=[random.randrange(2**63)],
        to_peer=await client.get_input_entity(target_id),
        top_msg_id=topic_id,
        drop_author=True,  # без подписи «переслано из»; поставь False, чтобы оставлять источник
    ))


async def do_add_rule(a):
    src, tgt = await resolve_chat(a["source"]), await resolve_chat(a["target"])
    if not src or not tgt:
        return await say("Не нашёл источник или группу. Проверь названия или дай @username.")
    topic = await resolve_topic(tgt, a.get("topic"))
    rules = load_rules()
    rid = max([r["id"] for r in rules], default=0) + 1
    rules.append({
        "id": rid, "source": utils.get_peer_id(src), "source_title": utils.get_display_name(src),
        "target": utils.get_peer_id(tgt), "target_title": utils.get_display_name(tgt),
        "topic": topic, "topic_title": a.get("topic"), "media": a.get("media") or "all",
    })
    save_rules(rules)
    await say(f"Правило #{rid}: {utils.get_display_name(src)} → {utils.get_display_name(tgt)}"
              f"{' / ' + a['topic'] if a.get('topic') else ''} ({a.get('media') or 'all'})")


async def do_list():
    rules = load_rules()
    if not rules:
        return await say("Правил нет.")
    await say("\n".join(
        f"#{r['id']}: {r['source_title']} → {r['target_title']}"
        f"{' / ' + r['topic_title'] if r.get('topic_title') else ''} ({r['media']})" for r in rules))


async def do_remove(a):
    rules = load_rules()
    new = [r for r in rules if r["id"] != int(a["id"])]
    save_rules(new)
    await say("Удалил." if len(new) < len(rules) else "Такого правила нет.")


async def do_history(a):
    src, tgt = await resolve_chat(a["source"]), await resolve_chat(a["target"])
    if not src or not tgt:
        return await say("Не нашёл источник или группу.")
    topic = await resolve_topic(tgt, a.get("topic"))
    limit = min(int(a.get("limit") or 20), 500)
    media = a.get("media") or "all"
    msgs = []
    async for m in client.iter_messages(src, limit=limit * 4):
        if wanted(m, media):
            msgs.append(m)
        if len(msgs) >= limit:
            break
    for m in reversed(msgs):  # от старых к новым
        await forward(m, utils.get_peer_id(tgt), topic)
        await asyncio.sleep(DELAY)
    await say(f"Переслал {len(msgs)} шт.")


async def run_action(act):
    name, a = act["action"], act.get("args", {})
    if name == "add_rule":
        await do_add_rule(a)
    elif name == "list_rules":
        await do_list()
    elif name == "remove_rule":
        await do_remove(a)
    elif name == "forward_history":
        await do_history(a)
    else:
        await say(a.get("text") or "Не понял команду.")


@client.on(events.NewMessage)
async def handler(event):
    global pending
    me = await client.get_me()
    # 1) команды из «Избранного»
    if event.chat_id == me.id:
        text = event.raw_text or ""
        if not text or text.startswith(MARK):
            return
        try:
            if text.lower().strip() in ("да", "yes") and pending:
                act, pending = pending, None
                return await run_action(act)
            act = await ask_llm(text)
            if act["action"] == "forward_history" and int(act["args"].get("limit") or 20) > 50:
                pending = act
                return await say(f"Переслать {act['args'].get('limit')} шт.? Напиши «да».")
            await run_action(act)
        except Exception as e:
            await say(f"Ошибка: {e}")
        return
    # 2) автопересылка по правилам
    for r in load_rules():
        if r["source"] == event.chat_id and wanted(event.message, r["media"]):
            try:
                await forward(event.message, r["target"], r["topic"])
                await asyncio.sleep(DELAY)
            except Exception as e:
                await say(f"Правило #{r['id']} не сработало: {e}")


async def main():
    await client.start()
    await say("Агент запущен. Пиши команды сюда.")
    await client.run_until_disconnected()


asyncio.run(main())
