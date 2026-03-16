"""
Helper de un solo uso: lista todos los canales y grupos donde estás,
con su ID numérico. Úsalo para identificar el WHITELIST_CHANNELS.

Uso:
    python src/listener/list_channels.py
"""
import asyncio
import os

from dotenv import load_dotenv
from telethon import TelegramClient
from telethon.tl.types import Channel, Chat

load_dotenv()

API_ID = int(os.environ["TELEGRAM_API_ID"])
API_HASH = os.environ["TELEGRAM_API_HASH"]
SESSION = os.environ.get("TELEGRAM_SESSION", "bot_session")

client = TelegramClient(SESSION, API_ID, API_HASH)


async def main() -> None:
    await client.start()
    print(f"\n{'ID':<25} {'Tipo':<12} {'Nombre'}")
    print("-" * 70)
    async for dialog in client.iter_dialogs():
        entity = dialog.entity
        if isinstance(entity, (Channel, Chat)):
            tipo = "Canal" if getattr(entity, "broadcast", False) else "Grupo"
            print(f"{dialog.id:<25} {tipo:<12} {dialog.name}")
    print()
    await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
