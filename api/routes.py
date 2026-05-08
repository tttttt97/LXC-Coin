"""Flask REST API 路由定义。

所有路由通过 ``register_routes(app, blockchain)`` 注入，保持
API 层对区块链核心的依赖为单向。
"""

import time
from decimal import Decimal
from typing import TYPE_CHECKING

from flask import Flask, jsonify, request, render_template

from utils import get_hash
import config

if TYPE_CHECKING:
    from blockchain.chain import Blockchain


def register_routes(app: Flask, blockchain: 'Blockchain') -> None:
    """将全部 REST 端点注册到指定 Flask 应用上。

    Args:
        app: Flask 实例。
        blockchain: 已初始化的 Blockchain 核心实例。
    """

    @app.route('/')
    def index():
        """渲染前端 DApp 面板。"""
        return render_template('index.html')

    @app.route('/explore/<query>', methods=['GET'])
    def explore_blockchain(query: str):
        """全网浏览器：按区块哈希/TxID/地址搜索目标。"""
        for block in blockchain.chain:
            if block['hash'] == query:
                return jsonify({'type': 'block', 'data': block}), 200
            for tx in block['transactions']:
                if tx.get('txid') == query or tx.get('signature') == query:
                    return jsonify(
                        {'type': 'transaction', 'data': tx, 'block_index': block['index']}
                    ), 200

        history, balance = [], Decimal('0')
        for block in blockchain.chain:
            for tx in block['transactions']:
                if tx['sender'] == query or tx['receiver'] == query:
                    history.append({
                        'tx': tx,
                        'block_index': block['index'],
                        'timestamp': block['timestamp'],
                    })
                    if tx['sender'] == query:
                        balance -= Decimal(str(tx['amount'])) + Decimal(str(tx.get('fee', 0)))
                    if tx['receiver'] == query:
                        balance += Decimal(str(tx['amount']))
        for tx in blockchain.current_transactions:
            if tx['sender'] == query or tx['receiver'] == query:
                history.append({
                    'tx': tx,
                    'block_index': 'Mempool',
                    'timestamp': 'Pending',
                })
                if tx['sender'] == query:
                    balance -= Decimal(str(tx['amount'])) + Decimal(str(tx.get('fee', 0)))
                if tx['receiver'] == query:
                    balance += Decimal(str(tx['amount']))
        if history:
            history.reverse()
            return jsonify({
                'type': 'address',
                'balance': float(round(balance, 8)),
                'history': history,
            }), 200
        return jsonify({'message': '未找到记录'}), 404

    @app.route('/node/wallet', methods=['GET'])
    def get_node_wallet():
        """返回当前节点矿工公钥（私钥不通过 API 传输）。"""
        return jsonify({
            'public_key': blockchain.node_public_key,
            'private_key': '【安全隐藏】',
        }), 200

    @app.route('/transactions/new', methods=['POST'])
    def new_transaction():
        """接收签名后的交易并提交至内存池。"""
        if not request.is_json:
            return jsonify({'message': '请求体必须为 JSON 格式'}), 415
        v = request.get_json()
        required = ['sender', 'receiver', 'amount', 'nonce', 'signature']
        if not all(k in v for k in required):
            return jsonify({'message': '缺少必填字段'}), 400
        try:
            fee = float(v.get('fee', 0))
        except (ValueError, TypeError):
            return jsonify({'message': '手续费格式无效'}), 400
        if fee < 0:
            return jsonify({'message': '手续费不能为负'}), 400
        success, res = blockchain.new_transaction(
            v['sender'], v['receiver'], v['amount'], fee, v['nonce'], v['signature'],
        )
        if not success:
            return jsonify({'message': f'❌ {res}'}), 400
        return jsonify({'message': f'✅ 交易成功入池，待打包区块 #{res}'}), 201

    @app.route('/mine', methods=['GET'])
    def mine():
        """执行 PoW 挖矿，打包内存池交易并生成新区块。"""
        last_block_hash = blockchain.last_block['hash']
        with blockchain.lock:
            tx_snapshot = list(blockchain.current_transactions)
        nonce = blockchain.proof_of_work(last_block_hash)
        with blockchain.lock:
            if blockchain.last_block['hash'] != last_block_hash:
                return jsonify({'message': '⚠️ 网络有更新，计算作废'}), 409
            total_fees = sum(Decimal(str(tx.get('fee', 0))) for tx in tx_snapshot)
            reward = Decimal(config.MINING_REWARD) + total_fees
            coinbase_tx_data = {
                'sender': '0',
                'receiver': blockchain.node_public_key,
                'amount': float(reward),
                'fee': 0,
                'nonce': f"CB-{int(time.time() * 1000)}",
                'signature': 'system_coinbase',
            }
            coinbase_tx = {**coinbase_tx_data, 'txid': get_hash(coinbase_tx_data)}
            tx_snapshot.insert(0, coinbase_tx)
            block = blockchain.new_block(
                prev_hash=last_block_hash, nonce=nonce, pending_txs=tx_snapshot,
            )
        return jsonify({
            'message': f"🎉 挖矿成功！获得 {reward} 币",
            'index': block['index'],
            'hash': block['hash'],
        }), 200

    @app.route('/chain', methods=['GET'])
    def full_chain():
        """返回当前节点的完整区块链数据。"""
        return jsonify({'chain': blockchain.chain, 'length': len(blockchain.chain)}), 200

    @app.route('/nodes/register', methods=['POST'])
    def register_nodes():
        """接收并注册邻居节点地址列表。"""
        if not request.is_json:
            return jsonify({'message': '请求体必须为 JSON 格式'}), 415
        data = request.get_json()
        nodes = data.get('nodes', []) if data else []
        for node in nodes:
            blockchain.register_node(node)
        return jsonify({'message': 'OK'}), 201

    @app.route('/balance/<address>', methods=['GET'])
    def get_balance(address: str):
        """查询指定地址当前余额。"""
        return jsonify({'address': address, 'balance': float(blockchain.get_balance(address))}), 200

    @app.route('/peers', methods=['GET'])
    def api_peers():
        """返回当前已注册的邻居节点列表。"""
        return jsonify({'peers': list(blockchain.nodes), 'count': len(blockchain.nodes)}), 200

    @app.route('/stats', methods=['GET'])
    def api_stats():
        """返回带缓存机制的链上统计信息。"""
        return jsonify(blockchain.get_cached_stats()), 200

    @app.route('/health', methods=['GET'])
    def health():
        """健康检查：返回节点存活状态和链高度。"""
        return jsonify({'status': 'ok', 'height': len(blockchain.chain)}), 200

    @app.route('/nodes/resolve', methods=['GET'])
    def consensus():
        """手动触发最长链共识同步。"""
        replaced = blockchain.resolve_conflicts()
        msg = '同步成功' if replaced else '已是最新'
        return jsonify({'message': msg, 'chain': blockchain.chain}), 200
