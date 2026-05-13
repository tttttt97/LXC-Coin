"""分布式区块链节点 -- 启动入口。

用法::

    python main.py -p 5000

环境变量:
    BLOCKCHAIN_HOST: 监听地址（默认 127.0.0.1）
    BLOCKCHAIN_PORT: 默认端口（默认 5000）
"""

import os
import threading
import logging
from logging.handlers import RotatingFileHandler
from typing import Tuple

from flask import Flask

from blockchain import Blockchain
from api import register_routes, create_rate_limiter
from network import auto_discover_network, auto_sync_worker
import config

AUDIT_LOG_FORMAT = '%(asctime)s [%(levelname)s] %(name)s: %(message)s'


def _setup_logging(port: int) -> None:
    """配置双通道日志：控制台（实时） + 滚动文件（审计留存）。"""
    os.makedirs(config.DATA_DIR, exist_ok=True)

    root = logging.getLogger()
    root.setLevel(logging.INFO)

    console = logging.StreamHandler()
    console.setLevel(logging.INFO)
    console.setFormatter(logging.Formatter('%(asctime)s [%(levelname)s] %(message)s', datefmt='%H:%M:%S'))
    root.addHandler(console)

    audit_file = os.path.join(config.DATA_DIR, f"audit_{port}.log")
    file_handler = RotatingFileHandler(
        audit_file, maxBytes=2 * 1024 * 1024, backupCount=7,
        encoding='utf-8',
    )
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(logging.Formatter(AUDIT_LOG_FORMAT, datefmt='%Y-%m-%d %H:%M:%S'))
    root.addHandler(file_handler)
    root.info("审计日志已启用，路径: %s", audit_file)
logger = logging.getLogger(__name__)


def create_app() -> Tuple[Flask, Blockchain]:
    """应用工厂：创建 Flask 实例并绑定 Blockchain。

    Returns:
        ``(Flask 实例, Blockchain 实例)``。
    """
    app = Flask(__name__, template_folder='templates')
    app.config['MAX_CONTENT_LENGTH'] = 1 * 1024 * 1024  # 1 MB 请求体上限
    blockchain = Blockchain()
    register_routes(app, blockchain)
    app.before_request(create_rate_limiter(config.RATE_LIMIT_WINDOW, config.RATE_LIMIT_MAX_REQUESTS))

    @app.after_request
    def set_security_headers(response):
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['X-Frame-Options'] = 'DENY'
        response.headers['X-XSS-Protection'] = '1; mode=block'
        response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self' 'unsafe-inline' https://cdnjs.cloudflare.com https://cdn.jsdelivr.net; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src https://fonts.gstatic.com; connect-src 'self'; img-src 'self' data:"
        response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
        return response

    return app, blockchain


if __name__ == '__main__':
    from argparse import ArgumentParser

    parser = ArgumentParser(description='分布式区块链仿真节点')
    parser.add_argument('-p', '--port', default=config.DEFAULT_PORT, type=int, help='监听端口')
    args = parser.parse_args()

    app, blockchain = create_app()
    _setup_logging(args.port)
    blockchain.init_storage(args.port)

    logger.info("=" * 56)
    logger.info("矿工公钥: %s", blockchain.node_public_key)
    logger.info("矿工私钥: [已隐藏，存储在 %s 目录的数据库文件中]", config.DATA_DIR)
    logger.info("=" * 56)

    threading.Thread(
        target=auto_discover_network, args=(args.port, blockchain), daemon=True,
    ).start()
    threading.Thread(
        target=auto_sync_worker, args=(blockchain,), daemon=True,
    ).start()
    app.run(host=config.HOST, port=args.port)
