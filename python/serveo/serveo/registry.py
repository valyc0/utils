from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from asyncssh import SSHServerConnection


@dataclass(frozen=True)
class Tunnel:
    conn: SSHServerConnection
    listen_host: str
    listen_port: int


class TunnelRegistry:
    """Stato del tunnel attivo: una sola registrazione alla volta."""

    def __init__(self) -> None:
        self._tunnel: Tunnel | None = None

    def set(self, conn: SSHServerConnection, listen_host: str,
            listen_port: int) -> Tunnel | None:
        previous = self._tunnel
        self._tunnel = Tunnel(conn=conn, listen_host=listen_host,
                              listen_port=listen_port)
        return previous

    def get(self) -> Tunnel | None:
        return self._tunnel

    def clear_if_conn(self, conn: SSHServerConnection) -> bool:
        if self._tunnel is not None and self._tunnel.conn is conn:
            self._tunnel = None
            return True
        return False
