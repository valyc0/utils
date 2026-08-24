from pathlib import Path

from serveo.config import parse_args


def test_defaults():
    cfg = parse_args([])
    assert cfg.ssh_port == 8086
    assert cfg.gateway_port == 8087
    assert cfg.bind == '0.0.0.0'
    assert cfg.host_key_path == Path('~/.serveo/host_key').expanduser()


def test_overrides():
    cfg = parse_args(['--ssh-port', '2222', '--gateway-port', '9090',
                      '--bind', '127.0.0.1', '--host-key', '/tmp/hk'])
    assert cfg.ssh_port == 2222
    assert cfg.gateway_port == 9090
    assert cfg.bind == '127.0.0.1'
    assert cfg.host_key_path == Path('/tmp/hk')
