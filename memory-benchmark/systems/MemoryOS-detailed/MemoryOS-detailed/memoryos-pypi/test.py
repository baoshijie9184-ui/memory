
import os
from memoryos import Memoryos

# --- 基本配置 ---
USER_ID = "demo_user"
ASSISTANT_ID = "demo_assistant"
API_KEY = "sk-bakuopflqametmrahmluljzoydoqttetzdftpwqlmtvrkaln"  # 替换为您的API密钥
BASE_URL = "https://api.siliconflow.cn/v1"  # 使用 SiliconFlow 或其他兼容 OpenAI 的 API
DATA_STORAGE_PATH = "./simple_demo_data"
LLM_MODEL = "deepseek-ai/DeepSeek-V3.2"
EMBEDDING_MODEL = "Qwen/Qwen3-Embedding-8B"  # API embedding 模型

def simple_demo():
    print("MemoryOS 简单演示")
    
    # 1. 初始化 MemoryOS
    print("正在初始化 MemoryOS...")
    try:
        memo = Memoryos(
            user_id=USER_ID,
            openai_api_key=API_KEY,
            openai_base_url=BASE_URL,
            data_storage_path=DATA_STORAGE_PATH,
            llm_model=LLM_MODEL,
            assistant_id=ASSISTANT_ID,
            short_term_capacity=7,  
            mid_term_heat_threshold=5,  
            retrieval_queue_capacity=10,
            long_term_knowledge_capacity=100,
            mid_term_similarity_threshold=0.6,
            embedding_model_name=EMBEDDING_MODEL,  # 使用 API embedding 模型
            use_embedding_api=True  # 启用 API embedding 调用
        )
        print("MemoryOS 初始化成功！\n")
    except Exception as e:
        print(f"初始化失败: {e}")
        return

    # 2. 添加一些基础记忆
    print("正在添加记忆...")
    
    memo.add_memory(
        user_input="你好！我叫小明，是一名在上海工作的数据科学家。",
        agent_response="你好小明！很高兴认识你。数据科学是一个很有前景的领域，你平时主要做哪方面的数据分析呢？"
    )
    memo.add_memory(
        user_input="我周末喜欢去爬山，特别是黄山和泰山。",
        agent_response="登山真是个好爱好！黄山和泰山都是名山，你更喜欢哪座山的风景呢？"
    )
    memo.add_memory(
        user_input="最近我一直在研究人工智能相关的技术。",
        agent_response="人工智能是个很热门的领域！你对AI的哪个方向最感兴趣呢？"
    )
    memo.add_memory(
        user_input="我最喜欢吃的是火锅，尤其是四川麻辣火锅。",
        agent_response="火锅确实很美味！四川火锅的麻辣味道很过瘾，你喜欢吃什么配菜呢？"
    )
    memo.add_memory(
        user_input="我养了一只叫豆豆的金毛犬。",
        agent_response="豆豆一定很可爱！金毛犬性格温顺，它今年几岁了？"
    )
    memo.add_memory(
        user_input="去年我去了日本旅行，参观了东京和京都。",
        agent_response="日本之旅一定很精彩！东京繁华，京都古朴，你最喜欢的景点是哪里？"
    )
    memo.add_memory(
        user_input="我正在学习弹吉他。",
        agent_response="学吉他真棒！你现在在练习哪些曲子呢？"
    )
    memo.add_memory(
        user_input="我每天早上都要喝一杯黑咖啡。",
        agent_response="咖啡是开启一天的好方式！你喜欢喝热咖啡还是冰咖啡？"
    )
    memo.add_memory(
        user_input="我最喜欢的电影类型是科幻片。",
        agent_response="科幻电影很有想象力！你有最喜欢的科幻电影吗？"
    )
    memo.add_memory(
        user_input="我业余时间喜欢画山水画。",
        agent_response="画画是很有艺术气息的爱好！你用水墨还是水彩作画呢？"
    )

    # 3. 测试记忆检索
    test_query = "你还记得我的工作是什么吗？"
    print(f"\n用户: {test_query}")
    
    response = memo.get_response(
        query=test_query,
    )
    
    print(f"助手: {response}")

if __name__ == "__main__":
    simple_demo()