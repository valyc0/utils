from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys
from pathlib import Path

import asyncssh

from .config import Config, parse_args
from .gateway import Gateway
from .registry import TunnelRegistry
from .ssh_server import ServeoSSHServer


def load_or_create_host_key(path: Path) -> asyncssh.SSHKey:
    if path.exists():
        return asyncssh.read_private_key(str(path))
    path.parent.mkdir(parents=True, exist_ok=True)
    key = asyncssh.generate_private_key('ssh-ed25519', 'serveo host key')
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'wb') as fh:
        fh.write(key.export_private_key('openssh'))
    return key


async def start_servers(config: Config):
    key = load_or_create_host_key(config.host_key_path)
    registry = TunnelRegistry()
    ssh_acceptor = await asyncssh.create_server(
        lambda: ServeoSSHServer(registry, config),
        config.bind, config.ssh_port,
        server_host_keys=[key])
    gateway = await Gateway.start(registry, config.bind, config.gateway_port)
    print('serveo in ascolto')
    print(f'  SSH:     {config.bind}:{config.ssh_port}')
    print(f'  Gateway: {config.bind}:{config.gateway_port}')
    print(f'\nUso:  ssh -p {config.ssh_port} '
          f'-R PORTA:localhost:PORTA <questo-host>')
    print(f'Poi raggiungi il servizio su questo host, porta '
          f'{config.gateway_port}.')
    return ssh_acceptor, gateway


async def run(config: Config) -> None:
    ssh_acceptor, gateway = await start_servers(config)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    await stop.wait()
    await gateway.close()
    ssh_acceptor.close()
    await ssh_acceptor.wait_closed()


def main() -> None:
    logging.basicConfig(level=logging.INFO,
                        format='%(asctime)s %(levelname)s %(message)s')
    config = parse_args(sys.argv[1:])
    try:
        asyncio.run(run(config))
    except KeyboardInterrupt:
        pass
    except OSError as exc:
        print(f'Errore di avvio: {exc}', file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
