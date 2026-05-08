import os

# ------ 挖矿 & 共识 ------
MINING_REWARD = 50
DIFFICULTY = "0000"
FLOAT_TOLERANCE = 1e-8

# ------ 网络 ------
HOST = os.getenv("BLOCKCHAIN_HOST", "127.0.0.1")
DEFAULT_PORT = int(os.getenv("BLOCKCHAIN_PORT", 5000))
NETWORK_SCAN_START = 5000
NETWORK_SCAN_END = 5006
NETWORK_TIMEOUT = 1
SYNC_TIMEOUT = 2
SYNC_INTERVAL = 5
DISCOVERY_DELAY = 3

# ------ 精度 ------
DECIMAL_PRECISION = 18

# ------ 存储 ------
STORAGE_BACKEND = os.getenv("BLOCKCHAIN_STORAGE", "sqlite")
CHAIN_FILE_TEMPLATE = "chain_{port}.json"
WALLET_FILE_TEMPLATE = "wallet_{port}.json"
DB_FILE_TEMPLATE = "blockchain_{port}.db"
DATA_DIR = os.getenv("BLOCKCHAIN_DATA_DIR", "data")

# ------ API 限流 ------
RATE_LIMIT_WINDOW = 60
RATE_LIMIT_MAX_REQUESTS = 120
API_AUTH_TOKEN = os.getenv("BLOCKCHAIN_API_TOKEN", "")

# ------ 统计缓存 ------
STATS_CACHE_TTL = 2.0

# ------ P2P 种子节点 ------
SEED_NODES = os.getenv("BLOCKCHAIN_SEEDS", "")
