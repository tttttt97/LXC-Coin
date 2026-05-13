"""区块链核心逻辑单元测试。

运行方式::

    cd final_block_chain
    pytest test_blockchain.py -v

所有涉及磁盘 I/O 的测试均使用 ``tmp_path`` 夹具,
测试结束后自动清理，不留垃圾文件。
"""

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
    os.environ.pop("BLOCKCHAIN_DATA_DIR", None)
    return b


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
    n1 = b.proof_of_work("0")
    b.new_block(prev_hash="0", nonce=n1)
    for tx in b.chain[0]['transactions']:
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
    message = f"{b.node_public_key}->{receiver}:10.00000000 fee:1.00000000 nonce:{nonce}"

    node_sk = SigningKey.from_string(bytes.fromhex(b.node_private_key), curve=SECP256k1)
    signature = node_sk.sign(message.encode('utf-8')).hex()

    success, res = b.new_transaction(
        b.node_public_key, receiver, 10, 1, nonce, signature,
    )
    assert success is True
    assert isinstance(res, int)


def test_insufficient_balance(tmp_path):
    b = _make_blockchain(tmp_path, 8005)
    n1 = b.proof_of_work("0")
    b.new_block(prev_hash="0", nonce=n1)

    sk = SigningKey.generate(curve=SECP256k1)
    receiver = sk.verifying_key.to_string().hex()
    nonce = "N-1001"
    message = f"{b.node_public_key}->{receiver}:1000.00000000 fee:1.00000000 nonce:{nonce}"
    node_sk = SigningKey.from_string(bytes.fromhex(b.node_private_key), curve=SECP256k1)
    signature = node_sk.sign(message.encode('utf-8')).hex()

    success, msg = b.new_transaction(
        b.node_public_key, receiver, 1000, 1, nonce, signature,
    )
    assert success is False
    assert '余额' in msg


def test_mempool_dedup(tmp_path):
    b = _make_blockchain(tmp_path, 8006)
    n1 = b.proof_of_work("0")
    b.new_block(prev_hash="0", nonce=n1)

    sk = SigningKey.generate(curve=SECP256k1)
    receiver = sk.verifying_key.to_string().hex()
    nonce = "N-2000"
    message = f"{b.node_public_key}->{receiver}:5.00000000 fee:0.00000000 nonce:{nonce}"
    node_sk = SigningKey.from_string(bytes.fromhex(b.node_private_key), curve=SECP256k1)
    sig = node_sk.sign(message.encode('utf-8')).hex()

    ok1, _ = b.new_transaction(b.node_public_key, receiver, 5, 0, nonce, sig)
    ok2, msg = b.new_transaction(b.node_public_key, receiver, 5, 0, nonce, sig)
    assert ok1 is True
    assert ok2 is False
    assert '已存在' in msg


def test_replay_protection(tmp_path):
    b = _make_blockchain(tmp_path, 8007)
    n1 = b.proof_of_work("0")
    b.new_block(prev_hash="0", nonce=n1)

    sk = SigningKey.generate(curve=SECP256k1)
    receiver = sk.verifying_key.to_string().hex()
    nonce1 = "N-3000"
    nonce2 = "N-3001"
    message = f"{b.node_public_key}->{receiver}:5.00000000 fee:0.00000000 nonce:{nonce1}"
    node_sk = SigningKey.from_string(bytes.fromhex(b.node_private_key), curve=SECP256k1)

    ok1, _ = b.new_transaction(
        b.node_public_key, receiver, 5, 0, nonce1,
        node_sk.sign(message.encode('utf-8')).hex(),
    )
    assert ok1 is True

    message2 = f"{b.node_public_key}->{receiver}:5.00000000 fee:0.00000000 nonce:{nonce2}"
    ok2, _ = b.new_transaction(
        b.node_public_key, receiver, 5, 0, nonce2,
        node_sk.sign(message2.encode('utf-8')).hex(),
    )
    assert ok2 is True

    ok3, msg3 = b.new_transaction(
        b.node_public_key, receiver, 5, 0, nonce1,
        node_sk.sign(message.encode('utf-8')).hex(),
    )
    assert ok3 is False
    assert '重复' in msg3


# ═══════════════════════════════════════════════
# Chain storage（全部使用 tmp_path）
# ═══════════════════════════════════════════════

def test_new_block_increments_index(tmp_path):
    b = _make_blockchain(tmp_path, 8008)
    n1 = b.proof_of_work("0")
    b1 = b.new_block(prev_hash="0", nonce=n1)
    assert b1['index'] == 1
    assert b.last_block['index'] == 1

    n2 = b.proof_of_work(b1['hash'])
    b2 = b.new_block(prev_hash=b1['hash'], nonce=n2)
    assert b2['index'] == 2
    assert b.last_block['index'] == 2


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
