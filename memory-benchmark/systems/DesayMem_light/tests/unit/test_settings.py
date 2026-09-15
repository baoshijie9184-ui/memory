from pathlib import Path

import pytest

from desaymem_light.bootstrap.settings import RuntimeSettings, load_app_config
from desaymem_light.domain.errors import ConfigurationError


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_example_config_loads_and_isolates_plugin_options() -> None:
    config = load_app_config(PROJECT_ROOT / "config" / "cloud-test.example.yaml")

    assert config.modules.topic_segmenter == "lightmem_v1"
    options = config.options_for("lightmem_v1")
    options["sensory_tokens"] = 1
    assert config.options_for("lightmem_v1")["sensory_tokens"] == 512
    assert "max_input_tokens" not in config.options_for("deterministic_v1")


def test_cloud_runtime_requires_dependencies() -> None:
    settings = RuntimeSettings(_env_file=None)
    with pytest.raises(ConfigurationError, match="missing runtime settings"):
        settings.validate_cloud_runtime()


def test_v1_rejects_wrong_embedding_dimensions() -> None:
    settings = RuntimeSettings(
        _env_file=None,
        qwen_base_url="http://qwen",
        qwen_model="Qwen3-32B",
        bge_base_url="http://bge",
        postgres_dsn="postgresql://example",
        embedding_dims=768,
    )
    with pytest.raises(ConfigurationError, match="must be 1024"):
        settings.validate_cloud_runtime()
