from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Optional, Tuple

import asyncssh

from .config import Config
from .registry import TunnelRegistry

if TYPE_CHECKING:
    from asyncssh import SSHServerConnection
    from asyncssh import DataType

log = logging.getLogger(__name__)


class VirtualListener(asyncssh.SSHListener):
    """Listener fittizio: successo per il client senza bindare porte."""

    def __init__(self, listen_port: int) -> None:
        super().__init__()
        self._listen_port = listen_port

    def get_port(self) -> int:
        return self._listen_port

    def close(self) -> None:
        # Override obbligatorio: SSHListener.close() chiuderebbe l'intera
        # connessione SSH (chiama self._tunnel.close()).
        pass

    async def wait_closed(self) -> None:
        return None


class InfoSession(asyncssh.SSHServerSession):
    """Sessione minimale che stampa lo stato del tunnel sul terminale client."""

    def __init__(self, message: str) -> None:
        self._message = message
        self._chan: Optional[asyncssh.SSHServerChannel] = None

    def connection_made(self, chan: asyncssh.SSHServerChannel) -> None:
        self._chan = chan

    def pty_requested(self, term_type: str,
                      term_size: Tuple[int, int, int, int],
                      term_modes: dict) -> bool:
        return True

    def shell_requested(self) -> bool:
        return True

    def session_started(self) -> None:
        if self._chan is not None:
            self._chan.write(self._message)

    def data_received(self, data: str,
                      datatype: Optional[DataType]) -> None:
        pass

    def eof_received(self) -> None:
        pass


class ServeoSSHServer(asyncssh.SSHServer):
    """Server SSH anonimo che registra tunnel virtuali dalle richieste -R."""

    def __init__(self, registry: TunnelRegistry, config: Config) -> None:
        super().__init__()
        self._registry = registry
        self._config = config
        self._conn: Optional[SSHServerConnection] = None
        self._last_forward: Optional[Tuple[str, int]] = None

    def connection_made(self, conn: SSHServerConnection) -> None:
        self._conn = conn

    def begin_auth(self, username: str) -> bool:
        return False

    def server_requested(self, listen_host: str,
                         listen_port: int) -> VirtualListener:
        assert self._conn is not None
        previous = self._registry.set(self._conn, listen_host, listen_port)
        if previous is not None:
            log.info('Tunnel %s:%s sostituito da %s:%s',
                     previous.listen_host, previous.listen_port,
                     listen_host, listen_port)
        log.info('Tunnel registrato: %s:%s', listen_host, listen_port)
        self._last_forward = (listen_host, listen_port)
        return VirtualListener(listen_port)

    def session_requested(self) -> 'InfoSessionFactory':
        return lambda: InfoSession(self._banner())

    def _banner(self) -> str:
        if self._last_forward is not None:
            _, port = self._last_forward
            head = (f'Tunnel attivo: raggiungi il tuo servizio su '
                    f'questo host porta {self._config.gateway_port} '
                    f'(inoltrato al tuo localhost:{port})')
        else:
            head = ('Nessun tunnel registrato. Usa: '
                    f'ssh -p {self._config.ssh_port} '
                    f'-R PORTA:localhost:PORTA <host>')
        return ('\r\n' + head +
                '\r\nIl tunnel resta attivo finche questa sessione e aperta.'
                '\r\n\r\n')

    def connection_lost(self, exc: Optional[Exception]) -> None:
        if self._conn is not None and \
                self._registry.clear_if_conn(self._conn):
            log.info('Connessione chiusa: tunnel rimosso')
