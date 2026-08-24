from __future__ import annotations

import asyncio
import logging

from .registry import TunnelRegistry

log = logging.getLogger(__name__)

_BUF_SIZE = 65536


async def _pump(reader: asyncio.StreamReader,
                writer: asyncio.StreamWriter) -> None:
    try:
        while True:
            data = await reader.read(_BUF_SIZE)
            if not data:
                break
            writer.write(data)
            await writer.drain()
    except (ConnectionError, OSError):
        pass


async def _close(writer: asyncio.StreamWriter) -> None:
    try:
        writer.close()
        await writer.wait_closed()
    except (ConnectionError, OSError):
        pass


class Gateway:
    """Unico punto di ingresso pubblico: instrada verso il tunnel attivo."""

    def __init__(self, server: asyncio.AbstractServer) -> None:
        self._server = server

    @classmethod
    async def start(cls, registry: TunnelRegistry, bind: str,
                    port: int) -> 'Gateway':
        server = await asyncio.start_server(
            lambda r, w: handle_client(r, w, registry), bind, port)
        log.info('Gateway in ascolto su %s:%s', bind, port)
        return cls(server)

    @property
    def port(self) -> int:
        return self._server.sockets[0].getsockname()[1]

    async def close(self) -> None:
        self._server.close()
        await self._server.wait_closed()


async def handle_client(reader: asyncio.StreamReader,
                        writer: asyncio.StreamWriter,
                        registry: TunnelRegistry) -> None:
    try:
        tunnel = registry.get()
        if tunnel is None:
            await _close(writer)
            return

        ssh_reader, ssh_writer = await tunnel.conn.open_connection(
            tunnel.listen_host, tunnel.listen_port)

        client_to_ssh = asyncio.create_task(_pump(reader, ssh_writer))
        ssh_to_client = asyncio.create_task(_pump(ssh_reader, writer))
        try:
            await asyncio.gather(client_to_ssh, ssh_to_client)
        finally:
            for task in (client_to_ssh, ssh_to_client):
                task.cancel()
            await _close(ssh_writer)
            await _close(writer)
        log.debug('Connessione tunnel completata')
    except Exception as exc:
        log.info('Connessione gateway rifiutata o fallita: %s', exc)
        await _close(writer)
