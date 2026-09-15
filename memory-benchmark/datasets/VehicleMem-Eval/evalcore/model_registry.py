"""
模型配置注册 — 从 config/models.yaml 加载模型配置
"""

import os
import yaml
from dataclasses import dataclass


@dataclass
class ModelConfig:
    name: str
    api_base: str
    api_key: str
    model: str
    judge_model: str
    judge_api_base: str
    judge_api_key: str
    embedding_base: str
    embedding_model: str
    embedding_key: str
    embedding_dim: int


def load_model_config(name: str, config_path: str = None) -> ModelConfig:
    """加载模型配置 — 从 config/models.yaml"""
    if config_path is None:
        config_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "config", "models.yaml"
        )

    with open(config_path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    models = cfg.get("models", {})
    if name not in models:
        raise ValueError(
            f"模型 '{name}' 未找到, 可选: {list(models.keys())}"
        )

    m = models[name]
    return ModelConfig(
        name=name,
        api_base=m["api_base"],
        api_key=m.get("api_key", os.getenv("LLM_API_KEY", "")),
        model=m["model"],
        judge_model=m.get("judge_model", m["model"]),
        judge_api_base=m.get("judge_api_base", m["api_base"]),
        judge_api_key=m.get("judge_api_key", m.get("api_key", "")),
        embedding_base=m.get("embedding_base", ""),
        embedding_model=m.get("embedding_model", ""),
        embedding_key=m.get("embedding_key", ""),
        embedding_dim=m.get("embedding_dim", 2048),
    )
