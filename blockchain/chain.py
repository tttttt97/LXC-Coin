import hashlib
import json
import time
import os
import threading
import logging
from decimal import Decimal, getcontext
from typing import Optional, Tuple, Union, List, Dict, Any
from urllib.parse import urlparse
import requests
from ecdsa import SigningKey, VerifyingKey, SECP256k1
from ecdsa.keys import BadSignatureError

from utils import get_hash, get_merkle_root
from utils.storage import BaseStorage, JsonFileStorage, SqliteStorage
import config

getcontext().prec = config.DECIMAL_PRECISION
logger = logging.getLogger(__name__)


class Blockchain:
    """核心区块链数据结构。

    负责链上数据存储、交易验证、挖矿共识与 P2P 节点同步。
    所有涉及链状态的写操作均受 `self.lock` 保护。
    内建 O(1) 级别的余额缓存和状态机重演引擎。

    Attributes:
        chain: 区块列表，按高度升序排列。
        current_transactions: 待打包交易内存池。
        nodes: 已发现的 P2P 邻居节点地址集合。
        node_public_key: 本节点矿工公钥。
        node_private_key: 本节点矿工私钥。
        nonce_map: 各账户已使用的最大 nonce，用于防重放。
        balance_cache: O(1) 级状态缓存，随新区块增量更新。
    """

    def __init__(self) -> None:
        self.chain: List[Dict[str, Any]] = []
        self.current_transactions: List[Dict[str, Any]] = []
        self.nodes: set = set()
        self.seed_nodes: List[str] = []
        self.port: Optional[int] = None
        self.storage: Optional[BaseStorage] = None
        self.node_public_key: Optional[str] = None
        self.node_private_key: Optional[str] = None
        self.lock = threading.RLock()
        self.nonce_map: Dict[str, int] = {}
        self.balance_cache: Dict[str, Decimal] = {}
        self._stats_cache: Optional[Dict[str, Any]] = None
        self._stats_cache_at: float = 0.0

    # ── 持久化 ──────────────────────────────────

    def _create_storage(self, port: int) -> BaseStorage:
        """根据 config 创建对应的存储后端实例。"""
        data_dir = config.DATA_DIR
        os.makedirs(data_dir, exist_ok=True)
        if config.STORAGE_BACKEND == "sqlite":
            db_path = os.path.join(data_dir, config.DB_FILE_TEMPLATE.format(port=port))
            return SqliteStorage(db_path=db_path)
        chain_path = os.path.join(data_dir, config.CHAIN_FILE_TEMPLATE.format(port=port))
        wallet_path = os.path.join(data_dir, config.WALLET_FILE_TEMPLATE.format(port=port))
        return JsonFileStorage(chain_path=chain_path, wallet_path=wallet_path)

    def init_storage(self, port: int) -> None:
        """初始化本地存储：加载/创建钱包文件，恢复链数据与余额缓存。"""
        self.port = port
        self.storage = self._create_storage(port)

        wallet_data = self.storage.load_wallet()
        if wallet_data:
            self.node_public_key = wallet_data['public_key']
            self.node_private_key = wallet_data['private_key']
        else:
            sk = SigningKey.generate(curve=SECP256k1)
            self.node_private_key = sk.to_string().hex()
            self.node_public_key = sk.verifying_key.to_string().hex()
            self.storage.save_wallet(self.node_public_key, self.node_private_key)

        self.load_chain()

    def save_chain(self) -> None:
        """全量覆盖持久化（仅 sync 时使用）。"""
        self.storage.save_chain(self.chain)

    def _save_block_incremental(self, block: Dict[str, Any]) -> None:
        """增量写入单个区块（挖矿产块时使用）。"""
        self.storage.save_block(block)

    def load_chain(self) -> None:
        """通过存储后端加载链数据，重建余额缓存和 nonce_map。"""
        data = self.storage.load_chain()
        if data:
            self.chain = data
            self._rebuild_caches()
        else:
            self.new_block(prev_hash='0', nonce=self.proof_of_work('0'))

    def _rebuild_caches(self) -> None:
        """O(N) 遍历全链重建余额缓存与 nonce_map。只在启动时执行一次。"""
        self.balance_cache.clear()
        self.nonce_map.clear()
        for block in self.chain:
            for tx in block['transactions']:
                sn = tx['sender']
                rc = tx['receiver']
                amt = Decimal(str(tx['amount']))
                fee = Decimal(str(tx.get('fee', 0)))
                if sn != '0':
                    self.nonce_map[sn] = max(
                        self.nonce_map.get(sn, 0),
                        int(tx['nonce'].split('-')[-1]),
                    )
                    self.balance_cache[sn] = self.balance_cache.get(sn, Decimal('0')) - (amt + fee)
                self.balance_cache[rc] = self.balance_cache.get(rc, Decimal('0')) + amt

    def _update_caches_for_block(self, block: Dict[str, Any]) -> None:
        """对单个区块增量更新余额缓存和 nonce_map。"""
        for tx in block['transactions']:
            sn = tx['sender']
            rc = tx['receiver']
            amt = Decimal(str(tx['amount']))
            fee = Decimal(str(tx.get('fee', 0)))
            if sn != '0':
                self.nonce_map[sn] = max(
                    self.nonce_map.get(sn, 0),
                    int(tx['nonce'].split('-')[-1]),
                )
                self.balance_cache[sn] = self.balance_cache.get(sn, Decimal('0')) - (amt + fee)
            self.balance_cache[rc] = self.balance_cache.get(rc, Decimal('0')) + amt

    # ── P2P 节点管理 ───────────────────────────

    def register_node(self, address: str) -> None:
        """将指定地址注册为已发现邻居节点。"""
        parsed = urlparse(address)
        self.nodes.add(parsed.netloc if parsed.netloc else parsed.path)

    def register_seed(self, address: str) -> None:
        """注册种子节点，用于 P2P 网络发现扩散。

        Args:
            address: 种子节点 HTTP 地址。
        """
        parsed = urlparse(address)
        node = parsed.netloc if parsed.netloc else parsed.path
        self.seed_nodes.append(node)
        self.nodes.add(node)

    # ── 链验证 ──────────────────────────────────

    def valid_chain(self, chain: List[Dict[str, Any]]) -> bool:
        """全量验证一条链的合法性，含状态机重演。

        校验项：创世区块哈希、每块 PoW、prev_hash 链接、coinbase 金额、
        每笔交易 ECDSA 签名、以及逐块重演余额（防止透支攻击）。

        Args:
            chain: 待验证的链（区块列表）。

        Returns:
            链合法返回 True，否则返回 False。
        """
        if not chain:
            logger.warning("valid_chain: 空链")
            return False
        last_block = chain[0]
        local_genesis_hash = get_hash({k: v for k, v in last_block.items() if k != 'hash'})
        if local_genesis_hash != last_block['hash']:
            logger.warning(
                "valid_chain: 创世块哈希不一致 (计算=%s, 存储=%s, 数据=%s)",
                local_genesis_hash, last_block['hash'],
                json.dumps({k: v for k, v in last_block.items() if k != 'hash'}, sort_keys=True),
            )
            return False
        if not self.valid_proof(last_block['prev_hash'], last_block['nonce']):
            logger.warning("valid_chain: 创世块 PoW 无效")
            return False

        virtual_balance: Dict[str, Decimal] = {}

        for idx, block in enumerate(chain[1:], start=1):
            if block['prev_hash'] != last_block['hash']:
                logger.warning("valid_chain: 区块 %d prev_hash 断裂", block.get('index', idx))
                return False
            if not self.valid_proof(block['prev_hash'], block['nonce']):
                logger.warning("valid_chain: 区块 %d PoW 无效", block.get('index', idx))
                return False
            if get_hash({k: v for k, v in block.items() if k != 'hash'}) != block['hash']:
                logger.warning("valid_chain: 区块 %d 哈希不匹配", block.get('index', idx))
                return False

            coinbase_count = 0
            block_fees = sum(
                Decimal(str(tx.get('fee', 0)))
                for tx in block['transactions']
                if tx['sender'] != '0'
            )

            for tx in block['transactions']:
                sn = tx['sender']
                rc = tx['receiver']
                amt = Decimal(str(tx['amount']))
                fee = Decimal(str(tx.get('fee', 0)))

                if sn == '0':
                    coinbase_count += 1
                    expected_reward = Decimal(config.MINING_REWARD) + block_fees
                    actual_amount = Decimal(str(tx['amount']))
                    if abs(actual_amount - expected_reward) > Decimal(str(config.FLOAT_TOLERANCE)):
                        logger.warning(
                            "valid_chain: 区块 %d coinbase 金额异常 (期望 %s, 实际 %s)",
                            block.get('index', idx), expected_reward, actual_amount,
                        )
                        return False
                else:
                    try:
                        vk = VerifyingKey.from_string(bytes.fromhex(sn), curve=SECP256k1)
                        message = (
                            f"{sn}->{rc}:{tx['amount']:.8f}"
                            f" fee:{tx['fee']:.8f} nonce:{tx['nonce']}"
                        )
                        msg_hash = hashlib.sha256(message.encode('utf-8')).digest()
                        if not vk.verify_digest(bytes.fromhex(tx['signature']), msg_hash):
                            logger.warning(
                                "valid_chain: 区块 %d 签名无效 %s->%s",
                                block.get('index', idx), sn[:10], rc[:10],
                            )
                            return False
                    except (ValueError, BadSignatureError, TypeError) as e:
                        logger.warning("valid_chain: 区块 %d 签名异常: %s", block.get('index', idx), e)
                        return False

                    virtual_balance[sn] = virtual_balance.get(sn, Decimal('0')) - (amt + fee)
                    if virtual_balance.get(sn, Decimal('0')) < 0:
                        logger.warning(
                            "valid_chain: 区块 %d 余额不足 %s (余额=%s 需=%s)",
                            block.get('index', idx), sn[:10],
                            virtual_balance[sn] + amt + fee, amt + fee,
                        )
                        return False

                virtual_balance[rc] = virtual_balance.get(rc, Decimal('0')) + amt

            if coinbase_count != 1:
                logger.warning(
                    "valid_chain: 区块 %d coinbase 计数异常 (%d)",
                    block.get('index', idx), coinbase_count,
                )
                return False
            last_block = block

        return True

    # ── 共识 ─────────────────────────────────────

    def _chain_total_difficulty(self, chain: List[Dict[str, Any]]) -> float:
        """计算累计工作量（难度固定时等价于链长 × 难度权重）。"""
        return len(chain) * 2 ** len(config.DIFFICULTY)

    @staticmethod
    def _is_safe_node(address: str) -> bool:
        """校验节点地址是否合法，防止 SSRF 攻击。
        
        仅允许 localhost / 127.0.0.1 及 config.HOST 的请求。
        """
        host = address.split(':')[0] if ':' in address else address
        return host in ('127.0.0.1', 'localhost', config.HOST)

    def resolve_conflicts(self) -> bool:
        """累计工作量共识：用已验证的更高累计工作量链替换本地链。

        Returns:
            若发生了链替换返回 True，否则返回 False。
        """
        local_difficulty = self._chain_total_difficulty(self.chain)
        new_chain = None
        best_difficulty = local_difficulty

        for node in list(self.nodes):
            if not self._is_safe_node(node):
                logger.warning("AUDIT: SSRF 阻断 | 目标=%s", node)
                continue
            try:
                r = requests.get(f'http://{node}/chain', timeout=config.SYNC_TIMEOUT)
                if r.status_code == 200:
                    chain_data = r.json()
                    chain = chain_data['chain']
                    remote_difficulty = self._chain_total_difficulty(chain)
                    if remote_difficulty > best_difficulty:
                        if self.valid_chain(chain):
                            best_difficulty = remote_difficulty
                            new_chain = chain
                            logger.info(
                                "发现更优链：本地 %d 块 (diff %.0f) → 远程 %d 块 (diff %.0f)",
                                len(self.chain), local_difficulty, len(chain), remote_difficulty,
                            )
                        else:
                            logger.warning(
                                "远程链验证失败：长度 %d 但 valid_chain 返回 False",
                                len(chain),
                            )
            except (requests.ConnectionError, requests.Timeout) as e:
                logger.debug("节点 %s 不可达: %s", node, e)
            except requests.RequestException as e:
                logger.debug("请求节点 %s 异常: %s", node, e)

        if new_chain:
            with self.lock:
                self.chain = new_chain
                self._rebuild_caches()
                self.save_chain()
            return True
        return False

    # ── 工作量证明 ──────────────────────────────

    def proof_of_work(self, last_hash: str) -> int:
        """对指定前驱哈希执行 SHA-256 暴力碰撞，寻找满足难度条件的 nonce。"""
        nonce = 0
        while not self.valid_proof(last_hash, nonce):
            nonce += 1
        return nonce

    @staticmethod
    def valid_proof(last_hash: str, nonce: int) -> bool:
        """验证 (last_hash, nonce) 对是否满足当前难度要求。"""
        guess = f'{last_hash}{nonce}'.encode('utf-8')
        return hashlib.sha256(guess).hexdigest()[:len(config.DIFFICULTY)] == config.DIFFICULTY

    # ── 区块生成 ────────────────────────────────

    def new_block(
        self, prev_hash: str, nonce: int, pending_txs: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """组装并上链一个新区块，增量更新余额缓存。"""
        with self.lock:
            tx_list = pending_txs if pending_txs else self.current_transactions
            block_data = {
                'index': len(self.chain) + 1,
                'timestamp': float(time.time()) if self.chain else 0.0,
                'transactions': tx_list,
                'nonce': nonce,
                'prev_hash': (
                    prev_hash
                    if prev_hash
                    else (self.chain[-1]['hash'] if self.chain else '0')
                ),
                'merkle_root': get_merkle_root(tx_list),
            }
            block_hash = get_hash(block_data)
            block = {**block_data, 'hash': block_hash}

            if pending_txs:
                self.current_transactions = [
                    tx for tx in self.current_transactions if tx not in pending_txs
                ]
            else:
                self.current_transactions = []

            self.chain.append(block)
            self._update_caches_for_block(block)
            self._invalidate_stats_cache()
            self._save_block_incremental(block)
            return block

    # ── 余额与交易 ──────────────────────────────

    def get_balance(self, address: str) -> Decimal:
        """O(1) 级余额查询（含内存池待确认交易）。

        链上部分直接从 balance_cache 读取，内存池部分遍历 current_transactions。
        """
        balance = self.balance_cache.get(address, Decimal('0'))
        for tx in self.current_transactions:
            if tx['sender'] == address:
                balance -= Decimal(str(tx['amount'])) + Decimal(str(tx.get('fee', 0)))
            if tx['receiver'] == address:
                balance += Decimal(str(tx['amount']))
        return balance

    def new_transaction(
        self, sender: str, receiver: str, amount: Union[float, str],
        fee: Union[float, str], nonce: str, signature: str = '',
    ) -> Tuple[bool, Union[str, int]]:
        """创建一笔新交易，验签、防重放、入池。"""
        amount = Decimal(str(amount))
        fee = Decimal(str(fee))

        if sender != '0':
            if amount <= 0 or fee < 0:
                return False, '交易参数错误'
            try:
                vk = VerifyingKey.from_string(bytes.fromhex(sender), curve=SECP256k1)
                message = f"{sender}->{receiver}:{amount:.8f} fee:{fee:.8f} nonce:{nonce}"
                msg_hash = hashlib.sha256(message.encode('utf-8')).digest()
                if not vk.verify_digest(bytes.fromhex(signature), msg_hash):
                    return False, '签名错误'
            except (ValueError, BadSignatureError, TypeError) as e:
                logger.debug("签名验证异常: %s", e)
                return False, '签名异常'
            last_nonce = self.nonce_map.get(sender, 0)
            if int(nonce.split('-')[-1]) <= last_nonce:
                return False, '重复或过期交易'
            self.nonce_map[sender] = int(nonce.split('-')[-1])

        tx_data = {
            'sender': sender,
            'receiver': receiver,
            'amount': float(amount),
            'fee': float(fee),
            'nonce': nonce,
            'signature': signature,
        }
        tx_data['txid'] = get_hash(tx_data)

        with self.lock:
            if any(t['txid'] == tx_data['txid'] for t in self.current_transactions):
                return False, '交易已存在'
            if sender != '0' and self.get_balance(sender) < (amount + fee):
                return False, '余额不足'
            self.current_transactions.append(tx_data)
            self.current_transactions.sort(key=lambda x: x.get('fee', 0), reverse=True)
            return True, self.last_block['index'] + 1

    # ── 统计缓存 ────────────────────────────────

    def _invalidate_stats_cache(self) -> None:
        self._stats_cache = None

    def get_cached_stats(self) -> Dict[str, Any]:
        """返回带缓存的链上统计信息。

        缓存有效期由 config.STATS_CACHE_TTL 控制，避免高频轮询
        导致的 O(N) 全局遍历。
        """
        now = time.time()
        if self._stats_cache is not None and (now - self._stats_cache_at) < config.STATS_CACHE_TTL:
            return self._stats_cache

        balances, tx_counts, cum_fees = [], [], []
        miner_balance = Decimal('0')
        running_fees = Decimal('0')
        for block in self.chain:
            for tx in block.get('transactions', []):
                if tx.get('receiver') == self.node_public_key:
                    miner_balance += Decimal(str(tx.get('amount', 0)))
                if tx.get('sender') == self.node_public_key:
                    miner_balance -= (
                        Decimal(str(tx.get('amount', 0))) + Decimal(str(tx.get('fee', 0)))
                    )
                if tx.get('sender') != '0':
                    running_fees += Decimal(str(tx.get('fee', 0)))
            balances.append(float(miner_balance))
            tx_counts.append(len(block.get('transactions', [])))
            cum_fees.append(float(running_fees))

        total_txs = sum(tx_counts)
        total_fees = float(running_fees)
        difficulty = self._chain_total_difficulty(self.chain)

        self._stats_cache = {
            'nodes': len(self.nodes),
            'mempool': len(self.current_transactions),
            'height': len(self.chain),
            'balances': balances,
            'tx_counts': tx_counts,
            'cum_fees': cum_fees,
            'total_txs': total_txs,
            'total_fees': round(total_fees, 8),
            'difficulty': difficulty,
        }
        self._stats_cache_at = now
        return self._stats_cache

    @property
    def last_block(self) -> Dict[str, Any]:
        """返回链上最后一个区块。"""
        return self.chain[-1]
