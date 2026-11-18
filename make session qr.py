"""Вход по QR-коду без кода из Telegram. Запусти, отсканируй QR телефоном."""
import asyncio
import os

import qrcode
from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError
from telethon.sessions import StringSession


def show(url):
    qr = qrcode.QRCode(border=1)
    qr.add_data(url)
    qr.print_ascii(invert=bool(os.getenv("INVERT")))


async def main():
    api_id = int(input("API_ID: ").strip())
    api_hash = input("API_HASH: ").strip()
    client = TelegramClient(StringSession(), api_id, api_hash)
    await client.connect()
    qr = await client.qr_login()
    while True:
        show(qr.url)
        print("\nТелефон: Telegram → Настройки → Устройства → Подключить устройство → сканируй QR.")
        try:
            await qr.wait(30)
            break
        except asyncio.TimeoutError:
            await qr.recreate()
        except SessionPasswordNeededError:
            await client.sign_in(password=input("Пароль двухэтапной проверки: "))
            break
    print("\nSESSION_STRING (никому не показывай):\n")
    print(client.session.save())
    await client.disconnect()


asyncio.run(main())
