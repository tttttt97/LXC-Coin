# LXC-Coin -- 分布式区块链仿真节点

![Python](https://img.shields.io/badge/python-3.10%2B-blue)
![Build](https://github.com/tttttt97/LXC-Coin/actions/workflows/ci.yml/badge.svg)
![Docker](https://img.shields.io/badge/docker-supported-blue)
![Storage](https://img.shields.io/badge/storage-SQLite_%2B_JSON-green)
![Tests](https://img.shields.io/badge/tests-21%20passed-brightgreen)

> 从零构建的完整区块链系统与去中心化应用终端。

---

## 目录

- [核心特性](#核心特性)
- [系统架构](#系统架构)
- [数据库结构](#数据库结构)
- [核心模块](#核心模块)
- [安全防护](#安全防护)
- [代码示例](#代码示例)
- [快速启动](#快速启动)
- [LXC-Coin 使用指南](#lxc-coin-使用指南)
- [API 端点](#api-端点)
- [配置](#配置)
- [单元测试与 CI/CD](#单元测试与-cicd)
- [常见问题](#常见问题)
- [故障排查](#故障排查)
- [局限与展望](#局限与展望)

---

## 核心特性

### 密码学与共识

| 特性 | 说明 |
|------|------|
| 离线 ECDSA 签名 | 基于 `secp256k1` 椭圆曲线，私钥在浏览器本地生成，不触碰网络 |
| 状态机重演验证 | P2P 同步时逐块重演余额，防止恶意节点构造透支攻击 |
| 防重放攻击 | 全局 `nonce_map` 严格递增校验，同一签名无法重复提交 |
| 累计工作量共识 | 基于 `chain_length * difficulty_weight` 比较，为动态难度预留接口 |
| Decimal 精度 | 全链路使用 Python `Decimal`，消除跨语言浮点误差 |

### 性能与存储

| 特性 | 说明 |
|------|------|
| O(1) 余额缓存 | `balance_cache` 随新区块增量更新，避免历史遍历 |
| 双后端持久化 | 策略模式抽象 `BaseStorage`，默认 SQLite，可切 JSON |
| SQLite 增量写入 | `blocks` + `transactions` 分表逐行 INSERT |
| Stats 缓存 | `/stats` 端点 TTL 缓存，防止高频轮询触发 CPU 雪崩 |

### P2P 网络

| 特性 | 说明 |
|------|------|
| 种子节点扩散 | `SEED_NODES` 环境变量注入，通过 `/peers` 端点网状发现 |
| 局域网扫描兜底 | 保留端口扫描作为本地多节点快速接入方式 |
| 自动同步 | 后台线程每 5 秒执行一次最长链共识 |

### 工程化

| 特性 | 说明 |
|------|------|
| Docker 集群 | `docker-compose up -d` 一键启动 3 节点互联网络 |
| API 安全 | 滑动窗口限流（支持 `X-Forwarded-For`）+ Bearer Token 鉴权 |
| 健康检查 | `GET /health` 返回节点状态与链高度 |
| CI/CD | GitHub Actions：glob 编译 + Ruff 静态分析 + 18 个 pytest |
| 类型标注 | 全部公开方法含返回类型标注与 docstring |

---

## 系统架构

```text
LXC-Coin
 ├── blockchain/        # 核心引擎 (状态机、缓存、共识、验签)
 │    ├── chain.py          Blockchain 类
 │    └── __init__.py
 ├── api/               # REST 路由 + 限流中间件
 │    ├── routes.py         14 个端点
 │    ├── middleware.py     滑动窗口限流 + Token 鉴权
 │    └── __init__.py
 ├── network/           # P2P 发现 + 自动同步
 │    ├── p2p.py            种子扩散 + 端口扫描
 │    └── __init__.py
 ├── utils/             # 密码学 + 存储抽象
 │    ├── crypto.py         SHA256 哈希 + Merkle 树
 │    ├── storage.py        JsonFile / Sqlite 双后端
 │    └── __init__.py
 ├── templates/
 │    └── index.html        LXC-Coin 控制台
 ├── .github/workflows/     CI 流水线
 │    └── ci.yml
 ├── config.py              配置中心 (难度 / 端口 / 缓存 TTL)
 ├── main.py                启动入口
 ├── test_blockchain.py     18 个 pytest 用例
 ├── Dockerfile
 ├── docker-compose.yml
 └── requirements.txt
```

**依赖方向：**

```text
main.py  -->  api  -->  blockchain  -->  utils
         -->  network --> (blockchain 实例注入)
```

---

## 数据库结构

SQLite 模式下的关系型表设计，采用 WAL 日志模式提升并发性能：

| 表名 | 字段 | 说明 |
|------|------|------|
| `blocks` | `block_index, timestamp, nonce, prev_hash, hash, merkle_root` | 区块核心数据，主键 `block_index` |
| `transactions` | `block_index, txid, sender, receiver, amount, fee, nonce, signature` | 每笔交易一行，外键关联 `blocks.block_index` |
| `wallet_data` | `id, public_key, private_key` | 矿工钱包，仅一行记录 |

每次挖矿产块时增量 INSERT 新区块和交易行，而非全量覆盖 JSON。

---

## 核心模块

| 模块 | 文件 | 职责 |
|------|------|------|
| 区块链引擎 | `blockchain/chain.py` | 交易入池、PoW 挖矿、链验证（含状态机重演）、余额缓存、共识同步 |
| API 路由 | `api/routes.py` | 14 个 REST 端点，通过 `register_routes(app, bc)` 注入 |
| 安全中间件 | `api/middleware.py` | 滑动窗口 IP 限流 + Bearer Token 鉴权，兼容反向代理 |
| P2P 网络 | `network/p2p.py` | 种子节点扩散 + 局域网端口扫描 + 后台自动同步线程 |
| 密码学工具 | `utils/crypto.py` | SHA-256 哈希 (`get_hash`) + Merkle 树根 (`get_merkle_root`) |
| 持久化层 | `utils/storage.py` | `BaseStorage` 抽象基类，`JsonFileStorage` / `SqliteStorage` 双实现 |
| 配置中心 | `config.py` | 全系统常量集中管理，支持环境变量覆盖 |
| 启动入口 | `main.py` | 应用工厂 `create_app()`，线程启动、参数解析 |
| 单元测试 | `test_blockchain.py` | 18 个 pytest 用例，含 PoW / Merkle / 签名 / 防重放 / 字符串化验证 |

---

## 安全防护

LXC-Coin 从设计之初就将安全性作为第一优先级，针对 Web3 领域常见的攻击向量实施了多层纵深防御。

### 漏洞防护矩阵

| 漏洞类型 | 防护措施 | 实现位置 |
|----------|---------|----------|
| XSS (跨站脚本) | 所有用户输入在插入 DOM 前通过 `esc()` 函数进行 HTML 实体编码 (`< > & " '`) | `index.html` |
| SSRF (服务端请求伪造) | `_is_safe_node()` 白名单校验，仅允许向 `127.0.0.1` / `localhost` 发起 P2P 请求 | `chain.py` / `p2p.py` |
| CSRF (跨站请求伪造) | 滑动窗口限流 + 可选 Bearer Token 鉴权 + `X-Frame-Options: DENY` 响应头 | `middleware.py` / `main.py` |
| Clickjacking | `X-Frame-Options: DENY` 防止浏览器将页面嵌入 `<iframe>` | `main.py` |
| MIME 嗅探 | `X-Content-Type-Options: nosniff` 阻止浏览器猜测响应类型 | `main.py` |
| 代码注入 | `Content-Security-Policy` 严格限制脚本/样式来源，仅允许白名单 CDN | `main.py` |
| 重放攻击 | 全局 `nonce_map` 严格递增校验 + TxID 去重 | `chain.py` |
| 双花攻击 | 状态机重演验证：P2P 同步时逐块重演余额，透支交易直接拒绝 | `chain.py` |
| SQL 注入 | 持久化层 100% 使用参数化查询 (`?` 占位符)，杜绝字符串拼接 | `storage.py` |
| Content-Type 攻击 | 所有 POST 端点强制校验 `Content-Type: application/json`，非 JSON 返回 415 | `routes.py` |
| 整数溢出 | 全链路 `Decimal` 精度引擎，消除浮点舍入误差 | `chain.py` |
| DoS (拒绝服务) | IP 级滑动窗口速率限制 (60 秒 / 120 次) + 请求体 1 MB 上限 | `middleware.py` / `main.py` |
| 信息泄露 | 私钥禁止出现在日志输出中，API 接口 `/node/wallet` 仅返回公钥 | `main.py` / `routes.py` |
| 引用泄露 | `Referrer-Policy: strict-origin-when-cross-origin` 限制跨域 Referer 发送 | `main.py` |

### XSS 防护细节

前端 `index.html` 中实现了一个统一的 HTML 转义函数：

```javascript
const esc = str => String(str).replace(/[&<>'"]/g, c =>
    ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c] || c)
);
```

所有来自区块链网络的动态数据（地址、哈希、TxID）在插入 `innerHTML` 之前均经过此函数转义：

- `renderExploreResults()`：用户查询关键词 + 服务端返回的 sender/receiver/txid 全部转义
- `renderBlockchain()`：区块哈希、交易哈希、地址前缀全部转义后渲染

### 等保 2.0 合规对照

LXC-Coin 的安全架构对标 GB/T 22239-2019《信息安全技术 网络安全等级保护基本要求》
第二级和第三级中适用于区块链信息系统的安全控制项：

| 等保 2.0 控制项 | 对应条款 | LXC-Coin 实现 |
|---------------|---------|--------------|
| 身份鉴别 | 8.1.4.2 a) | ECDSA 椭圆曲线签名 + nonce 防重放，实现双因子身份鉴别 |
| 访问控制 | 8.1.4.3 a) | 滑动窗口限流 + Bearer Token 鉴权，粒度到 IP 级 |
| 安全审计 | 8.1.4.4 a)-d) | `RotatingFileHandler` 滚动日志（2 MB × 7 份），记录鉴权失败、频率超限、SSRF 阻断、链验证异常等安全事件 |
| 入侵防范 | 8.1.4.5 a) | XSS 防护 (CSP + HTML 实体转义)、SSRF 白名单、SQL 注入参数化、Clickjacking 防护、MIME 嗅探防护 |
| 数据完整性 | 8.1.4.6 a) | SHA-256 哈希链 + Merkle 树 + 状态机重演验证，任意篡改即时可检测 |
| 数据保密性 | 8.1.4.6 b) | 私钥禁止出现在日志和 API 响应中，仅存储在数据库文件 |
| 通信保密性 | 8.1.4.6 c) | 安全响应头 (CSP / HSTS-ready / Referrer-Policy) |
| 安全标记 | 8.1.4.7 | 审计日志中标记 `AUDIT` 前缀的安全事件，可按关键词快速检索 |
| 可信验证 | 8.1.4.8 | PoW 工作量证明 + `valid_chain()` 全量验证含状态机重演 |

**审计日志示例：**

```
2026-05-10 17:04:27 [WARNING] audit: AUDIT: 频率限制触发 | IP=192.168.1.5 Requests=120/120 Window=60s
2026-05-10 17:04:30 [WARNING] blockchain.chain: AUDIT: SSRF 阻断 | 目标=10.0.0.1:5000
2026-05-10 17:04:33 [WARNING] audit: AUDIT: 鉴权失败 | IP=192.168.1.6 Path=/mine
```

### 安全提示

1. **私钥安全**：矿工私钥在本地生成并仅存储在 `data/` 目录的数据库文件中，切勿将此文件泄露
2. **数据备份**：定期备份 `data/` 目录（含完整链数据和钱包密钥对）
3. **生产部署**：对外暴露时务必设置 `BLOCKCHAIN_API_TOKEN` 环境变量启用 Bearer Token 鉴权
4. **Flask 开发服务器**：当前使用内置 WSGI 服务器，生产环境请通过 `gunicorn` 或 Nginx 反向代理部署

---

## 代码示例

以下示例可在 Python 交互环境中直接运行，用于快速理解核心 API 用法：

### 创建钱包与发起交易

```python
from blockchain import Blockchain
from ecdsa import SigningKey, SECP256k1

bc = Blockchain()
bc.init_storage(port=5090)

# 本地生成钱包
sk = SigningKey.generate(curve=SECP256k1)
addr = sk.verifying_key.to_string().hex()

# 签名并提交交易
nonce = "N-1001"
msg = f"{bc.node_public_key}->{addr}:5.00000000 fee:0.00000000 nonce:{nonce}"
sig = SigningKey.from_string(
    bytes.fromhex(bc.node_private_key), curve=SECP256k1
).sign(msg.encode('utf-8')).hex()

ok, info = bc.new_transaction(bc.node_public_key, addr, 5, 0, nonce, sig)
print(ok, info)  # True, 2
```

### 挖矿打包

```python
# 寻找有效 nonce 并生成新区块
block = bc.new_block(
    prev_hash=bc.last_block['hash'],
    nonce=bc.proof_of_work(bc.last_block['hash']),
)
print(block['index'], block['hash'][:16])  # 2, "0000abc123..."
```

### 链验证

```python
# 全量验证（含状态机重演）
assert bc.valid_chain(bc.chain) is True

# 篡改检测
bc.chain[1]['transactions'][0]['amount'] = 999999
assert bc.valid_chain(bc.chain) is False
```

---

## 快速启动

### 方式一：本地运行

环境要求：Python 3.10+

```bash
git clone https://github.com/your-username/lxc-coin.git
cd lxc-coin

pip install -r requirements.txt
python main.py -p 5000
```

启动后访问 `http://127.0.0.1:5000`

**启动多节点 P2P 网络：**

```bash
python main.py -p 5000   # 节点 0
python main.py -p 5001   # 节点 1（自动发现 5000）
python main.py -p 5002   # 节点 2（自动发现 5000/5001）
```

### 方式二：Docker 一键部署

```bash
docker-compose up -d --build
```

自动启动 3 个互联容器（`lxc-node-0` / `lxc-node-1` / `lxc-node-2`），分别映射 `5000/5001/5002` 端口。

```bash
docker-compose down    # 停止并清理
```

---

## LXC-Coin 使用指南

| 功能 | 操作 |
|------|------|
| 生成钱包 | 侧边栏 -> 钱包管理 -> 生成本地钱包 |
| 挖矿 | 矿工控制台 -> 开始挖矿打包（奖励 50 币 + 交易手续费） |
| 转账 | 点击 "打开发起转账" -> 点击对等节点填入接收方 -> 输入私钥签名 |
| 全网搜索 | 搜索引擎 -> 粘贴地址/哈希/TxID -> 一键追踪 |
| 数据可视化 | 点击 "查看链上数据图表" -> 矿工余额走势 + 累计手续费收益 |
| 区块导航 | 右侧时间线 -> hover 展开全部区块号 -> 点击跳转 |

---

## API 端点

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/` | LXC-Coin 前端界面 |
| `GET` | `/chain` | 完整区块链数据 |
| `GET` | `/mine` | PoW 挖矿（打包内存池） |
| `POST` | `/transactions/new` | 提交已签名交易 |
| `GET` | `/explore/<query>` | 搜索地址/哈希/TxID |
| `GET` | `/balance/<address>` | 查询余额 |
| `GET` | `/node/wallet` | 获取矿工公钥 |
| `GET` | `/peers` | 已发现节点列表 |
| `POST` | `/nodes/register` | 注册邻居节点 |
| `GET` | `/nodes/resolve` | 手动触发共识 |
| `GET` | `/stats` | 统计信息（Chart.js 数据源） |
| `GET` | `/health` | 健康检查 |

---

## 配置

所有常量集中在 `config.py`，支持环境变量覆盖：

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `MINING_REWARD` | `50` | 币基奖励 |
| `DIFFICULTY` | `"0000"` | PoW 难度目标（SHA256 前缀） |
| `STORAGE_BACKEND` | `sqlite` | 存储后端 (`sqlite` / `json`) |
| `BLOCKCHAIN_HOST` | `127.0.0.1` | 监听地址 |
| `BLOCKCHAIN_DATA_DIR` | `data` | 数据目录 |
| `SEED_NODES` | (空) | 种子节点列表，逗号分隔 |
| `API_AUTH_TOKEN` | (空) | 设置后启用 Bearer Token 鉴权 |
| `RATE_LIMIT_WINDOW` | `60` | 限流窗口（秒） |
| `STATS_CACHE_TTL` | `2.0` | Stats 缓存有效期（秒） |

---

## 单元测试与 CI/CD

```bash
# 运行全部测试
pytest test_blockchain.py -v --tb=short

# 代码规范检查
ruff check .

# 编译验证
python -c "import py_compile, glob; [py_compile.compile(f, doraise=True) for f in glob.glob('**/*.py', recursive=True) if '__pycache__' not in f]"
```

**测试覆盖范围：**

| 模块 | 用例数 |
|------|--------|
| SHA256 哈希 & Merkle 树 | 5 |
| PoW 工作量证明 | 3 |
| 链验证（创世 / 篡改 / 透支） | 4 |
| 交易（签名 / 余额 / 双花 / 重放） | 6 |

> 所有磁盘 I/O 测试均使用 `tmp_path` 夹具，不产生残留文件。

**GitHub Actions：** 每次 push 在 Python 3.10 / 3.11 / 3.12 三环境下自动运行编译检查 + Ruff 扫描 + 全量单元测试编译检查 + Ruff 扫描 + 全量单元测试。

---

## 常见问题

### 为什么余额查询是 O(1) 而不是 O(N)？

系统维护 `balance_cache` 字典，在 `new_block()` 时增量更新。查询直接从缓存读取，不遍历区块链。仅在节点冷启动时执行一次 O(N) 的 `_rebuild_caches()`。

### 为什么存 SQLite 而不是 Postgres？

LXC-Coin 是单节点仿真系统，SQLite 零配置、零依赖、WAL 模式支持并发读，且增量写入接口已抽象 -- 切换到 Postgres 只需实现新的 `BaseStorage` 子类。

### 为什么用字典而不是 OOP 封装 Transaction/Block 类？

区块链数据需在 JSON 序列化、HTTP 传输、`json.dumps` 之间频繁转换。Python 字典天然可序列化，避免手写 `to_dict()` / `from_dict()` 编码器。课程报告中将此列为 "可展望的 OOP 重构方向"。

---

## 故障排查

| 症状 | 可能原因 | 排查方法 |
|------|---------|----------|
| 节点间不同步 | 旧数据残留导致创世块哈希不一致 | 停掉所有节点 → `Remove-Item data\* -Force` → 重启 |
| 搜索结果空白 | 查询地址长度 < 64（非公钥格式） | 确认输入为 128 位十六进制公钥 |
| 挖矿失败 | 服务器未启动或端口被占用 | 检查终端是否有 `Running on http://127.0.0.1:5000` |
| import 报错 | 缺少依赖 | `pip install -r requirements.txt` |
| Docker 构建失败 | DNS 解析不可达 | 虚拟机环境执行 `sudo docker-compose up -d --build` |

---

## 局限与展望

| 方向 | 当前状态 | 改进方案 |
|------|---------|----------|
| 状态存储 | `balance_cache` Dict | 可升级为 MPT 状态树，支持状态证明 |
| 难度调整 | 固定 `"0000"` | 引入基于出块间隔的动态难度 |
| P2P 协议 | HTTP API 拉取 | 可改为 Gossip 协议降低带宽 |
| 轻节点 | Merkle 树已实现 | 增加 SPV 验证接口 |
| API 认证 | Bearer Token | 可接入 JWT + OAuth2 |

---

## License

MIT License
