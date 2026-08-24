from serveo.registry import TunnelRegistry


class FakeConn:
    pass


def test_set_and_get():
    reg = TunnelRegistry()
    conn = FakeConn()
    reg.set(conn, '', 1522)
    t = reg.get()
    assert t is not None
    assert t.conn is conn
    assert t.listen_host == ''
    assert t.listen_port == 1522


def test_get_empty():
    assert TunnelRegistry().get() is None


def test_replace_returns_previous():
    reg = TunnelRegistry()
    c1, c2 = FakeConn(), FakeConn()
    reg.set(c1, '', 1000)
    prev = reg.set(c2, '', 2000)
    assert prev.conn is c1 and prev.listen_port == 1000
    assert reg.get().conn is c2


def test_clear_other_conn_keeps_tunnel():
    reg = TunnelRegistry()
    reg.set(FakeConn(), '', 1522)
    assert reg.clear_if_conn(FakeConn()) is False
    assert reg.get() is not None


def test_clear_same_conn_removes():
    reg = TunnelRegistry()
    conn = FakeConn()
    reg.set(conn, '', 1522)
    assert reg.clear_if_conn(conn) is True
    assert reg.get() is None
    assert reg.clear_if_conn(conn) is False
