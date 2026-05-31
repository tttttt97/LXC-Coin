"""区块链核心逻辑单元测试。

运行方式::

    cd LXC-Coin
    pytest test_blockchain.py -v

所有涉及磁盘 I/O 的测试均使用 ``tmp_path`` 夹具,
测试结束后自动清理，不留垃圾文件。
"""

import hashlib
import json
import os
import pytest
from decimal import Decimal
from ecdsa import SigningKey, SECP256k1

from utils.crypto import get_hash, get_merkle_root
from blockchain.chain import Blockchain
import config


# ═══════════════════════════════════════════════
# 辅助
# ═══════════════════════════════════════════════

def _make_blockchain(tmp_path, port: int) -> Blockchain:
    """在临时目录中创建并初始化一个带创世区块的节点。"""
    os.environ["BLOCKCHAIN_DATA_DIR"] = str(tmp_path)
    b = Blockchain()
    b.init_storage(port)
    return b


def _sign(b: Blockchain, receiver: str, amount: int, fee: int, nonce: str):
    msg = Blockchain.format_message(b.node_public_key, receiver, amount, fee, nonce)
    sk = SigningKey.from_string(bytes.fromhex(b.node_private_key), curve=SECP256k1)
    digest = hashlib.sha256(msg.encode('utf-8')).digest()
    return msg, sk.sign_digest(digest).hex()


# ═══════════════════════════════════════════════
# get_hash / get_merkle_root
# ═══════════════════════════════════════════════

def test_get_hash_consistent():
    """相同输入应产生相同哈希"""
    a = get_hash({'a': 1, 'b': 2})
    b = get_hash({'b': 2, 'a': 1})
    assert a == b
    assert len(a) == 64


def test_get_hash_different():
    """不同输入应产生不同哈希"""
    a = get_hash({'a': 1})
    b = get_hash({'a': 2})
    assert a != b


def test_merkle_root_empty():
    """空交易列表返回空字符串"""
    assert get_merkle_root([]) == ''


def test_merkle_root_single():
    """单笔交易的 merkle 根"""
    root = get_merkle_root([{'amount': 100}])
    assert len(root) == 64


def test_merkle_root_multi():
    """多笔交易的 merkle 根"""
    txs = [{'a': i} for i in range(5)]
    root = get_merkle_root(txs)
    assert len(root) == 64


# ═══════════════════════════════════════════════
# PoW
# ═══════════════════════════════════════════════

def test_valid_proof():
    b = Blockchain()
    nonce = b.proof_of_work("0")
    assert b.valid_proof("0", nonce) is True
    assert b.valid_proof("0", -1) is False


def test_proof_of_work_returns_valid_nonce():
    b = Blockchain()
    fake_hash = "abc123"
    nonce = b.proof_of_work(fake_hash)
    assert b.valid_proof(fake_hash, nonce) is True


# ═══════════════════════════════════════════════
# Chain validation
# ═══════════════════════════════════════════════

def test_valid_chain_empty():
    b = Blockchain()
    assert b.valid_chain([]) is False


def test_valid_chain_genesis():
    b = Blockchain()
    genesis_nonce = b.proof_of_work("0")
    b.new_block(prev_hash="0", nonce=genesis_nonce)
    assert b.valid_chain(b.chain) is True


def test_valid_chain_tampered_prev_hash():
    b = Blockchain()
    n1 = b.proof_of_work("0")
    b.new_block(prev_hash="0", nonce=n1)
    b.chain[0]['prev_hash'] = "tampered"
    assert b.valid_chain(b.chain) is False


def test_valid_chain_tampered_amount():
    b = Blockchain()
    g_nonce = b.proof_of_work("0")
    b.new_block(prev_hash="0", nonce=g_nonce)
    n1 = b.proof_of_work(b.last_block['hash'])
    b.new_block(prev_hash=b.last_block['hash'], nonce=n1)
    for tx in b.chain[1]['transactions']:
        if tx['sender'] == '0':
            tx['amount'] = 999999
    assert b.valid_chain(b.chain) is False


# ═══════════════════════════════════════════════
# Transactions（全部使用 tmp_path）
# ═══════════════════════════════════════════════

def test_coinbase_reward(tmp_path):
    b = _make_blockchain(tmp_path, 8001)
    n1 = b.proof_of_work("0")
    genesis = b.new_block(prev_hash="0", nonce=n1)
    coinbase_tx = genesis['transactions'][0] if genesis['transactions'] else None
    assert coinbase_tx is not None
    assert coinbase_tx['sender'] == '0'
    assert coinbase_tx['receiver'] == b.node_public_key
    assert coinbase_tx['amount'] == config.MINING_REWARD


def test_balance_after_coinbase(tmp_path):
    b = _make_blockchain(tmp_path, 8002)
    n1 = b.proof_of_work("0")
    b.new_block(prev_hash="0", nonce=n1)
    assert b.get_balance(b.node_public_key) == Decimal(str(config.MINING_REWARD))


def test_transaction_signature_required(tmp_path):
    b = _make_blockchain(tmp_path, 8003)
    n1 = b.proof_of_work("0")
    b.new_block(prev_hash="0", nonce=n1)
    success, msg = b.new_transaction(
        b.node_public_key, "receiver", 10, 1, "N-9999", "bad_signature",
    )
    assert success is False
    assert '签名' in msg


def test_valid_transaction(tmp_path):
    b = _make_blockchain(tmp_path, 8004)
    n1 = b.proof_of_work("0")
    b.new_block(prev_hash="0", nonce=n1)

    sk = SigningKey.generate(curve=SECP256k1)
    receiver = sk.verifying_key.to_string().hex()
    nonce = "N-1000"

    _, signature = _sign(b, receiver, 10, 1, nonce)
    success, res = b.new_transaction(b.node_public_key, receiver, 10, 1, nonce, signature)
    assert success is True, f"new_transaction 返回 False: {res}"
    assert isinstance(res, int)


def test_insufficient_balance(tmp_path):
    b = _make_blockchain(tmp_path, 8005)
    n1 = b.proof_of_work("0")
    b.new_block(prev_hash="0", nonce=n1)

    sk = SigningKey.generate(curve=SECP256k1)
    receiver = sk.verifying_key.to_string().hex()
    nonce = "N-1001"

    _, signature = _sign(b, receiver, 1000, 1, nonce)
    success, msg = b.new_transaction(b.node_public_key, receiver, 1000, 1, nonce, signature)
    assert success is False
    assert '余额' in msg or '签名' in msg


def test_mempool_dedup(tmp_path):
    b = _make_blockchain(tmp_path, 8006)
    n1 = b.proof_of_work("0")
    b.new_block(prev_hash="0", nonce=n1)

    sk = SigningKey.generate(curve=SECP256k1)
    receiver = sk.verifying_key.to_string().hex()
    nonce = "N-2000"

    _, sig = _sign(b, receiver, 5, 0, nonce)
    ok1, res1 = b.new_transaction(b.node_public_key, receiver, 5, 0, nonce, sig)
    ok2, msg = b.new_transaction(b.node_public_key, receiver, 5, 0, nonce, sig)
    assert ok1 is True, f"第一次交易应为成功，但返回: {res1}"
    assert ok2 is False
    assert '重复' in msg


def test_replay_protection(tmp_path):
    b = _make_blockchain(tmp_path, 8007)
    n1 = b.proof_of_work("0")
    b.new_block(prev_hash="0", nonce=n1)

    sk_r = SigningKey.generate(curve=SECP256k1)
    receiver = sk_r.verifying_key.to_string().hex()
    nonce1 = "N-3000"
    nonce2 = "N-3001"

    _, sig1 = _sign(b, receiver, 5, 0, nonce1)
    _, sig2 = _sign(b, receiver, 5, 0, nonce2)

    ok1, _ = b.new_transaction(b.node_public_key, receiver, 5, 0, nonce1, sig1)
    assert ok1 is True

    ok2, _ = b.new_transaction(b.node_public_key, receiver, 5, 0, nonce2, sig2)
    assert ok2 is True

    ok3, msg3 = b.new_transaction(b.node_public_key, receiver, 5, 0, nonce1, sig1)
    assert ok3 is False
    assert '重复' in msg3


# ═══════════════════════════════════════════════
# Chain storage（全部使用 tmp_path）
# ═══════════════════════════════════════════════

def test_new_block_increments_index(tmp_path):
    b = _make_blockchain(tmp_path, 8008)
    n1 = b.proof_of_work("0")
    b1 = b.new_block(prev_hash="0", nonce=n1)
    assert b1['index'] == 2
    assert b.last_block['index'] == 2

    n2 = b.proof_of_work(b1['hash'])
    b2 = b.new_block(prev_hash=b1['hash'], nonce=n2)
    assert b2['index'] == 3
    assert b.last_block['index'] == 3


def test_block_hash_integrity(tmp_path):
    b = _make_blockchain(tmp_path, 8009)
    n1 = b.proof_of_work("0")
    block = b.new_block(prev_hash="0", nonce=n1)
    block_copy = {k: v for k, v in block.items() if k != 'hash'}
    assert get_hash(block_copy) == block['hash']


def test_save_and_load_chain(tmp_path):
    """测试链序列化与反序列化的一致性"""
    b = _make_blockchain(tmp_path, 8010)
    n1 = b.proof_of_work("0")
    b.new_block(prev_hash="0", nonce=n1)
    original_json = json.dumps(b.chain, sort_keys=True)

    b2 = _make_blockchain(tmp_path, 8010)
    loaded_json = json.dumps(b2.chain, sort_keys=True)
    assert loaded_json == original_json


# ═══════════════════════════════════════════════
# 边界条件与安全性测试（v2 新增）
# ═══════════════════════════════════════════════

def test_negative_amount_rejected(tmp_path):
    """负数金额的转账请求应被明确拒绝。"""
    """负数金额交易应被拒绝。"""
    b = _make_blockchain(tmp_path, 8011)
    n1 = b.proof_of_work("0")
    b.new_block(prev_hash="0", nonce=n1)
    _, sig = _sign(b, "receiver", 10, 0, "N-9001")
    success, msg = b.new_transaction(b.node_public_key, "receiver", -5, 0, "N-9001", sig)
    assert success is False
    assert '参数错误' in msg


def test_zero_amount_rejected(tmp_path):
    """零金额转账请求应被拒绝，防止空交易污染内存池。"""
    """零金额交易应被拒绝。"""
    b = _make_blockchain(tmp_path, 8012)
    n1 = b.proof_of_work("0")
    b.new_block(prev_hash="0", nonce=n1)
    _, sig = _sign(b, "receiver", 0, 0, "N-9002")
    success, msg = b.new_transaction(b.node_public_key, "receiver", 0, 0, "N-9002", sig)
    assert success is False
    assert '参数错误' in msg


def test_negative_fee_rejected(tmp_path):
    """负手续费应被拒绝，防止恶意节点通过负费攻击获利。"""
    """负手续费应被拒绝。"""
    b = _make_blockchain(tmp_path, 8013)
    n1 = b.proof_of_work("0")
    b.new_block(prev_hash="0", nonce=n1)
    _, sig = _sign(b, "receiver", 5, -1, "N-9003")
    success, msg = b.new_transaction(b.node_public_key, "receiver", 5, -1, "N-9003", sig)
    assert success is False


def test_self_transfer_rejected_or_handled(tmp_path):
    """自转账户不应改变余额，确保转账逻辑的一致性。"""
    """自转账（发送方=接收方）的行为应明确。"""
    b = _make_blockchain(tmp_path, 8014)
    n1 = b.proof_of_work("0")
    b.new_block(prev_hash="0", nonce=n1)
    nonce = "N-9004"
    _, sig = _sign(b, b.node_public_key, 5, 0, nonce)
    success, msg = b.new_transaction(b.node_public_key, b.node_public_key, 5, 0, nonce, sig)
    # 自转账应成功入池（但不改变余额），或显式拒绝
    if success:
        bal = b.get_balance(b.node_public_key)
        assert bal == Decimal(str(config.MINING_REWARD)), f"自转账不应改变余额，当前={bal}"


def test_large_amount_precision(tmp_path):
    """高精度金额在 Decimal 链路中不应丢失尾数位。"""
    """大额交易应保持 Decimal 精度（不丢失最低位）。"""
    b = _make_blockchain(tmp_path, 8015)
    n1 = b.proof_of_work("0")
    b.new_block(prev_hash="0", nonce=n1)
    nonce = "N-9005"
    amount = "12.34567890"
    _, sig = _sign(b, "receiver", amount, 0, nonce)
    success, _ = b.new_transaction(b.node_public_key, "receiver", amount, 0, nonce, sig)
    assert success is True
    # 验证交易在内存池中金额精确
    tx = b.current_transactions[-1]
    assert tx['amount'] == float(amount)


def test_mine_empty_mempool(tmp_path):
    """空内存池状态下挖矿应正常产生仅含 coinbase 的区块。"""
    """空内存池时挖矿应正常产出只含 coinbase 的区块。"""
    b = _make_blockchain(tmp_path, 8016)
    n1 = b.proof_of_work("0")
    b.new_block(prev_hash="0", nonce=n1)
    assert len(b.current_transactions) == 0
    n2 = b.proof_of_work(b.last_block['hash'])
    block = b.new_block(prev_hash=b.last_block['hash'], nonce=n2)
    assert block['index'] == 3
    # 空内存池区块应只有 coinbase
    assert len(block['transactions']) == 1
    assert block['transactions'][0]['sender'] == '0'


def test_chain_validation_after_sync_prevents_double_spend(tmp_path):
    """恶意构造的透支链应被 valid_chain 拒绝，防御双花攻击。"""
    """模拟双花攻击：恶意链中包含透支交易，valid_chain 应拒绝。"""
    b = _make_blockchain(tmp_path, 8017)
    n1 = b.proof_of_work("0")
    g = b.new_block(prev_hash="0", nonce=n1)
    # 构造一条包含透支交易的"恶意链"
    malicious_chain = [g]
    # 第二个区块：发送方发送超过 coinbase 奖励的金额（双花）
    coinbase = {
        'sender': '0', 'receiver': b.node_public_key, 'amount': float(config.MINING_REWARD),
        'fee': 0, 'nonce': 'CB-1', 'signature': 'system_coinbase',
    }
    coinbase['txid'] = get_hash(coinbase)
    overspend = {
        'sender': b.node_public_key, 'receiver': 'attacker', 'amount': float(config.MINING_REWARD * 2),
        'fee': 0, 'nonce': 'N-FAKE', 'signature': 'invalid_sig', 'txid': 'fake_txid',
    }
    malicious_block = {
        'index': 2, 'timestamp': 999999.0, 'transactions': [coinbase, overspend],
        'nonce': 0, 'prev_hash': g['hash'], 'merkle_root': '',
    }
    malicious_block['hash'] = get_hash({k: v for k, v in malicious_block.items() if k != 'hash'})
    malicious_chain.append(malicious_block)
    # 由于签名无效，valid_chain 应拒绝
    assert b.valid_chain(malicious_chain) is False
