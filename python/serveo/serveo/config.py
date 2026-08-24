from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Config:
    ssh_port: int
    gateway_port: int
    bind: str
    host_key_path: Path


def parse_args(argv: list[str] | None = None) -> Config:
    parser = argparse.ArgumentParser(
        prog='serveo',
        description='Reverse tunnel SSH con porta gateway unica.')
    parser.add_argument('--ssh-port', type=int, default=8086,
                        help='porta del server SSH (default: 8086)')
    parser.add_argument('--gateway-port', type=int, default=8087,
                        help='porta pubblica di ingresso traffico (default: 8087)')
    parser.add_argument('--bind', default='0.0.0.0',
                        help='indirizzo di ascolto (default: 0.0.0.0)')
    parser.add_argument('--host-key', default='~/.serveo/host_key',
                        help='percorso della host key (default: ~/.serveo/host_key)')
    args = parser.parse_args(argv)
    return Config(
        ssh_port=args.ssh_port,
        gateway_port=args.gateway_port,
        bind=args.bind,
        host_key_path=Path(args.host_key).expanduser(),
    )
