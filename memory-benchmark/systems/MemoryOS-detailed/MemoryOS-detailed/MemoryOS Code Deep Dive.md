# MemoryOS 项目代码详解

## 一、项目概述与架构图解析

MemoryOS 采用了**三层记忆架构**，模拟人类记忆系统：

### 核心组件说明：

| 组件 | 全称 | 作用 | 对应代码文件 |
|------|------|------|--------------|
| **STM** | Short-Term Memory（短期记忆） | FIFO队列存储最近对话，容量有限 | [short_term.py](cci:7://file:///d:/PyCode/Memory/MemoryOS/memoryos-pypi/short_term.py:0:0-0:0) |
| **MTM** | Mid-Term Memory（中期记忆） | 以 Segment（会话）形式存储，按热度(Heat)管理 | [mid_term.py](cci:7://file:///d:/PyCode/Memory/MemoryOS/memoryos-pypi/mid_term.py:0:0-0:0) |
| **LPM** | Long-term Persona Memory（长期记忆） | 存储用户画像、知识库等持久信息 | [long_term.py](cci:7://file:///d:/PyCode/Memory/MemoryOS/memoryos-pypi/long_term.py:0:0-0:0) |

---

## 二、入口文件 test.py 深度解析

```python
# ============= test.py 详解 =============

import os
from memoryos import Memoryos  # 导入核心类

# --- 基本配置 ---
USER_ID = "demo_user"           # 用户唯一标识
ASSISTANT_ID = "demo_assistant" # 助手唯一标识
API_KEY = "sk-xxx"              # OpenAI 兼容 API 密钥
BASE_URL = "https://api.siliconflow.cn/v1"  # API 服务地址
DATA_STORAGE_PATH = "./simple_demo_data"    # 数据持久化路径
LLM_MODEL = "deepseek-ai/DeepSeek-V3.2"     # LLM 模型名称
EMBEDDING_MODEL = "Qwen/Qwen3-Embedding-8B" # Embedding 模型名称

def simple_demo():
    # ========== 第一步：初始化 MemoryOS ==========
    memo = Memoryos(
        user_id=USER_ID,                    # 用户ID，用于区分不同用户的记忆
        openai_api_key=API_KEY,             # API密钥
        openai_base_url=BASE_URL,           # API服务地址
        data_storage_path=DATA_STORAGE_PATH,# 数据存储目录
        llm_model=LLM_MODEL,                # 用于生成/分析的LLM模型
        assistant_id=ASSISTANT_ID,          # 助手ID
        
        # ↓↓↓ 核心参数：控制三层记忆的行为 ↓↓↓
        short_term_capacity=7,              # 短期记忆容量（超过则转入中期）
        mid_term_heat_threshold=5,          # 中期记忆热度阈值（超过则更新长期画像）
        retrieval_queue_capacity=10,        # 检索时返回的最大页面数
        long_term_knowledge_capacity=100,   # 长期知识库容量
        mid_term_similarity_threshold=0.6,  # 中期记忆相似度阈值（判断是否合并会话）
        
        embedding_model_name=EMBEDDING_MODEL,  # Embedding模型
        use_embedding_api=True                  # 使用API调用embedding服务
    )
    
    # ========== 第二步：添加记忆 ==========
    # 每次 add_memory 都会：
    # 1. 将对话存入短期记忆（STM）
    # 2. 检查STM是否满了，满了则触发 STM → MTM 转换
    # 3. 检查MTM中是否有热度超标的会话，超标则更新LPM
    
    memo.add_memory(
        user_input="你好！我叫小明，是一名在上海工作的数据科学家。",
        agent_response="你好小明！很高兴认识你..."
    )
    # ... 更多对话 ...
    
    # ========== 第三步：检索+生成响应 ==========
    test_query = "你还记得我的工作是什么吗？"
    response = memo.get_response(query=test_query)
    # get_response 内部会：
    # 1. 从 STM/MTM/LPM 三层并行检索相关信息
    # 2. 组装 Prompt，调用 LLM 生成响应
    # 3. 将本次对话也存入记忆系统
```

---

## 三、核心类 memoryos.py 详解

### 3.1 类初始化流程

```python
class Memoryos:
    def __init__(self, user_id: str, 
                 openai_api_key: str, 
                 data_storage_path: str,
                 ...):
        # ========== 步骤1：保存配置参数 ==========
        self.user_id = user_id
        self.assistant_id = assistant_id
        self.data_storage_path = os.path.abspath(data_storage_path)
        self.llm_model = llm_model
        
        # ========== 步骤2：初始化 OpenAI 客户端 ==========
        self.client = OpenAIClient(api_key=openai_api_key, base_url=openai_base_url)
        
        # 如果使用 API embedding，设置全局 embedding 客户端
        if self.use_embedding_api:
            set_embedding_api_client(self.client)  # 设置全局变量供 get_embedding() 使用
        
        # ========== 步骤3：定义数据文件路径 ==========
        # 用户数据目录结构：
        # data_storage_path/
        # ├── users/
        # │   └── {user_id}/
        # │       ├── short_term.json      # 短期记忆
        # │       ├── mid_term.json        # 中期记忆
        # │       └── long_term_user.json  # 用户长期记忆（画像+知识）
        # └── assistants/
        #     └── {assistant_id}/
        #         └── long_term_assistant.json  # 助手知识库
        
        # ========== 步骤4：初始化三层记忆模块 ==========
        self.short_term_memory = ShortTermMemory(file_path=user_short_term_path, max_capacity=short_term_capacity)
        self.mid_term_memory = MidTermMemory(file_path=user_mid_term_path, client=self.client, ...)
        self.user_long_term_memory = LongTermMemory(file_path=user_long_term_path, ...)
        self.assistant_long_term_memory = LongTermMemory(file_path=assistant_long_term_path, ...)
        
        # ========== 步骤5：初始化协调器 ==========
        self.updater = Updater(...)   # 负责记忆层间的转换
        self.retriever = Retriever(...) # 负责记忆检索
```

### 3.2 add_memory() - 记忆添加的核心流程

```python
def add_memory(self, user_input: str, agent_response: str, timestamp: str = None, meta_data: dict = None):
    """
    添加一条新的 QA 对话到记忆系统
    
    核心流程（对应框图中的 "Push to STM" 和 "Insert to MTM"）：
    """
    # 步骤1：构建 QA 对（Page）
    if not timestamp:
        timestamp = get_timestamp()  # 自动生成时间戳
    
    qa_pair = {
        "user_input": user_input,
        "agent_response": agent_response,
        "timestamp": timestamp
    }
    
    # 步骤2：添加到短期记忆（对应框图 "Push to STM"）
    self.short_term_memory.add_qa_pair(qa_pair)
    # ↑ 内部使用 deque（双端队列），FIFO 策略自动淘汰最旧的
    
    # 步骤3：检查 STM 是否已满，满则转入 MTM
    if self.short_term_memory.is_full():
        print("Memoryos: Short-term memory full. Processing to mid-term.")
        self.updater.process_short_term_to_mid_term()
        # ↑ 这是 STM → MTM 转换的核心！详见 updater.py
    
    # 步骤4：检查是否需要触发 MTM → LPM 更新
    self._trigger_profile_and_knowledge_update_if_needed()
    # ↑ 当 MTM 中某个 Segment 热度超过阈值时，提取信息更新 LPM
```

### 3.3 _trigger_profile_and_knowledge_update_if_needed() - MTM→LPM 的关键

```python
def _trigger_profile_and_knowledge_update_if_needed(self):
    """
    检查中期记忆中的热门会话，如果热度超标则触发画像/知识更新
    
    对应框图中 "Heat > τ" → "Update to LPM" 的流程
    """
    if not self.mid_term_memory.heap:
        return
    
    # 步骤1：获取堆顶（最热的会话）
    # MTM 使用最小堆存储 (-heat, session_id)，所以堆顶是热度最高的
    neg_heat, sid = self.mid_term_memory.heap[0]
    current_heat = -neg_heat  # 取反得到真实热度
    
    # 步骤2：判断是否超过热度阈值 τ
    if current_heat >= self.mid_term_heat_threshold:
        session = self.mid_term_memory.sessions.get(sid)
        
        # 步骤3：获取该会话中未分析过的页面
        unanalyzed_pages = [p for p in session.get("details", []) if not p.get("analyzed", False)]
        
        if unanalyzed_pages:
            # 步骤4：并行执行两个 LLM 任务
            # 任务A：用户画像分析（直接输出完整的更新后画像）
            # 任务B：知识提取（提取用户私人知识 + 助手知识）
            
            with ThreadPoolExecutor(max_workers=2) as executor:
                future_profile = executor.submit(task_user_profile_analysis)
                future_knowledge = executor.submit(task_knowledge_extraction)
                
                updated_user_profile = future_profile.result()  # 新的完整画像
                knowledge_result = future_knowledge.result()    # {"private": ..., "assistant_knowledge": ...}
            
            # 步骤5：更新用户画像（对应框图 "User Profile" 和 "User Traits"）
            if updated_user_profile:
                self.user_long_term_memory.update_user_profile(self.user_id, updated_user_profile, merge=False)
            
            # 步骤6：添加用户私人知识（对应框图 "User KB"）
            if new_user_private_knowledge:
                for line in new_user_private_knowledge.split('\n'):
                    self.user_long_term_memory.add_user_knowledge(line.strip())
            
            # 步骤7：添加助手知识（对应框图 "Agent Traits"）
            if new_assistant_knowledge:
                for line in new_assistant_knowledge.split('\n'):
                    self.assistant_long_term_memory.add_assistant_knowledge(line.strip())
            
            # 步骤8：标记页面为已分析，重置热度因子
            for p in session["details"]:
                p["analyzed"] = True
            session["N_visit"] = 0          # 重置访问次数
            session["L_interaction"] = 0   # 重置交互长度
            session["H_segment"] = compute_segment_heat(session)  # 重新计算热度
            self.mid_term_memory.rebuild_heap()  # 重建堆
```

### 3.4 get_response() - 检索+生成响应

```python
def get_response(self, query: str, relationship_with_user="friend", ...) -> str:
    """
    生成对用户查询的响应
    
    对应框图中 "Query" → "Response Generation" 的完整流程
    """
    # ========== 第1步：从三层记忆并行检索 ==========
    # 对应框图顶部的三个 "Retrieve" 箭头
    retrieval_results = self.retriever.retrieve_context(
        user_query=query,
        user_id=self.user_id
    )
    
    # 检索结果包含：
    retrieved_pages = retrieval_results["retrieved_pages"]           # 从 MTM 检索的 Top-k Page
    retrieved_user_knowledge = retrieval_results["retrieved_user_knowledge"]      # 从 LPM 检索的用户知识
    retrieved_assistant_knowledge = retrieval_results["retrieved_assistant_knowledge"]  # 从 LPM 检索的助手知识
    
    # ========== 第2步：获取短期历史（FIFO） ==========
    short_term_history = self.short_term_memory.get_all()
    history_text = "\n".join([
        f"User: {qa.get('user_input', '')}\nAssistant: {qa.get('agent_response', '')} (Time: {qa.get('timestamp', '')})"
        for qa in short_term_history
    ])
    
    # ========== 第3步：格式化检索到的中期记忆页面（Top-k Page） ==========
    retrieval_text = "\n".join([
        f"【Historical Memory】\nUser: {page.get('user_input', '')}\nAssistant: {page.get('agent_response', '')}\n..."
        for page in retrieved_pages
    ])
    
    # ========== 第4步：获取用户画像（Relevant LPM） ==========
    user_profile_text = self.user_long_term_memory.get_raw_user_profile(self.user_id)
    
    # ========== 第5步：组装 Prompt ==========
    system_prompt_text = prompts.GENERATE_SYSTEM_RESPONSE_SYSTEM_PROMPT.format(
        relationship=relationship_with_user,
        assistant_knowledge_text=assistant_knowledge_text_for_prompt,
        meta_data_text=meta_data_text_for_prompt
    )
    
    user_prompt_text = prompts.GENERATE_SYSTEM_RESPONSE_USER_PROMPT.format(
        history_text=history_text,          # STM 的 FIFO 历史
        retrieval_text=retrieval_text,      # MTM 的 Top-k Page
        background=background_context,       # LPM 的 Relevant LPM
        relationship=relationship_with_user,
        query=query
    )
    
    # ========== 第6步：调用 LLM 生成响应 ==========
    response_content = self.client.chat_completion(
        model=self.llm_model,
        messages=[
            {"role": "system", "content": system_prompt_text},
            {"role": "user", "content": user_prompt_text}
        ],
        temperature=0.7,
        max_tokens=1500
    )
    
    # ========== 第7步：将本次交互存入记忆 ==========
    self.add_memory(user_input=query, agent_response=response_content, timestamp=get_timestamp())
    
    return response_content
```

---

## 四、短期记忆 short_term.py 详解

短期记忆是最简单的一层，核心是一个 **FIFO（先进先出）队列**：

```python
from collections import deque

class ShortTermMemory:
    def __init__(self, file_path, max_capacity=10):
        self.max_capacity = max_capacity  # 容量上限（如测试中的 7）
        self.file_path = file_path        # 持久化文件路径
        
        # 使用 Python 的 deque，设置 maxlen 后会自动淘汰最旧元素
        # 对应框图中 STM 的 "FIFO" 策略
        self.memory = deque(maxlen=max_capacity)
        self.load()  # 从文件加载已有记忆
    
    def add_qa_pair(self, qa_pair):
        """
        添加一条 QA 对话
        
        对应框图 "Push to STM" 操作
        """
        if 'timestamp' not in qa_pair or not qa_pair['timestamp']:
            qa_pair["timestamp"] = get_timestamp()
        
        self.memory.append(qa_pair)
        # ↑ 当 len(memory) > maxlen 时，deque 自动移除最左边（最旧）的元素
        
        self.save()  # 持久化
    
    def get_all(self):
        """获取所有短期记忆（用于构建历史上下文）"""
        return list(self.memory)
    
    def is_full(self):
        """判断是否已满（触发 STM → MTM 转换的条件）"""
        return len(self.memory) >= self.max_capacity
    
    def pop_oldest(self):
        """
        弹出最旧的 QA 对
        
        对应框图 "Insert to MTM" 前的操作
        当 STM 满时，Updater 会调用此方法将旧数据转入 MTM
        """
        if self.memory:
            msg = self.memory.popleft()  # 从队首弹出
            self.save()
            return msg
        return None
```

**数据结构示例**（short_term.json）：
```json
[
  {
    "user_input": "你好！我叫小明，是一名在上海工作的数据科学家。",
    "agent_response": "你好小明！很高兴认识你...",
    "timestamp": "2026-01-28 14:13:57"
  },
  {
    "user_input": "我周末喜欢去爬山，特别是黄山和泰山。",
    "agent_response": "登山真是个好爱好！...",
    "timestamp": "2026-01-28 14:14:00"
  }
]
```

---

## 五、中期记忆 mid_term.py 详解

中期记忆是整个系统最复杂的部分，核心概念包括：

### 5.1 核心数据结构

```python
class MidTermMemory:
    def __init__(self, file_path: str, client: OpenAIClient, max_capacity=2000, ...):
        self.sessions = {}              # {session_id: session_object} 存储所有会话(Segment)
        self.access_frequency = defaultdict(int)  # {session_id: count} 用于 LFU 淘汰
        self.heap = []                  # 最小堆，存储 (-H_segment, session_id)，用于快速获取最热会话
```

### 5.2 Session（Segment）结构

对应框图中 MTM 区域的 **Segment**：

```python
session_obj = {
    "id": session_id,                    # 会话唯一标识
    "summary": summary,                  # 会话主题摘要（用于相似度匹配）
    "summary_keywords": summary_keywords,# 主题关键词（辅助匹配）
    "summary_embedding": summary_vec,    # 摘要的向量表示（用于 FAISS 检索）
    
    "details": processed_details,        # 包含的 Pages 列表（具体对话内容）
    
    # ↓↓↓ 核心：热度计算因子 ↓↓↓
    "L_interaction": len(processed_details),  # 交互长度（对话轮数）
    "R_recency": 1.0,                         # 时间衰减因子
    "N_visit": 0,                             # 访问次数
    "H_segment": 0.0,                         # 热度值（由上述因子计算）
    
    "timestamp": current_ts,             # 创建时间
    "last_visit_time": current_ts,       # 最后访问时间
    "access_count_lfu": 0                # LFU 淘汰用的访问计数
}
```

### 5.3 热度计算公式

对应框图中 Segment 旁边的 **Heat** 标注：

```python
# 热度计算常量（可调参数）
HEAT_ALPHA = 1.0    # 访问次数权重
HEAT_BETA = 1.0     # 交互长度权重
HEAT_GAMMA = 1      # 时间衰减权重
RECENCY_TAU_HOURS = 24  # 时间衰减的半衰期（小时）

def compute_segment_heat(session, alpha=HEAT_ALPHA, beta=HEAT_BETA, gamma=HEAT_GAMMA, tau_hours=RECENCY_TAU_HOURS):
    """
    计算会话热度的核心公式：
    
    H_segment = α × N_visit + β × L_interaction + γ × R_recency
    
    其中：
    - N_visit: 该会话被检索/访问的次数（越常被提起越热）
    - L_interaction: 该会话包含的对话轮数（内容越丰富越热）
    - R_recency: 时间衰减因子，R = exp(-Δt / τ)（越近越热）
    """
    N_visit = session.get("N_visit", 0)
    L_interaction = session.get("L_interaction", 0)
    
    # 计算时间衰减 R_recency
    R_recency = 1.0
    if session.get("last_visit_time"):
        R_recency = compute_time_decay(session["last_visit_time"], get_timestamp(), tau_hours)
    
    session["R_recency"] = R_recency  # 更新到会话对象
    return alpha * N_visit + beta * L_interaction + gamma * R_recency
```

**热度衰减图示**：
```
R_recency
  1.0 ┼────╮
       │     ╲
  0.5 ┼       ╲
       │         ╲___
  0.0 ┼──────────────────▶ time
       0   24h   48h   72h
```

### 5.4 会话插入/合并逻辑

对应框图中 **Dialogue Chain** 和 **Insert to MTM** 的流程：

```python
def insert_pages_into_session(self, summary_for_new_pages, keywords_for_new_pages, pages_to_insert, 
                              similarity_threshold=0.6, keyword_similarity_alpha=1.0):
    """
    将新页面插入到中期记忆
    
    核心决策：是合并到已有会话，还是创建新会话？
    """
    if not self.sessions:  # 没有已有会话，直接创建新的
        return self.add_session(summary_for_new_pages, pages_to_insert, keywords_for_new_pages)
    
    # 步骤1：计算新内容的 embedding
    new_summary_vec = get_embedding(summary_for_new_pages, ...)
    new_summary_vec = normalize_vector(new_summary_vec)
    
    # 步骤2：遍历所有已有会话，找最相似的
    best_sid = None
    best_overall_score = -1
    
    for sid, existing_session in self.sessions.items():
        existing_summary_vec = np.array(existing_session["summary_embedding"], dtype=np.float32)
        
        # 语义相似度（余弦相似度，因为向量已归一化）
        semantic_sim = float(np.dot(existing_summary_vec, new_summary_vec))
        
        # 关键词相似度（Jaccard 指数）
        existing_keywords = set(existing_session.get("summary_keywords", []))
        new_keywords_set = set(keywords_for_new_pages)
        s_topic_keywords = 0
        if existing_keywords and new_keywords_set:
            intersection = len(existing_keywords.intersection(new_keywords_set))
            union = len(existing_keywords.union(new_keywords_set))
            if union > 0:
                s_topic_keywords = intersection / union
        
        # 综合得分 = 语义相似度 + α × 关键词相似度
        overall_score = semantic_sim + keyword_similarity_alpha * s_topic_keywords
        
        if overall_score > best_overall_score:
            best_overall_score = overall_score
            best_sid = sid
    
    # 步骤3：决策 - 合并还是新建？
    if best_sid and best_overall_score >= similarity_threshold:
        # 合并到已有会话（对应框图 "Dialogue Chain" 的链接关系）
        print(f"MidTermMemory: Merging pages into session {best_sid}")
        target_session = self.sessions[best_sid]
        
        # 将新页面添加到已有会话
        for page_data in pages_to_insert:
            # ... 处理页面embedding等 ...
            target_session["details"].append(processed_page)
        
        # 更新热度
        target_session["L_interaction"] += len(pages_to_insert)
        target_session["last_visit_time"] = get_timestamp()
        target_session["H_segment"] = compute_segment_heat(target_session)
        self.rebuild_heap()
        return best_sid
    else:
        # 创建新会话
        print(f"MidTermMemory: Creating new session (best score {best_overall_score:.2f} < threshold)")
        return self.add_session(summary_for_new_pages, pages_to_insert, keywords_for_new_pages)
```

### 5.5 会话检索逻辑

对应框图中 **Retrieve** → **Top-m Segment** → **Top-k Page** 的流程：

```python
def search_sessions(self, query_text, segment_similarity_threshold=0.1, page_similarity_threshold=0.1, 
                    top_k_sessions=5, keyword_alpha=1.0, recency_tau_search=3600):
    """
    根据查询文本检索相关会话和页面
    
    两阶段检索：
    1. 先找 Top-m 个相关的 Segment
    2. 再从每个 Segment 中找相关的 Page
    """
    # 步骤1：计算查询的 embedding
    query_vec = get_embedding(query_text, ...)
    query_vec = normalize_vector(query_vec)
    
    # 步骤2：构建 FAISS 索引进行高效检索
    session_ids = list(self.sessions.keys())
    summary_embeddings_list = [self.sessions[s]["summary_embedding"] for s in session_ids]
    summary_embeddings_np = np.array(summary_embeddings_list, dtype=np.float32)
    
    dim = summary_embeddings_np.shape[1]
    index = faiss.IndexFlatIP(dim)  # 使用内积（等价于余弦相似度，因为已归一化）
    index.add(summary_embeddings_np)
    
    # 步骤3：检索 Top-k 相关会话（对应 Top-m Segment）
    query_arr_np = np.array([query_vec], dtype=np.float32)
    distances, indices = index.search(query_arr_np, min(top_k_sessions, len(session_ids)))
    
    results = []
    for i, idx in enumerate(indices[0]):
        if idx == -1:
            continue
        
        session_id = session_ids[idx]
        session = self.sessions[session_id]
        semantic_sim_score = float(distances[0][i])
        
        # 结合关键词相似度
        session_relevance_score = (semantic_sim_score + keyword_alpha * s_topic_keywords)
        
        if session_relevance_score >= segment_similarity_threshold:
            # 步骤4：从该会话中检索相关页面（对应 Top-k Page）
            matched_pages_in_session = []
            for page in session.get("details", []):
                page_embedding = np.array(page["page_embedding"], dtype=np.float32)
                page_sim_score = float(np.dot(page_embedding, query_vec))
                
                if page_sim_score >= page_similarity_threshold:
                    matched_pages_in_session.append({"page_data": page, "score": page_sim_score})
            
            if matched_pages_in_session:
                # 步骤5：更新会话的访问统计（影响热度！）
                session["N_visit"] += 1           # 增加访问次数
                session["last_visit_time"] = current_time_str
                session["access_count_lfu"] = session.get("access_count_lfu", 0) + 1
                session["H_segment"] = compute_segment_heat(session)  # 重新计算热度
                self.rebuild_heap()
                
                results.append({
                    "session_id": session_id,
                    "session_summary": session["summary"],
                    "session_relevance_score": session_relevance_score,
                    "matched_pages": sorted(matched_pages_in_session, key=lambda x: x["score"], reverse=True)
                })
    
    return sorted(results, key=lambda x: x["session_relevance_score"], reverse=True)
```

### 5.6 LFU 淘汰策略

对应框图中 **Delete Segment**（❄️冷冻图标）的机制：

```python
def evict_lfu(self):
    """
    LFU (Least Frequently Used) 淘汰策略
    当中期记忆超容量时，删除访问次数最少的会话
    
    对应框图中 Segment 被 "冻结" 删除的逻辑
    """
    if not self.access_frequency or not self.sessions:
        return
    
    # 找到访问频率最低的会话
    lfu_sid = min(self.access_frequency, key=self.access_frequency.get)
    print(f"MidTermMemory: LFU eviction. Session {lfu_sid} has lowest access frequency.")
    
    # 从内存中删除
    session_to_delete = self.sessions.pop(lfu_sid)
    del self.access_frequency[lfu_sid]
    
    self.rebuild_heap()
    self.save()
```

---

## 六、长期记忆 long_term.py 详解

长期记忆存储**持久化**的用户画像和知识，对应框图右侧的 **LPM** 模块：

### 6.1 核心数据结构

```python
class LongTermMemory:
    def __init__(self, file_path, knowledge_capacity=100, ...):
        self.knowledge_capacity = knowledge_capacity
        
        # 用户画像（对应框图 "User Persona" → "User Profile" 和 "User Traits"）
        self.user_profiles = {}  # {user_id: {"data": "profile_string", "last_updated": "timestamp"}}
        
        # 用户知识库（对应框图 "User KB"）
        # 使用固定容量的 deque，满了自动淘汰最旧的
        self.knowledge_base = deque(maxlen=self.knowledge_capacity)
        
        # 助手知识库（对应框图 "Agent Persona" → "Agent Traits"）
        self.assistant_knowledge = deque(maxlen=self.knowledge_capacity)
```

### 6.2 用户画像更新

对应框图 **Update to LPM** 中的 **User Profile** 部分：

```python
def update_user_profile(self, user_id, new_data, merge=True):
    """
    更新用户画像
    
    merge=True: 将新信息追加到旧画像后（保留历史）
    merge=False: 完全替换为新画像（用于综合分析后的覆盖）
    """
    if merge and user_id in self.user_profiles and self.user_profiles[user_id].get("data"):
        # 合并模式：追加新内容
        current_data = self.user_profiles[user_id]["data"]
        updated_data = f"{current_data}\n\n--- Updated on {get_timestamp()} ---\n{new_data}"
    else:
        # 替换模式：直接使用新数据
        updated_data = new_data
    
    self.user_profiles[user_id] = {
        "data": updated_data,
        "last_updated": get_timestamp()
    }
    self.save()
```

### 6.3 知识库添加

```python
def add_knowledge_entry(self, knowledge_text, knowledge_deque: deque, type_name="knowledge"):
    """
    添加知识条目到知识库
    
    每条知识都会计算 embedding 用于后续检索
    """
    if not knowledge_text or knowledge_text.strip().lower() in ["", "none", "- none"]:
        return
    
    # 计算知识的向量表示
    vec = get_embedding(knowledge_text, ...)
    vec = normalize_vector(vec).tolist()
    
    entry = {
        "knowledge": knowledge_text,       # 知识内容
        "timestamp": get_timestamp(),      # 记录时间
        "knowledge_embedding": vec         # 向量表示（用于语义检索）
    }
    
    knowledge_deque.append(entry)
    # ↑ deque 设置了 maxlen，满了会自动淘汰最旧的
    
    self.save()

def add_user_knowledge(self, knowledge_text):
    """添加用户私人知识（对应框图 "User KB"）"""
    self.add_knowledge_entry(knowledge_text, self.knowledge_base, "user knowledge")

def add_assistant_knowledge(self, knowledge_text):
    """添加助手知识（对应框图 "Agent Traits"）"""
    self.add_knowledge_entry(knowledge_text, self.assistant_knowledge, "assistant knowledge")
```

### 6.4 知识检索

对应框图中 **Retrieve** → **Relevant LPM** 的流程：

```python
def _search_knowledge_deque(self, query, knowledge_deque: deque, threshold=0.1, top_k=5):
    """
    使用 FAISS 进行语义检索
    """
    query_vec = get_embedding(query, ...)
    query_vec = normalize_vector(query_vec)
    
    # 收集所有知识的 embedding
    embeddings = []
    valid_entries = []
    for entry in knowledge_deque:
        if "knowledge_embedding" in entry and entry["knowledge_embedding"]:
            embeddings.append(np.array(entry["knowledge_embedding"], dtype=np.float32))
            valid_entries.append(entry)
    
    if not embeddings:
        return []
    
    # 构建 FAISS 索引
    embeddings_np = np.array(embeddings, dtype=np.float32)
    dim = embeddings_np.shape[1]
    index = faiss.IndexFlatIP(dim)  # 内积检索
    index.add(embeddings_np)
    
    # 检索
    query_arr = np.array([query_vec], dtype=np.float32)
    distances, indices = index.search(query_arr, min(top_k, len(valid_entries)))
    
    results = []
    for i, idx in enumerate(indices[0]):
        if idx != -1:
            similarity_score = float(distances[0][i])
            if similarity_score >= threshold:
                results.append(valid_entries[idx])
    
    return results
```

---

## 七、更新器 updater.py 详解

更新器负责**记忆层之间的转换**，核心是 STM → MTM 的处理：

```python
class Updater:
    def process_short_term_to_mid_term(self):
        """
        STM → MTM 转换的核心流程
        
        对应框图 "Insert to MTM" 的完整逻辑
        """
        # 步骤1：从 STM 弹出所有溢出的 QA 对
        evicted_qas = []
        while self.short_term_memory.is_full():
            qa = self.short_term_memory.pop_oldest()
            if qa and qa.get("user_input") and qa.get("agent_response"):
                evicted_qas.append(qa)
        
        # 步骤2：为每个 QA 创建 Page 结构，处理对话连续性
        current_batch_pages = []
        temp_last_page = self.last_evicted_page_for_continuity
        
        for qa_pair in evicted_qas:
            current_page_obj = {
                "page_id": generate_id("page"),
                "user_input": qa_pair.get("user_input", ""),
                "agent_response": qa_pair.get("agent_response", ""),
                "timestamp": qa_pair.get("timestamp", get_timestamp()),
                "preloaded": False,
                "analyzed": False,      # 尚未被 LPM 分析
                "pre_page": None,       # 前一页链接（对话链）
                "next_page": None,      # 后一页链接
                "meta_info": None       # 对话链的元信息摘要
            }
            
            # 步骤3：检查与上一条对话的连续性（LLM 判断）
            is_continuous = check_conversation_continuity(temp_last_page, current_page_obj, self.client, ...)
            
            if is_continuous and temp_last_page:
                # 建立对话链接（对应框图 "Dialogue Chain"）
                current_page_obj["pre_page"] = temp_last_page["page_id"]
                
                # 更新/生成元信息摘要
                new_meta = generate_page_meta_info(last_meta, current_page_obj, self.client, ...)
                current_page_obj["meta_info"] = new_meta
            else:
                # 新对话链的开始
                current_page_obj["meta_info"] = generate_page_meta_info(None, current_page_obj, ...)
            
            current_batch_pages.append(current_page_obj)
            temp_last_page = current_page_obj
        
        # 步骤4：生成多主题摘要（用于 MTM 会话匹配）
        input_text_for_summary = "\n".join([
            f"User: {p.get('user_input','')}\nAssistant: {p.get('agent_response','')}" 
            for p in current_batch_pages
        ])
        
        multi_summary_result = gpt_generate_multi_summary(input_text_for_summary, self.client, ...)
        # 返回格式：{"summaries": [{"theme": "...", "keywords": [...], "content": "..."}]}
        
        # 步骤5：按主题将页面插入 MTM（可能合并到已有会话或创建新会话）
        if multi_summary_result and multi_summary_result.get("summaries"):
            for summary_item in multi_summary_result["summaries"]:
                theme_summary = summary_item.get("content", "...")
                theme_keywords = summary_item.get("keywords", [])
                
                self.mid_term_memory.insert_pages_into_session(
                    summary_for_new_pages=theme_summary,
                    keywords_for_new_pages=theme_keywords,
                    pages_to_insert=current_batch_pages,
                    similarity_threshold=self.topic_similarity_threshold
                )
```

---

## 八、检索器 retriever.py 详解

检索器负责从三层记忆中**并行检索**相关信息：

```python
class Retriever:
    def retrieve_context(self, user_query: str, user_id: str, ...):
        """
        并行检索上下文
        
        对应框图顶部三个 "Retrieve" 箭头的并行执行
        """
        print(f"Retriever: Starting PARALLEL retrieval...")
        
        # 定义三个并行任务
        tasks = [
            lambda: self._retrieve_mid_term_context(user_query, ...),    # ① 从 MTM 检索
            lambda: self._retrieve_user_knowledge(user_query, ...),      # ② 从用户 LPM 检索
            lambda: self._retrieve_assistant_knowledge(user_query, ...)  # ③ 从助手 LPM 检索
        ]
        
        # 使用线程池并行执行
        with ThreadPoolExecutor(max_workers=3) as executor:
            futures = []
            for i, task in enumerate(tasks):
                future = executor.submit(task)
                futures.append((i, future))
            
            results = [None] * 3
            for task_idx, future in futures:
                results[task_idx] = future.result()
        
        retrieved_mid_term_pages, retrieved_user_knowledge, retrieved_assistant_knowledge = results
        
        return {
            "retrieved_pages": retrieved_mid_term_pages,               # Top-k Page
            "retrieved_user_knowledge": retrieved_user_knowledge,      # 用户知识
            "retrieved_assistant_knowledge": retrieved_assistant_knowledge,  # 助手知识
            "retrieved_at": get_timestamp()
        }
    
    def _retrieve_mid_term_context(self, user_query, ...):
        """
        从中期记忆检索
        
        使用堆来维护 Top-k 页面
        """
        matched_sessions = self.mid_term_memory.search_sessions(query_text=user_query, ...)
        
        # 使用最小堆获取得分最高的 K 个页面
        top_pages_heap = []
        page_counter = 0
        
        for session_match in matched_sessions:
            for page_match in session_match.get("matched_pages", []):
                page_data = page_match["page_data"]
                page_score = page_match["score"]
                
                if len(top_pages_heap) < self.retrieval_queue_capacity:
                    heapq.heappush(top_pages_heap, (page_score, page_counter, page_data))
                    page_counter += 1
                elif page_score > top_pages_heap[0][0]:
                    heapq.heappop(top_pages_heap)
                    heapq.heappush(top_pages_heap, (page_score, page_counter, page_data))
                    page_counter += 1
        
        # 按得分降序返回
        retrieved_pages = [item[2] for item in sorted(top_pages_heap, key=lambda x: x[0], reverse=True)]
        return retrieved_pages
```

---

## 九、工具函数 utils.py 关键函数解析

### 9.1 Embedding 获取（支持本地模型和 API）

```python
def get_embedding(text, model_name="all-MiniLM-L6-v2", use_cache=True, use_api=False, **kwargs):
    """
    获取文本的 embedding 向量
    
    支持三种模式：
    1. API 模式 (use_api=True)：通过 OpenAI 兼容 API 调用
    2. BGE-M3 模式：使用 FlagEmbedding 库
    3. 默认模式：使用 SentenceTransformer
    """
    # 模式1：API 调用
    if use_api:
        return _get_embedding_via_api(text, model_name, use_cache)
    
    # 模式2 & 3：本地模型
    if 'bge-m3' in model_name.lower():
        from FlagEmbedding import BGEM3FlagModel
        model = BGEM3FlagModel(model_name, **init_kwargs)
        result = model.encode([text], **encode_kwargs)
        embedding = result['dense_vecs'][0]
    else:
        from sentence_transformers import SentenceTransformer
        model = SentenceTransformer(model_name, **init_kwargs)
        embedding = model.encode([text], **encode_kwargs)[0]
    
    return embedding

def _get_embedding_via_api(text, model_name, use_cache=True):
    """
    通过 API 获取 embedding
    """
    response = _embedding_api_client.client.embeddings.create(
        model=model_name,
        input=text
    )
    embedding = np.array(response.data[0].embedding, dtype=np.float32)
    return embedding
```

### 9.2 时间衰减计算

```python
def compute_time_decay(event_timestamp_str, current_timestamp_str, tau_hours=24):
    """
    计算时间衰减因子
    
    公式：R = exp(-Δt / τ)
    
    其中 τ 是半衰期（以小时为单位）
    """
    from datetime import datetime
    fmt = "%Y-%m-%d %H:%M:%S"
    
    t_event = datetime.strptime(event_timestamp_str, fmt)
    t_current = datetime.strptime(current_timestamp_str, fmt)
    delta_hours = (t_current - t_event).total_seconds() / 3600.0
    
    return np.exp(-delta_hours / tau_hours)
```

### 9.3 LLM 调用函数

```python
def gpt_user_profile_analysis(dialogs, client, model, existing_user_profile):
    """
    用户画像分析
    
    基于 90 个人格维度分析用户特征，并与现有画像整合
    """
    # 构建对话文本
    conversation = "\n".join([
        f"User: {d.get('user_input','')} (Timestamp: {d.get('timestamp', '')})\nAssistant: ..."
        for d in dialogs
    ])
    
    messages = [
        {"role": "system", "content": prompts.PERSONALITY_ANALYSIS_SYSTEM_PROMPT},
        {"role": "user", "content": prompts.PERSONALITY_ANALYSIS_USER_PROMPT.format(
            conversation=conversation,
            existing_user_profile=existing_user_profile
        )}
    ]
    
    result = client.chat_completion(model=model, messages=messages)
    return result.strip()

def gpt_knowledge_extraction(dialogs, client, model):
    """
    知识提取
    
    从对话中提取用户私人数据和助手展示的知识
    """
    messages = [
        {"role": "system", "content": prompts.KNOWLEDGE_EXTRACTION_SYSTEM_PROMPT},
        {"role": "user", "content": prompts.KNOWLEDGE_EXTRACTION_USER_PROMPT.format(conversation=conversation)}
    ]
    
    result_text = client.chat_completion(model=model, messages=messages)
    
    # 解析结果
    # 【User Private Data】
    # - ...
    # 【Assistant Knowledge】
    # - ...
    
    return {
        "private": private_data,
        "assistant_knowledge": assistant_knowledge
    }
```

---

## 十、完整数据流总结

```
┌─────────────────────────────────────────────────────────────────────────┐
│                           MemoryOS 数据流                                │
└─────────────────────────────────────────────────────────────────────────┘

用户输入 ──────────────────────────────────────────────────────┐
    │                                                          │
    ▼                                                          │
┌──────────────────────────────────────────────────────────┐   │
│  add_memory(user_input, agent_response)                  │   │
│  ┌─────────────────────────────────────────────────────┐ │   │
│  │ 1. 构建 QA 对象                                      │ │   │
│  │    qa_pair = {user_input, agent_response, timestamp}│ │   │
│  └─────────────────────────────────────────────────────┘ │   │
│                        │                                  │   │
│                        ▼                                  │   │
│  ┌─────────────────────────────────────────────────────┐ │   │
│  │ 2. 推送到 STM (FIFO 队列)                            │ │   │
│  │    short_term_memory.add_qa_pair(qa_pair)           │ │   │
│  └─────────────────────────────────────────────────────┘ │   │
│                        │                                  │   │
│              ┌─────────┴─────────┐                       │   │
│              │  STM 是否满？     │                       │   │
│              └────────┬──────────┘                       │   │
│                  是 │    │ 否                            │   │
│                     ▼    └──────────────────────┐        │   │
│  ┌─────────────────────────────────────────────┐│        │   │
│  │ 3. STM → MTM 转换                           ││        │   │
│  │    updater.process_short_term_to_mid_term() ││        │   │
│  │                                              ││        │   │
│  │    a. 弹出溢出的 QA 对                       ││        │   │
│  │    b. 检查对话连续性 (LLM)                   ││        │   │
│  │    c. 建立 Dialogue Chain                   ││        │   │
│  │    d. 生成多主题摘要 (LLM)                   ││        │   │
│  │    e. 插入/合并到 MTM 会话                   ││        │   │
│  └─────────────────────────────────────────────┘│        │   │
│                        │◄────────────────────────┘        │   │
│                        ▼                                  │   │
│  ┌─────────────────────────────────────────────────────┐ │   │
│  │ 4. 检查 MTM 热度，可能触发 MTM → LPM 更新            │ │   │
│  │    _trigger_profile_and_knowledge_update_if_needed()│ │   │
│  │                                                      │ │   │
│  │    if 最热会话的 Heat > τ:                           │ │   │
│  │        a. 提取未分析的页面                           │ │   │
│  │        b. 并行执行：                                 │ │   │
│  │           - 用户画像分析 (LLM)                       │ │   │
│  │           - 知识提取 (LLM)                           │ │   │
│  │        c. 更新 User Profile                         │ │   │
│  │        d. 添加 User Knowledge                       │ │   │
│  │        e. 添加 Assistant Knowledge                  │ │   │
│  │        f. 重置会话热度因子                           │ │   │
│  └─────────────────────────────────────────────────────┘ │   │
└──────────────────────────────────────────────────────────┘   │
                                                               │
═══════════════════════════════════════════════════════════════╪═══
                                                               │
用户查询 ◄─────────────────────────────────────────────────────┘
    │
    ▼
┌──────────────────────────────────────────────────────────────────┐
│  get_response(query)                                             │
│  ┌────────────────────────────────────────────────────────────┐  │
│  │ 1. 并行检索三层记忆                                         │  │
│  │    retriever.retrieve_context()                            │  │
│  │                                                             │  │
│  │    ┌────────────┐  ┌────────────┐  ┌──────────────────┐    │  │
│  │    │  STM       │  │  MTM       │  │  LPM             │    │  │
│  │    │  FIFO历史  │  │ Top-k Page │  │ User Profile     │    │  │
│  │    │            │  │            │  │ User Knowledge   │    │  │
│  │    │            │  │            │  │ Assistant Knowledge│  │  │
│  │    └──────┬─────┘  └──────┬─────┘  └────────┬─────────┘    │  │
│  │           │               │                 │              │  │
│  │           └───────────────┼─────────────────┘              │  │
│  │                           ▼                                 │  │
│  └────────────────────────────────────────────────────────────┘  │
│                              │                                    │
│                              ▼                                    │
│  ┌────────────────────────────────────────────────────────────┐  │
│  │ 2. 组装 Prompt                                              │  │
│  │    - System Prompt: 角色设定 + 助手知识                      │  │
│  │    - User Prompt:                                           │  │
│  │        · history_text (STM FIFO)                            │  │
│  │        · retrieval_text (MTM Top-k Page)                    │  │
│  │        · background (LPM User Profile + Knowledge)          │  │
│  │        · query                                              │  │
│  └────────────────────────────────────────────────────────────┘  │
│                              │                                    │
│                              ▼                                    │
│  ┌────────────────────────────────────────────────────────────┐  │
│  │ 3. 调用 LLM 生成响应                                        │  │
│  │    client.chat_completion(...)                              │  │
│  └────────────────────────────────────────────────────────────┘  │
│                              │                                    │
│                              ▼                                    │
│  ┌────────────────────────────────────────────────────────────┐  │
│  │ 4. 将本次交互存入记忆                                        │  │
│  │    add_memory(query, response)  ───────────────────────────┼──┼──► 回到顶部
│  └────────────────────────────────────────────────────────────┘  │
│                              │                                    │
│                              ▼                                    │
│                         返回响应                                   │
└──────────────────────────────────────────────────────────────────┘
```

---

## 十一、关键算法总结

| 算法/机制 | 所在模块 | 作用 | 核心公式/逻辑 |
|-----------|----------|------|---------------|
| **FIFO 淘汰** | ShortTermMemory | 维护固定容量的短期记忆 | [deque(maxlen=N)](cci:1://file:///d:/PyCode/Memory/MemoryOS/memoryos-pypi/long_term.py:82:4-130:22) |
| **热度计算** | MidTermMemory | 衡量会话的"热门程度" | `H = α×N_visit + β×L_interaction + γ×R_recency` |
| **时间衰减** | utils.py | 计算记忆的新鲜度 | `R = exp(-Δt/τ)` |
| **语义相似度** | 多处 | 向量检索 | `similarity = dot(v1, v2)` (归一化后等于余弦) |
| **Jaccard 相似度** | MidTermMemory | 关键词匹配 | `J = |A∩B| / |A∪B|` |
| **LFU 淘汰** | MidTermMemory | 淘汰冷门会话 | `min(access_frequency)` |
| **堆维护** | MidTermMemory | 快速获取最热会话 | 最小堆存储 [(-heat, id)](cci:1://file:///d:/PyCode/Memory/MemoryOS/memoryos-pypi/long_term.py:142:4-152:74) |
| **FAISS 检索** | 多处 | 高效向量检索 | `IndexFlatIP` 内积检索 |
| **对话连续性检测** | Updater | 判断对话是否属于同一主题 | LLM 返回 `true/false` |
| **多主题摘要** | Updater | 提取对话的多个主题 | LLM 返回 JSON 数组 |

---











# MemoryOS 评测数据集与结果详解

## 一、评测数据集介绍

### 1.1 GVD (Generative Video Description) 数据集

**GVD** 是一个用于评测对话记忆系统的数据集，主要特点：

| 特性 | 说明 |
|------|------|
| **来源** | 基于视频描述的多轮对话 |
| **目标** | 评估系统对过去对话内容的**记忆准确性** |
| **评测维度** | Accuracy（准确率）、Correctness（正确性）、Coherence（连贯性） |

### 1.2 LoCoMo (Long-term Conversation Memory) 数据集

**LoCoMo** 是专门设计用于测试**长期对话记忆**能力的数据集，其特点更加丰富：

```
LoCoMo 数据集结构：
│
├── 多轮长对话（跨越数周/数月的对话会话）
│   ├── session_1（第一次对话）
│   ├── session_2（第二次对话）
│   ├── ...
│   └── session_N（最后一次对话）
│
├── 多模态内容
│   ├── 纯文本对话
│   └── 带图片描述的对话（blip_caption）
│
└── 分类问答对（QA pairs）
    ├── Category 1: Single Hop（单跳问题）
    ├── Category 2: Temporal（时间相关问题）
    ├── Category 3: Multi Hop（多跳问题）
    └── Category 4: Open Domain（开放域问题）
```

---

## 二、locomo10.json 数据结构详解

### 2.1 整体数据结构

```json
[
  {
    "sample_id": "conv-26",           // 样本唯一标识
    "conversation": {                  // 完整对话历史
      "speaker_a": "Caroline",         // 用户角色名
      "speaker_b": "Melanie",          // 助手角色名
      "session_1": [...],              // 第一轮对话
      "session_1_date_time": "7 May 2023",  // 第一轮对话时间
      "session_2": [...],              // 第二轮对话
      // ...更多session
    },
    "qa": [...]                        // 问答对列表（用于评测）
  },
  // ...更多样本
]
```

### 2.2 QA 对象详解（您提供的示例）

```json
{
  "question": "When did Caroline go to the LGBTQ support group?",  // 问题
  "answer": "7 May 2023",          // 标准答案（Ground Truth）
  "evidence": ["D1:3"],            // 证据来源
  "category": 2                    // 问题类别
}
```

#### 字段含义：

| 字段 | 类型 | 说明 |
|------|------|------|
| `question` | string | 需要系统回答的问题 |
| `answer` | string/number | 标准答案（Ground Truth） |
| `evidence` | array | **证据来源**，格式为 `"D{session}:{turn}"`，表示答案可从第几轮对话的第几个回合找到 |
| `category` | number | **问题类别**（1-4），决定评测时使用的指标 |

#### Evidence（证据）解读：

```
"evidence": ["D1:3"]
    │        │
    │        └── :3 表示该 session 中的第 3 个对话回合
    └── D1 表示 session_1（第一轮对话）

"evidence": ["D1:9", "D1:11"]
    └── 表示需要结合多个对话回合才能得出答案（多跳推理）
```

### 2.3 Category（问题类别）详解

| Category | 名称 | 说明 | 难度 |
|----------|------|------|------|
| **1** | **Single Hop** | 直接事实查询，答案存在于单个对话回合中 | ⭐ 简单 |
| **2** | **Temporal** | 时间相关问题，需要理解时间表达或计算时间差 | ⭐⭐ 中等 |
| **3** | **Multi Hop** | 多跳推理，需要综合多个对话回合的信息 | ⭐⭐⭐ 困难 |
| **4** | **Open Domain** | 开放域问题，可能需要推理或外部知识 | ⭐⭐⭐⭐ 更难 |

---

## 三、评测指标详解

### 3.1 GVD 数据集指标（表1）

| 指标 | 全称 | 计算方式 | 意义 |
|------|------|----------|------|
| **Acc. ↑** | Accuracy | 完全匹配正确率 | 系统回答与标准答案完全一致的比例 |
| **Corr. ↑** | Correctness | 语义正确性评分 | 评估回答的内容是否语义正确（可能由 LLM 评判） |
| **Cohe. ↑** | Coherence | 连贯性评分 | 评估回答是否逻辑连贯、表达自然 |

**↑ 表示越高越好**

### 3.2 LoCoMo 数据集指标（表2）

| 指标 | 全称 | 计算公式 | 意义 |
|------|------|----------|------|
| **F1 ↑** | F1 Score | `2 × (P × R) / (P + R)` | **精确率和召回率的调和平均**，综合评估答案质量 |
| **BLEU-1 ↑** | Bilingual Evaluation Understudy | 1-gram 匹配度 | 评估生成文本与参考文本在词级别的相似度 |
| **Avg. Rank ↓** | Average Rank | 各指标排名的平均值 | **↓ 表示越低越好** |

#### F1 Score 计算原理（对应 [evalution_loco.py](cci:7://file:///d:/PyCode/Memory/MemoryOS/eval/evalution_loco.py:0:0-0:0)）：

```python
def calculate_f1(prediction: str, reference: str) -> float:
    """
    计算 F1 分数
    
    F1 = 2 × (精确率 × 召回率) / (精确率 + 召回率)
    
    其中：
    - 精确率 (Precision) = |预测词 ∩ 参考词| / |预测词|
    - 召回率 (Recall) = |预测词 ∩ 参考词| / |参考词|
    """
    pred_tokens = set(simple_tokenize(prediction))   # 预测答案的词集合
    ref_tokens = set(simple_tokenize(reference))     # 标准答案的词集合
    
    common_tokens = pred_tokens & ref_tokens         # 交集（共同的词）
    
    precision = len(common_tokens) / len(pred_tokens)  # 精确率
    recall = len(common_tokens) / len(ref_tokens)      # 召回率
    
    if precision + recall > 0:
        f1 = 2 * (precision * recall) / (precision + recall)
    else:
        f1 = 0
    return f1
```

**示例**：
```
参考答案: "Psychology, counseling certification"
系统回答: "counseling and psychology"

参考词: {"psychology", "counseling", "certification"}
预测词: {"counseling", "and", "psychology"}
交集: {"counseling", "psychology"}

Precision = 2/3 = 0.67
Recall = 2/3 = 0.67
F1 = 2 × (0.67 × 0.67) / (0.67 + 0.67) = 0.67
```

---

## 四、表格详细分析

### 4.1 GVD 数据集结果分析（表1）

```
┌─────────────────────────────────────────────────────────────┐
│      GPT-4o-mini 作为基座模型的结果                          │
├─────────────┬────────┬────────┬────────┬───────────────────┤
│ Method      │ Acc. ↑ │ Corr. ↑│ Cohe. ↑│ 综合评价           │
├─────────────┼────────┼────────┼────────┼───────────────────┤
│ TiM         │ 84.5   │ 78.8   │ 90.8   │ 基线方法           │
│ MemoryBank  │ 78.4   │ 73.3   │ 91.2   │ 准确率下降         │
│ MemGPT      │ 87.9   │ 83.2   │ 89.6   │ 进步较大           │
│ A-Mem       │ 90.4   │ 86.5   │ 91.4   │ 次优               │
│ Ours        │ 93.3   │ 91.2   │ 92.3   │ ⭐ 最优！          │
├─────────────┼────────┼────────┼────────┼───────────────────┤
│ Improvement │ +3.2%  │ +5.4%  │ +1.0%  │ 相较第二名         │
└─────────────┴────────┴────────┴────────┴───────────────────┘
```

**关键发现**：

1. **MemoryOS（Ours）在所有指标上均取得最佳结果**
   - Accuracy: 93.3%（相比 A-Mem 提升 3.2%）
   - Correctness: 91.2%（相比 A-Mem 提升 5.4%，**最大提升**）
   - Coherence: 92.3%（相比 A-Mem 提升 1.0%）

2. **Correctness 提升最显著（5.4%）**：说明 MemoryOS 的三层记忆架构显著提高了回答的**语义正确性**，这得益于：
   - 短期记忆的 FIFO 策略保持上下文连贯
   - 中期记忆的热度机制保留重要信息
   - 长期记忆的画像系统提供背景知识

3. **MemoryBank 表现较差**：可能因为其记忆管理策略不如 MemoryOS 的分层架构高效

### 4.2 LoCoMo 数据集结果分析（表2）

这张表更复杂，展示了**四类问题的分别表现**：

```
┌──────────────────────────────────────────────────────────────────────────────────────┐
│                          GPT-4o-mini 结果分析                                          │
├───────────┬──────────────┬──────────────┬──────────────┬──────────────┬──────────────┤
│           │ Single Hop   │ Multi Hop    │ Temporal     │ Open Domain  │              │
│ Method    │ F1 / BLEU-1  │ F1 / BLEU-1  │ F1 / BLEU-1  │ F1 / BLEU-1  │ Avg.Rank     │
├───────────┼──────────────┼──────────────┼──────────────┼──────────────┼──────────────┤
│ TiM       │ 16.25/13.12  │ 18.43/17.35  │ 8.35/7.32    │ 23.74/22.05  │ 3.8 / 4.0    │
│ MemBank   │ 5.00/4.77    │ 9.68/6.99    │ 5.56/5.94    │ 6.61/5.16    │ 5.0 / 5.0    │
│ MemGPT    │ 26.65/17.72  │ 25.52/19.44  │ 9.15/7.44    │ 41.04/34.34  │ 2.2 / 2.5    │
│ A-Mem     │ 27.02/20.09  │ 45.85/36.67  │ 12.14/12.00  │ 44.65/37.06  │ - / -        │
│ A-Mem*    │ 22.61/15.25  │ 33.23/29.11  │ 8.04/7.81    │ 34.13/27.73  │ 3.0 / 2.5    │
│ Ours      │ 35.27/25.22  │ 41.15/30.76  │ 20.02/16.52  │ 48.62/42.99  │ 1.0 / 1.0 ⭐│
├───────────┼──────────────┼──────────────┼──────────────┼──────────────┼──────────────┤
│ Improve.  │ +32.35%/42%  │ +23.83%/5.7% │ +118.8%/111% │ +18.47%/25%  │ 排名第一    │
└───────────┴──────────────┴──────────────┴──────────────┴──────────────┴──────────────┘
```

**关键发现**：

1. **MemoryOS 在所有四类问题上均排名第一**（Avg. Rank = 1.0）

2. **Temporal（时间问题）提升最显著：+118.8% / +111%**
   - 原因：MemoryOS 的时间衰减机制 `R = exp(-Δt/τ)` 能够更好地处理时间相关信息
   - 中期记忆中的 [timestamp](cci:1://file:///d:/PyCode/Memory/MemoryOS/memoryos-pypi/utils.py:118:0-119:63) 和 `last_visit_time` 字段有效支持时间推理

3. **各类别表现分析**：

   | 类别 | MemoryOS 表现 | 核心优势 |
   |------|---------------|----------|
   | Single Hop | F1=35.27 | 长期记忆的知识库快速检索 |
   | Multi Hop | F1=41.15 | 中期记忆的会话链接（Dialogue Chain）支持多跳推理 |
   | Temporal | F1=20.02 | **热度计算中的时间衰减因子** |
   | Open Domain | F1=48.62 | 用户画像（90维人格分析）提供丰富背景 |

4. **Qwen2.5-3B 结果（表格下半部分）**：
   - 使用较小的开源模型也能取得优异表现
   - 证明 MemoryOS 的**架构优势**不依赖于特定大模型

### 4.3 Avg. Rank 指标解读

```
Avg. Rank 计算方式：
1. 在每个子类别（Single Hop, Multi Hop, Temporal, Open Domain）中按 F1/BLEU 对方法排名
2. 计算各方法在所有类别中排名的平均值
3. Avg. Rank = 1.0 表示在所有类别中都排名第一

示例：
- MemoryOS: 在 4 个类别中全部排名第 1 → Avg. Rank = (1+1+1+1)/4 = 1.0
- MemGPT: 在各类别排名为 {2, 2, 3, 2} → Avg. Rank = (2+2+3+2)/4 = 2.25 ≈ 2.2
```

---

## 五、评测流程示意图

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                        LoCoMo 评测完整流程                                   │
└─────────────────────────────────────────────────────────────────────────────┘

1. 加载数据集 (locomo10.json)
        │
        ▼
┌───────────────────────────────────────────────────────────────────────┐
│ 对每个样本 (sample)：                                                  │
│ ┌─────────────────────────────────────────────────────────────────┐  │
│ │ 2. 初始化三层记忆系统                                            │  │
│ │    short_mem = ShortTermMemory(capacity=7)                      │  │
│ │    mid_mem = MidTermMemory(capacity=200)                        │  │
│ │    long_mem = LongTermMemory()                                  │  │
│ └─────────────────────────────────────────────────────────────────┘  │
│                      │                                                │
│                      ▼                                                │
│ ┌─────────────────────────────────────────────────────────────────┐  │
│ │ 3. 处理对话历史，构建记忆                                         │  │
│ │    for dialog in processed_dialogs:                             │  │
│ │        short_mem.add_qa_pair(dialog)                            │  │
│ │        if short_mem.is_full():                                  │  │
│ │            dynamic_updater.bulk_evict_and_update_mid_term()     │  │
│ │        update_user_profile_from_top_segment(...)  # MTM→LPM     │  │
│ └─────────────────────────────────────────────────────────────────┘  │
│                      │                                                │
│                      ▼                                                │
│ ┌─────────────────────────────────────────────────────────────────┐  │
│ │ 4. 对每个 QA 对进行评测                                           │  │
│ │    for qa in qa_pairs:                                          │  │
│ │        # 检索相关记忆                                            │  │
│ │        retrieval_result = retrieval_system.retrieve(question)   │  │
│ │                                                                  │  │
│ │        # 生成系统回答                                            │  │
│ │        system_answer = generate_system_response_with_meta(...)  │  │
│ │                                                                  │  │
│ │        # 保存结果                                                │  │
│ │        results.append({                                          │  │
│ │            "question": question,                                 │  │
│ │            "system_answer": system_answer,                       │  │
│ │            "original_answer": answer,  # Ground Truth           │  │
│ │            "category": category                                  │  │
│ │        })                                                        │  │
│ └─────────────────────────────────────────────────────────────────┘  │
└───────────────────────────────────────────────────────────────────────┘
                       │
                       ▼
┌───────────────────────────────────────────────────────────────────────┐
│ 5. 计算评测指标 (evalution_loco.py)                                    │
│    for sample in results:                                              │
│        f1 = calculate_f1(system_answer, original_answer)              │
│        category_f1[category].append(f1)                                │
│                                                                        │
│    # 输出各类别的平均 F1                                               │
│    for category, f1_scores in category_f1.items():                    │
│        print(f"Category {category}: Avg F1 = {mean(f1_scores)}")      │
└───────────────────────────────────────────────────────────────────────┘
```

---

## 六、总结

### MemoryOS 为什么在这些评测中表现优异？

1. **三层记忆架构的优势**：
   - STM (FIFO): 保持对话上下文的连贯性
   - MTM (热度管理): 智能筛选重要记忆，避免信息过载
   - LPM (持久化画像): 积累长期用户知识，支持个性化回答

2. **热度机制 `H = αN + βL + γR`**：
   - 频繁被访问的记忆更容易被检索
   - 时间衰减确保近期记忆优先
   - 超过阈值触发画像更新，形成长期知识

3. **多层次语义检索**：
   - 使用 FAISS 进行高效向量检索
   - 二阶段检索：先找 Segment，再找 Page
   - 支持关键词 + 语义相似度的混合匹配

4. **并行处理优化**：
   - 三层记忆并行检索
   - 用户画像分析与知识提取并行执行
