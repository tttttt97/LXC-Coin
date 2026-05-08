import time
import logging
from typing import TYPE_CHECKING

import requests
import config

if TYPE_CHECKING:
    from blockchain.chain import Blockchain

logger = logging.getLogger(__name__)


def _is_safe_node(address: str) -> bool:
    host = address.split(':')[0] if ':' in address else address
    return host in ('127.0.0.1', 'localhost', config.HOST)


def auto_discover_network(my_port: int, blockchain: 'Blockchain') -> None:
    """启动后执行节点发现。

    优先从 SEED_NODES 环境变量读取种子节点进行扩散，
    此外仍执行局域网端口扫描作为兜底策略。

    Args:
        my_port: 本节点的监听端口。
        blockchain: 宿主 Blockchain 实例。
    """
    time.sleep(config.DISCOVERY_DELAY)

    seeds = config.SEED_NODES
    if seeds:
        logger.info("通过种子节点 %s 进行网络扩散...", seeds)
        for seed in seeds.split(","):
            seed = seed.strip()
            if seed:
                if not _is_safe_node(seed):
                    logger.warning("拒绝不安全的种子节点: %s", seed)
                    continue
                blockchain.register_seed(seed)
                try:
                    res = requests.get(f"http://{seed}/peers", timeout=config.NETWORK_TIMEOUT)
                    if res.status_code == 200:
                        peer_data = res.json()
                        for peer in peer_data.get('peers', []):
                            if peer != f"{config.HOST}:{my_port}":
                                blockchain.register_node(f"http://{peer}")
                        logger.info("从种子节点 %s 获取到 %d 个邻居", seed, len(peer_data.get('peers', [])))
                except (requests.ConnectionError, requests.Timeout):
                    logger.debug("种子节点 %s 不可达", seed)

    logger.info("补充扫描网络节点 %d-%d ...", config.NETWORK_SCAN_START, config.NETWORK_SCAN_END - 1)
    for target_port in range(config.NETWORK_SCAN_START, config.NETWORK_SCAN_END):
        if target_port == my_port:
            continue
        target_node = f"{config.HOST}:{target_port}"
        try:
            res = requests.get(f"http://{target_node}/chain", timeout=config.NETWORK_TIMEOUT)
            if res.status_code == 200:
                blockchain.register_node(f"http://{target_node}")
                requests.post(
                    f"http://{target_node}/nodes/register",
                    json={"nodes": [f"http://{config.HOST}:{my_port}"]},
                )
                logger.info("已连接邻居节点: %s", target_node)
        except (requests.ConnectionError, requests.Timeout):
            pass


def auto_sync_worker(blockchain: 'Blockchain') -> None:
    """后台线程：每 `SYNC_INTERVAL` 秒执行一次最长链共识同步。

    Args:
        blockchain: 宿主 Blockchain 实例。
    """
    while True:
        time.sleep(config.SYNC_INTERVAL)
        if list(blockchain.nodes):
            blockchain.resolve_conflicts()
