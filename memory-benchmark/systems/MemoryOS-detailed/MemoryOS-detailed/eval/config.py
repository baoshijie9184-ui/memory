# eval/config.py
# API 配置文件 - 集中管理所有API参数

# --- 基本配置 ---
API_KEY = "sk-mryilyrekolvgxtjxucccuzgpgbaatifdmcsnsdthaxqvbeh"
BASE_URL = "https://api.siliconflow.cn/v1"
LLM_MODEL = "deepseek-ai/DeepSeek-V3.2"
EMBEDDING_MODEL = "Qwen/Qwen3-Embedding-8B"

# --- Embedding 配置 ---
USE_EMBEDDING_API = True  # 设置为 True 使用 API embedding，False 使用本地模型

# --- 记忆系统配置 ---
H_THRESHOLD = 5.0  # 热度阈值
MID_TERM_CAPACITY = 200  # 中期记忆容量
SHORT_TERM_CAPACITY = 7  # 短期记忆容量
