"""API 安全和限流中间件。

提供基于 IP 的滑动窗口速率限制和可选的 Token 鉴权。
Docker/反向代理环境下优先读取 X-Forwarded-For 头以正确识别客户端。
"""

import time
import logging
from collections import defaultdict
from typing import Optional
from flask import request, Flask

import config

logger = logging.getLogger("audit")


def _real_client_ip() -> str:
    """获取真实客户端 IP，兼容反向代理和 Docker Bridge 网络。"""
    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.remote_addr or "unknown"


def create_rate_limiter(window: int = 60, max_req: int = 120):
    """创建滑动窗口速率限制器闭包。

    Args:
        window: 时间窗口（秒）。
        max_req: 窗口内最大请求数。

    Returns:
        一个可注册为 ``app.before_request`` 的限流回调函数。
    """
    buckets: dict[str, list[float]] = defaultdict(list)

    def _before_request():
        token = config.API_AUTH_TOKEN
        if token:
            auth = request.headers.get("Authorization", "")
            if auth != f"Bearer {token}" and request.path != "/health":
                logger.warning(
                    "AUDIT: 鉴权失败 | IP=%s Path=%s",
                    _real_client_ip(), request.path,
                )
                from flask import jsonify
                return jsonify({"message": "未授权访问"}), 401

        client = _real_client_ip()
        now = time.time()
        bucket = buckets[client]
        bucket[:] = [t for t in bucket if now - t < window]
        if len(bucket) >= max_req:
            logger.warning(
                "AUDIT: 频率限制触发 | IP=%s Requests=%d/%d Window=%ds",
                client, len(bucket), max_req, window,
            )
            from flask import jsonify
            return jsonify({"message": "请求过于频繁，请稍后重试"}), 429
        bucket.append(now)
        return None

    return _before_request
