import asyncssh

from serveo.config import Config
from serveo.registry import TunnelRegistry
from serveo.ssh_server import InfoSession, ServeoSSHServer, VirtualListener


def make_config(tmp_path):
    return Config(ssh_port=22331, gateway_port=22887, bind='127.0.0.1',
                  host_key_path=tmp_path / 'hk')


def test_virtual_listener_no_op_close():
    listener = VirtualListener(1522)
    assert listener.get_port() == 1522
    listener.close()
    import inspect
    assert inspect.iscoroutinefunction(listener.wait_closed)


def test_begin_auth_skipped(tmp_path):
    server = ServeoSSHServer(TunnelRegistry(), make_config(tmp_path))
    assert server.begin_auth('anyuser') is False


def test_server_requested_registers_tunnel(tmp_path):
    class FakeConn:
        pass

    reg = TunnelRegistry()
    config = make_config(tmp_path)
    server = ServeoSSHServer(reg, config)
    conn = FakeConn()
    server.connection_made(conn)

    listener = server.server_requested('', 1522)
    assert isinstance(listener, asyncssh.SSHListener)
    assert isinstance(listener, VirtualListener)
    assert listener.get_port() == 1522

    tunnel = reg.get()
    assert tunnel is not None
    assert tunnel.conn is conn
    assert tunnel.listen_port == 1522

    server.connection_lost(None)
    assert reg.get() is None


def test_session_requested_returns_session(tmp_path):
    server = ServeoSSHServer(TunnelRegistry(), make_config(tmp_path))
    session = server.session_requested()
    assert isinstance(session, InfoSession)
    assert hasattr(session, 'connection_made')
