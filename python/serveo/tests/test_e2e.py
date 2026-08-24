import asyncio

import asyncssh
import pytest

from serveo.__main__ import start_servers
from serveo.config import Config

SSH_PORT = 22331
GW_PORT = 22887


@pytest.fixture
async def servers(tmp_path):
    config = Config(ssh_port=SSH_PORT, gateway_port=GW_PORT,
                    bind='127.0.0.1', host_key_path=tmp_path / 'hk')
    ssh_acceptor, gateway = await start_servers(config)
    yield
    await gateway.close()
    ssh_acceptor.close()
    await ssh_acceptor.wait_closed()


async def _echo(reader: asyncio.StreamReader,
                writer: asyncio.StreamWriter) -> None:
    try:
        while True:
            data = await reader.read(1024)
            if not data:
                break
            writer.write(data)
            await writer.drain()
    finally:
        writer.close()


async def _start_echo_server():
    server = await asyncio.start_server(_echo, '127.0.0.1', 0)
    return server, server.sockets[0].getsockname()[1]


async def test_tcp_roundtrip_through_gateway(servers):
    echo, echo_port = await _start_echo_server()
    async with asyncssh.connect('127.0.0.1', port=SSH_PORT,
                                known_hosts=None) as conn:
        listener = await conn.forward_remote_port('', 15222, 'localhost',
                                                  echo_port)
        reader, writer = await asyncio.open_connection('127.0.0.1', GW_PORT)
        payload = b'ping-through-tunnel'
        writer.write(payload)
        await writer.drain()
        received = await asyncio.wait_for(
            reader.readexactly(len(payload)), timeout=5)
        assert received == payload
        writer.close()
        listener.close()
    echo.close()


async def test_gateway_rejects_without_tunnel(servers):
    reader, writer = await asyncio.open_connection('127.0.0.1', GW_PORT)
    data = await asyncio.wait_for(reader.read(64), timeout=5)
    assert data == b''
    writer.close()


async def test_cleanup_after_disconnect(servers):
    echo, echo_port = await _start_echo_server()
    conn = await asyncssh.connect('127.0.0.1', port=SSH_PORT,
                                  known_hosts=None)
    listener = await conn.forward_remote_port('', 15223, 'localhost',
                                              echo_port)
    conn.close()
    await conn.wait_closed()
    await asyncio.sleep(0.2)

    reader, writer = await asyncio.open_connection('127.0.0.1', GW_PORT)
    data = await asyncio.wait_for(reader.read(64), timeout=5)
    assert data == b''
    writer.close()
    listener.close()
    echo.close()
