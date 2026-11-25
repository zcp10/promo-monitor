"""Lê os grupos de promoção como a SUA conta (Telethon / MTProto), sem bot nos grupos.

Somente leitura: não marca como lido, não envia nada nos grupos.
Na primeira execução o Telethon pede seu telefone e o código do Telegram;
depois a sessão fica salva no arquivo <TG_SESSION>.session (guarde-o com cuidado:
ele dá acesso à sua conta).
"""
from __future__ import annotations

import asyncio
import logging

from .pipeline import Pipeline

log = logging.getLogger(__name__)


def _client(settings):
    from telethon import TelegramClient

    s = settings.secrets
    if not (s.tg_api_id and s.tg_api_hash):
        raise SystemExit("Configure TG_API_ID e TG_API_HASH no .env (https://my.telegram.org).")
    return TelegramClient(s.tg_session, s.tg_api_id, s.tg_api_hash)


def _chat_ref(value):
    value = str(value).strip()
    return int(value) if value.lstrip("-").isdigit() else value


async def list_chats(settings) -> None:
    async with _client(settings) as client:
        async for dialog in client.iter_dialogs():
            if dialog.is_group or dialog.is_channel:
                print(f"{dialog.id:>16}  {dialog.name}")


async def backfill(settings, pipeline: Pipeline, limit: int = 30, groups: list | None = None) -> None:
    """Processa as últimas N mensagens de cada grupo (útil para testar)."""
    async with _client(settings) as client:
        for ref in groups or settings.get("telegram.groups", []) or []:
            entity = await client.get_entity(_chat_ref(ref))
            title = getattr(entity, "title", str(ref))
            async for msg in client.iter_messages(entity, limit=limit):
                if msg.message:
                    await pipeline.handle_text(msg.message, chat_id=msg.chat_id, chat_title=title,
                                               message_id=msg.id, posted_at=msg.date)


async def listen(settings, pipeline: Pipeline) -> None:
    from telethon import events

    groups = [_chat_ref(g) for g in settings.get("telegram.groups", []) or []]
    if not groups:
        raise SystemExit("Nenhum grupo em telegram.groups (config/settings.yaml).")

    client = _client(settings)
    await client.start()
    queue: asyncio.Queue = asyncio.Queue(maxsize=500)

    @client.on(events.NewMessage(chats=groups))
    async def on_message(event):  # só enfileira: a análise roda no worker
        if event.raw_text:
            chat = await event.get_chat()
            try:
                queue.put_nowait((event.raw_text, event.chat_id, getattr(chat, "title", ""),
                                  event.id, event.date))
            except asyncio.QueueFull:
                log.warning("Fila cheia; post descartado.")

    async def worker():
        while True:
            text, chat_id, title, msg_id, date = await queue.get()
            try:
                await pipeline.handle_text(text, chat_id=chat_id, chat_title=title,
                                           message_id=msg_id, posted_at=date)
            except Exception:
                log.exception("Erro ao processar post %s/%s", chat_id, msg_id)
            finally:
                queue.task_done()

    workers = [asyncio.create_task(worker()) for _ in range(2)]
    log.info("Ouvindo %d grupo(s). Ctrl+C para parar.", len(groups))
    try:
        await client.run_until_disconnected()
    finally:
        for w in workers:
            w.cancel()
