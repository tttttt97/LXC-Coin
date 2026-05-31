import hashlib
import json
from typing import Any, List, Dict, Union


def get_hash(data: Union[Dict[str, Any], str, int, List[Any]]) -> str:
    """对任意 Python 对象执行稳定 SHA-256 哈希。

    字典类型统一用 ``json.dumps(sort_keys=True)`` 序列化后再哈希，
    确保跨平台/跨运行的一致性。

    Args:
        data: 待哈希数据，字典/字符串/数字/列表等。

    Returns:
        64 字符十六进制 SHA-256 摘要。
    """
    try:
        data_str = json.dumps(data, sort_keys=True)
    except TypeError:
        data_str = str(data)
    return hashlib.sha256(data_str.encode('utf-8')).hexdigest()


def get_merkle_root(txs: List[Dict[str, Any]]) -> str:
    """计算交易的 Merkle 树根哈希。

    使用经典自底向上算法，奇数层补足最后一个叶子。

    Args:
        txs: 交易字典列表。

    Returns:
        Merkle 根哈希的十六进制字符串；空列表返回 ``""``。
    """
    if not txs:
        return ''
    hashes = [get_hash(tx) for tx in txs]
    while len(hashes) > 1:
        if len(hashes) % 2 != 0:
            hashes.append(hashes[-1])
        hashes = [
            get_hash(hashes[i] + hashes[i + 1])
            for i in range(0, len(hashes), 2)
        ]
    return hashes[0]

# ── 钱包加密（无外部依赖，基于 hashlib + PBKDF2）─────────────────────

import base64
import secrets as _secrets
import hashlib as _hashlib

_WALLET_SALT_LEN = 16
_WALLET_ITERATIONS = 100_000


def derive_key(password: str, salt: bytes = None) -> tuple[bytes, bytes]:
    """PBKDF2-HMAC-SHA256 密钥派生，返回 (derived_key, salt)。"""
    if salt is None:
        salt = _secrets.token_bytes(_WALLET_SALT_LEN)
    key = _hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt,
                               _WALLET_ITERATIONS, dklen=32)
    return key, salt


def encrypt_private_key(private_key_hex: str, password: str) -> str:
    """使用口令加密十六进制私钥，返回 base64(salt + ciphertext)。

    加密方式为 XOR 流密码，密钥流由 PBKDF2 派生，每次加密使用随机 salt。
    """
    key, salt = derive_key(password)
    plain_bytes = bytes.fromhex(private_key_hex)
    # XOR 流密码：密钥流 = PBKDF2(key, salt + counter)
    ciphertext = bytearray()
    for i in range(0, len(plain_bytes), 32):
        chunk_key = _hashlib.pbkdf2_hmac('sha256', key, salt + i.to_bytes(4, 'big'),
                                         _WALLET_ITERATIONS, dklen=32)
        chunk = plain_bytes[i:i + 32]
        ciphertext.extend(b ^ c for b, c in zip(chunk, chunk_key))
    return base64.b64encode(salt + bytes(ciphertext)).decode('ascii')


def decrypt_private_key(encrypted_b64: str, password: str) -> str:
    """解密由 encrypt_private_key 生成的密文，返回十六进制私钥。"""
    raw = base64.b64decode(encrypted_b64)
    salt, ciphertext = raw[:_WALLET_SALT_LEN], raw[_WALLET_SALT_LEN:]
    key, _ = derive_key(password, salt)
    plain = bytearray()
    for i in range(0, len(ciphertext), 32):
        chunk_key = _hashlib.pbkdf2_hmac('sha256', key, salt + i.to_bytes(4, 'big'),
                                         _WALLET_ITERATIONS, dklen=32)
        chunk = ciphertext[i:i + 32]
        plain.extend(b ^ c for b, c in zip(chunk, chunk_key))
    return bytes(plain).hex()
