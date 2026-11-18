"""Один раз запусти, чтобы получить SESSION_STRING."""
import asyncio

from telethon import TelegramClient
from telethon.sessions import StringSession


async def main():
    api_id = int(input("API_ID: ").strip())
    api_hash = input("API_HASH: ").strip()
    client = TelegramClient(StringSession(), api_id, api_hash)
    await client.start()  # спросит номер, затем код (приходит в приложение Telegram, чат «Telegram»)
    print("\nSESSION_STRING (никому не показывай, это полный доступ к аккаунту):\n")
    print(client.session.save())
    await client.disconnect()


asyncio.run(main())
