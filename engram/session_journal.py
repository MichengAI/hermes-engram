"""插件身份日志：只保存服务端确认的会话 ID 和摘要哈希状态，不保存正文。"""
from __future__ import annotations

import sqlite3
import threading
from contextlib import closing
from pathlib import Path


class SessionJournal:
    """按 Hermes profile 和 Engram 数据目录隔离的 SQLite 日志，写入时才建文件。"""
    def __init__(self, path: Path, namespace: str) -> None:
        self.path, self.namespace = path, namespace
        self._lock = threading.RLock()

    def _open(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path, timeout=2)
        conn.execute("CREATE TABLE IF NOT EXISTS identities (namespace TEXT, sid TEXT, project TEXT, root TEXT, effective TEXT, PRIMARY KEY(namespace,sid,project))")
        return conn

    def get(self, sid: str, project: str) -> tuple[str, str] | None:
        """读取已确认身份；不存在时不创建日志。"""
        if not self.path.is_file():
            return None
        with self._lock, closing(self._open()) as conn, conn:
            row = conn.execute("SELECT root,effective FROM identities WHERE namespace=? AND sid=? AND project=?",
                               (self.namespace, sid, project)).fetchone()
        return (str(row[0]), str(row[1])) if row else None

    def put(self, sid: str, project: str, root: str, effective: str) -> None:
        """服务端确认之后原子记录，恢复时仍需再次确认归属。"""
        with self._lock, closing(self._open()) as conn, conn:
            conn.execute("INSERT INTO identities VALUES (?,?,?,?,?) ON CONFLICT(namespace,sid,project) DO UPDATE SET root=excluded.root,effective=excluded.effective",
                         (self.namespace, sid, project, root, effective))
