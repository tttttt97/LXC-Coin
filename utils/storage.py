"""持久化存储抽象层。

提供 JSON 文件和 SQLite 两种后端，通过 config.STORAGE_BACKEND 切换。
SQLite 模式下以增量 INSERT 方式逐块写入 blocks 表和 transactions 表。
"""

import json
import os
import sqlite3
import threading
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional


class BaseStorage(ABC):
    """存储抽象基类。"""

    @abstractmethod
    def save_chain(self, chain: List[Dict[str, Any]]) -> None:
        """持久化整条链（全量覆盖）。"""

    @abstractmethod
    def save_block(self, block: Dict[str, Any]) -> None:
        """增量追加单个区块。"""

    @abstractmethod
    def load_chain(self) -> List[Dict[str, Any]]:
        """从持久化介质读取整条链。"""

    @abstractmethod
    def save_wallet(self, public_key: str, private_key: str) -> None:
        """持久化钱包密钥对。"""

    @abstractmethod
    def load_wallet(self) -> Optional[Dict[str, str]]:
        """读取钱包密钥对。"""


class JsonFileStorage(BaseStorage):
    """基于 JSON 文件的存储实现。

    Attributes:
        chain_path: 链数据文件路径。
        wallet_path: 钱包文件路径。
    """

    def __init__(self, chain_path: str, wallet_path: str) -> None:
        self.chain_path = chain_path
        self.wallet_path = wallet_path
        self._lock = threading.Lock()

    def save_chain(self, chain: List[Dict[str, Any]]) -> None:
        with self._lock:
            with open(self.chain_path, 'w', encoding='utf-8') as f:
                json.dump({'chain': chain}, f, indent=4, ensure_ascii=False)

    def save_block(self, block: Dict[str, Any]) -> None:
        """JSON 后端下增量追加 = 全量覆盖写。"""
        pass

    def load_chain(self) -> List[Dict[str, Any]]:
        if not os.path.exists(self.chain_path):
            return []
        try:
            with open(self.chain_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
                return data.get('chain', [])
        except (json.JSONDecodeError, OSError):
            return []

    def save_wallet(self, public_key: str, private_key: str) -> None:
        with self._lock:
            with open(self.wallet_path, 'w', encoding='utf-8') as f:
                json.dump(
                    {'public_key': public_key, 'private_key': private_key},
                    f, indent=4, ensure_ascii=False,
                )

    def load_wallet(self) -> Optional[Dict[str, str]]:
        if not os.path.exists(self.wallet_path):
            return None
        try:
            with open(self.wallet_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return None


class SqliteStorage(BaseStorage):
    """基于 SQLite 的增量写入存储实现。

    使用 blocks 表和 transactions 表，每笔交易和每个区块
    各自一行记录，避免全量覆盖写。
    """

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self._lock = threading.Lock()
        self._init_db()

    def _init_db(self) -> None:
        with self._lock:
            conn = sqlite3.connect(self.db_path)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute(
                """CREATE TABLE IF NOT EXISTS blocks (
                       id INTEGER PRIMARY KEY AUTOINCREMENT,
                       block_index INTEGER UNIQUE NOT NULL,
                       timestamp REAL NOT NULL,
                       nonce INTEGER NOT NULL,
                       prev_hash TEXT NOT NULL,
                       hash TEXT NOT NULL,
                       merkle_root TEXT DEFAULT ''
                   )"""
            )
            conn.execute(
                """CREATE TABLE IF NOT EXISTS transactions (
                       id INTEGER PRIMARY KEY AUTOINCREMENT,
                       block_index INTEGER NOT NULL,
                       txid TEXT UNIQUE NOT NULL,
                       sender TEXT NOT NULL,
                       receiver TEXT NOT NULL,
                       amount REAL NOT NULL,
                       fee REAL DEFAULT 0,
                       nonce TEXT DEFAULT '',
                       signature TEXT DEFAULT '',
                       FOREIGN KEY (block_index) REFERENCES blocks(block_index)
                   )"""
            )
            conn.execute(
                """CREATE TABLE IF NOT EXISTS wallet_data (
                       id INTEGER PRIMARY KEY CHECK(id=1),
                       public_key TEXT NOT NULL,
                       private_key TEXT NOT NULL
                   )"""
            )
            conn.commit()
            conn.close()

    def _clear_all(self) -> None:
        conn = sqlite3.connect(self.db_path)
        conn.execute("DELETE FROM transactions")
        conn.execute("DELETE FROM blocks")
        conn.commit()
        conn.close()

    def save_chain(self, chain: List[Dict[str, Any]]) -> None:
        """全量覆盖（仅 sync 时使用）。"""
        with self._lock:
            self._clear_all()
            for block in chain:
                self._insert_block(block)

    def save_block(self, block: Dict[str, Any]) -> None:
        """增量写入单个区块及其交易。"""
        with self._lock:
            self._insert_block(block)

    def _insert_block(self, block: Dict[str, Any]) -> None:
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            """INSERT OR REPLACE INTO blocks
               (block_index, timestamp, nonce, prev_hash, hash, merkle_root)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                block['index'], block['timestamp'], block['nonce'],
                block['prev_hash'], block['hash'], block.get('merkle_root', ''),
            ),
        )
        txs = block.get('transactions', [])
        for tx in txs:
            conn.execute(
                """INSERT OR REPLACE INTO transactions
                   (block_index, txid, sender, receiver, amount, fee, nonce, signature)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    block['index'], tx.get('txid', ''), tx.get('sender', ''),
                    tx.get('receiver', ''), tx.get('amount', 0), tx.get('fee', 0),
                    tx.get('nonce', ''), tx.get('signature', ''),
                ),
            )
        conn.commit()
        conn.close()

    def load_chain(self) -> List[Dict[str, Any]]:
        try:
            conn = sqlite3.connect(self.db_path)
            block_rows = conn.execute(
                "SELECT block_index, timestamp, nonce, prev_hash, hash, merkle_root "
                "FROM blocks ORDER BY block_index"
            ).fetchall()
            chain = []
            for brow in block_rows:
                bi, ts, nonce, prev_h, h, mk = brow
                tx_rows = conn.execute(
                    "SELECT txid, sender, receiver, amount, fee, nonce, signature "
                    "FROM transactions WHERE block_index=? ORDER BY id",
                    (bi,),
                ).fetchall()
                txs = []
                for trow in tx_rows:
                    txs.append({
                        'txid': trow[0], 'sender': trow[1], 'receiver': trow[2],
                        'amount': trow[3], 'fee': trow[4], 'nonce': trow[5],
                        'signature': trow[6],
                    })
                chain.append({
                    'index': bi, 'timestamp': ts, 'nonce': nonce,
                    'prev_hash': prev_h, 'hash': h, 'merkle_root': mk,
                    'transactions': txs,
                })
            conn.close()
            return chain
        except (sqlite3.DatabaseError, json.JSONDecodeError):
            return []

    def save_wallet(self, public_key: str, private_key: str) -> None:
        with self._lock:
            conn = sqlite3.connect(self.db_path)
            conn.execute(
                "INSERT OR REPLACE INTO wallet_data (id, public_key, private_key) VALUES (1, ?, ?)",
                (public_key, private_key),
            )
            conn.commit()
            conn.close()

    def load_wallet(self) -> Optional[Dict[str, str]]:
        try:
            conn = sqlite3.connect(self.db_path)
            row = conn.execute(
                "SELECT public_key, private_key FROM wallet_data WHERE id=1"
            ).fetchone()
            conn.close()
            if row:
                return {'public_key': row[0], 'private_key': row[1]}
            return None
        except (sqlite3.DatabaseError, IndexError):
            return None
