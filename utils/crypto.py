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
