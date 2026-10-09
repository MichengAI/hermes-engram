"""身份日志的连接释放与命名空间隔离回归。"""
import sqlite3

from engram.session_journal import SessionJournal


def test_journal_closes_every_connection(tmp_path, monkeypatch):
    original = sqlite3.connect
    opened, closed = [], []
    class Tracked(sqlite3.Connection):
        def close(self):
            closed.append(id(self))
            super().close()
    def connect(*args, **kwargs):
        kwargs["factory"] = Tracked
        connection = original(*args, **kwargs)
        opened.append(connection)
        return connection
    monkeypatch.setattr(sqlite3, "connect", connect)
    journal = SessionJournal(tmp_path / "identity.db", "data-a")
    try:
        journal.put("s1", "a", "root", "effective")
        assert journal.get("s1", "a") == ("root", "effective")
        assert len(opened) == len(closed) == 2
    finally:
        for connection in opened:
            connection.close()


def test_journal_namespaces_are_isolated(tmp_path):
    path = tmp_path / "identity.db"
    a, b = SessionJournal(path, "a"), SessionJournal(path, "b")
    a.put("s1", "project", "root-a", "effective-a")
    b.put("s1", "project", "root-b", "effective-b")
    assert a.get("s1", "project") == ("root-a", "effective-a")
    assert b.get("s1", "project") == ("root-b", "effective-b")
