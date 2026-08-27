import base64
import math

from openai import OpenAI

from globalenv import get_global_config, set_global_config


AI_ENDPOINT_CONFIG_KEY = "ai_endpoint"
AI_API_KEY_CONFIG_KEY = "ai_api_key"
AI_MODELS_CONFIG_KEY = "ai_models"
AI_VIDEO_MODELS_CONFIG_KEY = "ai_video_models"
AI_IMAGE_MODELS_CONFIG_KEY = "ai_image_models"
AI_DEFAULT_MODEL_CONFIG_KEY = "ai_default_model"
AI_IMAGE_MODEL_CONFIG_KEY = "ai_image_model"
AI_REVIEW_MODEL_CONFIG_KEY = "ai_review_model"
AI_REPORT_MODEL_CONFIG_KEY = "ai_report_model"
AI_TOOL_CALL_MODES_CONFIG_KEY = "ai_tool_call_modes"
AI_VISION_MODELS_CONFIG_KEY = "ai_vision_models"
AI_VISION_MODEL_CONFIG_KEY = "ai_vision_model"

VALID_AI_TOOL_CALL_MODES = {"auto", "native", "emulated"}

DEFAULT_AI_ENDPOINT = "https://api.poe.com/v1"
DEFAULT_AI_MODELS = {
    "openai-fast": 0.05,
    "openai": 0.10,
    "gpt-5-mini": 0.10,
    "openai-large": 0.45,
    "perplexity-fast": 0.10,
    "claude-fast": 0.15,
    "kimi-k2.6": 0.05,
    "gemma-4-31b": 0.10,
    "glm-5.1-t": 0.10,
    "qwen3.5-397b-a17b-t": 0.15,
}
DEFAULT_AI_VIDEO_MODELS = {
    "seedance-2.0-fast-el": 500.00,
    "seedance-2.0-pro-el": 1000.00,
}
DEFAULT_AI_IMAGE_MODELS = {
    "gpt-image-2": 250.00,
}
DEFAULT_AI_DEFAULT_MODEL = "kimi-k2.6"
DEFAULT_AI_IMAGE_MODEL = "gpt-image-2"
DEFAULT_AI_REVIEW_MODEL = "openai"
DEFAULT_AI_REPORT_MODEL = "openai-fast"
DEFAULT_AI_VISION_MODELS = []
DEFAULT_AI_VISION_MODEL = ""
AI_GLOBAL_CONFIG_DEFAULTS = {
    AI_ENDPOINT_CONFIG_KEY: DEFAULT_AI_ENDPOINT,
    AI_API_KEY_CONFIG_KEY: "",
    AI_MODELS_CONFIG_KEY: DEFAULT_AI_MODELS,
    AI_VIDEO_MODELS_CONFIG_KEY: DEFAULT_AI_VIDEO_MODELS,
    AI_IMAGE_MODELS_CONFIG_KEY: DEFAULT_AI_IMAGE_MODELS,
    AI_DEFAULT_MODEL_CONFIG_KEY: DEFAULT_AI_DEFAULT_MODEL,
    AI_IMAGE_MODEL_CONFIG_KEY: DEFAULT_AI_IMAGE_MODEL,
    AI_REVIEW_MODEL_CONFIG_KEY: DEFAULT_AI_REVIEW_MODEL,
    AI_REPORT_MODEL_CONFIG_KEY: DEFAULT_AI_REPORT_MODEL,
    AI_TOOL_CALL_MODES_CONFIG_KEY: {},
    AI_VISION_MODELS_CONFIG_KEY: DEFAULT_AI_VISION_MODELS,
    AI_VISION_MODEL_CONFIG_KEY: DEFAULT_AI_VISION_MODEL,
}
_GLOBAL_CONFIG_MISSING = object()

# 原生 tool calling 在執行期偵測到不支援的模型（僅限本次進程，換 endpoint 或重啟後重新探測）
_AI_NATIVE_TOOLS_RUNTIME_UNSUPPORTED: set = set()


def ensure_ai_global_config_defaults():
    for key, value in AI_GLOBAL_CONFIG_DEFAULTS.items():
        if get_global_config(key, _GLOBAL_CONFIG_MISSING) is _GLOBAL_CONFIG_MISSING:
            set_global_config(key, value)


def coerce_ai_rate_dict(value, default: dict[str, float]) -> dict[str, float]:
    source = value if isinstance(value, dict) else default
    rates: dict[str, float] = {}
    for model, rate in source.items():
        model_name = str(model).strip()
        if not model_name:
            continue
        try:
            rate_value = float(rate)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(rate_value):
            continue
        rates[model_name] = rate_value
    return rates or dict(default)


def coerce_ai_model_list(value, valid_models=None) -> list[str]:
    if not isinstance(value, (list, tuple, set)):
        return []
    valid = set(valid_models) if valid_models is not None else None
    models: list[str] = []
    seen: set[str] = set()
    for model in value:
        model_name = str(model or "").strip()
        if not model_name or model_name in seen:
            continue
        if valid is not None and model_name not in valid:
            continue
        seen.add(model_name)
        models.append(model_name)
    return models


def _reconcile_ai_vision_config(model_rates: dict[str, float] | None = None):
    valid_models = set(
        (model_rates if model_rates is not None else get_ai_model_rates()).keys()
    )
    vision_models = coerce_ai_model_list(
        get_global_config(AI_VISION_MODELS_CONFIG_KEY, DEFAULT_AI_VISION_MODELS),
        valid_models,
    )
    configured_delegate = str(
        get_global_config(AI_VISION_MODEL_CONFIG_KEY, DEFAULT_AI_VISION_MODEL) or ""
    ).strip()
    if configured_delegate not in vision_models:
        configured_delegate = ""
    set_global_config(AI_VISION_MODELS_CONFIG_KEY, vision_models)
    set_global_config(AI_VISION_MODEL_CONFIG_KEY, configured_delegate)


def get_ai_endpoint() -> str:
    ensure_ai_global_config_defaults()
    endpoint = str(get_global_config(AI_ENDPOINT_CONFIG_KEY, DEFAULT_AI_ENDPOINT) or "").strip()
    return (endpoint or DEFAULT_AI_ENDPOINT).rstrip("/")


def set_ai_endpoint(endpoint: str):
    set_global_config(AI_ENDPOINT_CONFIG_KEY, str(endpoint or "").strip().rstrip("/"))
    # 換 endpoint 後原生 tool calling 支援度可能不同，重新探測
    clear_ai_native_tools_runtime_cache()


def get_ai_tool_call_modes() -> dict:
    ensure_ai_global_config_defaults()
    raw_modes = get_global_config(AI_TOOL_CALL_MODES_CONFIG_KEY, {})
    modes: dict = {}
    if isinstance(raw_modes, dict):
        for model, mode in raw_modes.items():
            model_name = str(model or "").strip()
            mode_value = str(mode or "").strip().lower()
            if model_name and mode_value in ("native", "emulated"):
                modes[model_name] = mode_value
    return modes


def set_ai_tool_call_mode(model: str, mode: str):
    model_name = str(model or "").strip()
    mode_value = str(mode or "").strip().lower()
    if not model_name:
        raise ValueError("model cannot be empty")
    if mode_value not in VALID_AI_TOOL_CALL_MODES:
        raise ValueError(f"mode must be one of {sorted(VALID_AI_TOOL_CALL_MODES)}")
    modes = get_ai_tool_call_modes()
    if mode_value == "auto":
        modes.pop(model_name, None)
    else:
        modes[model_name] = mode_value
    set_global_config(AI_TOOL_CALL_MODES_CONFIG_KEY, modes)


def mark_ai_native_tools_unsupported(model: str):
    model_name = str(model or "").strip()
    if model_name:
        _AI_NATIVE_TOOLS_RUNTIME_UNSUPPORTED.add(model_name)


def get_ai_native_tools_runtime_unsupported() -> set:
    return set(_AI_NATIVE_TOOLS_RUNTIME_UNSUPPORTED)


def clear_ai_native_tools_runtime_cache():
    _AI_NATIVE_TOOLS_RUNTIME_UNSUPPORTED.clear()


def resolve_ai_tool_call_mode(model: str) -> str:
    model_name = str(model or "").strip()
    configured_mode = get_ai_tool_call_modes().get(model_name)
    if configured_mode:
        return configured_mode
    if model_name in _AI_NATIVE_TOOLS_RUNTIME_UNSUPPORTED:
        return "emulated"
    return "native"


def get_ai_api_key() -> str:
    ensure_ai_global_config_defaults()
    return str(get_global_config(AI_API_KEY_CONFIG_KEY, "") or "").strip()


def set_ai_api_key(api_key: str):
    set_global_config(AI_API_KEY_CONFIG_KEY, str(api_key or "").strip())


def get_ai_model_rates() -> dict[str, float]:
    ensure_ai_global_config_defaults()
    return coerce_ai_rate_dict(
        get_global_config(AI_MODELS_CONFIG_KEY, DEFAULT_AI_MODELS),
        DEFAULT_AI_MODELS,
    )


def set_ai_model_rates(models: dict[str, float]):
    model_rates = coerce_ai_rate_dict(models, {})
    set_global_config(AI_MODELS_CONFIG_KEY, model_rates)
    _reconcile_ai_vision_config(model_rates)


def get_ai_vision_models() -> list[str]:
    ensure_ai_global_config_defaults()
    return coerce_ai_model_list(
        get_global_config(AI_VISION_MODELS_CONFIG_KEY, DEFAULT_AI_VISION_MODELS),
        get_ai_model_rates(),
    )


def set_ai_vision_models(models):
    vision_models = coerce_ai_model_list(models, get_ai_model_rates())
    set_global_config(AI_VISION_MODELS_CONFIG_KEY, vision_models)
    configured_delegate = str(
        get_global_config(AI_VISION_MODEL_CONFIG_KEY, DEFAULT_AI_VISION_MODEL) or ""
    ).strip()
    if configured_delegate not in vision_models:
        set_global_config(AI_VISION_MODEL_CONFIG_KEY, "")


def get_ai_vision_model() -> str:
    ensure_ai_global_config_defaults()
    configured_model = str(
        get_global_config(AI_VISION_MODEL_CONFIG_KEY, DEFAULT_AI_VISION_MODEL) or ""
    ).strip()
    return configured_model if configured_model in get_ai_vision_models() else ""


def set_ai_vision_model(model: str):
    model_name = str(model or "").strip()
    if not model_name:
        set_global_config(AI_VISION_MODEL_CONFIG_KEY, "")
        return
    if model_name not in get_ai_model_rates():
        raise ValueError(f"Model not found in ai_models: {model_name}")
    vision_models = get_ai_vision_models()
    if model_name not in vision_models:
        vision_models.append(model_name)
        set_global_config(AI_VISION_MODELS_CONFIG_KEY, vision_models)
    set_global_config(AI_VISION_MODEL_CONFIG_KEY, model_name)


def is_ai_vision_model(model: str) -> bool:
    return str(model or "").strip() in get_ai_vision_models()


def resolve_ai_vision_model(current_model: str = "") -> str:
    current_model_name = str(current_model or "").strip()
    if is_ai_vision_model(current_model_name):
        return current_model_name
    return get_ai_vision_model()


def get_ai_default_model() -> str:
    ensure_ai_global_config_defaults()
    configured_model = str(get_global_config(AI_DEFAULT_MODEL_CONFIG_KEY, DEFAULT_AI_DEFAULT_MODEL) or "").strip()
    text_models = get_ai_model_rates()
    if configured_model in text_models:
        return configured_model
    if DEFAULT_AI_DEFAULT_MODEL in text_models:
        return DEFAULT_AI_DEFAULT_MODEL
    return "openai" if "openai" in text_models else next(iter(text_models), "openai")


def set_ai_default_model(model: str):
    set_global_config(AI_DEFAULT_MODEL_CONFIG_KEY, str(model or "").strip())


def get_ai_video_model_rates() -> dict[str, float]:
    ensure_ai_global_config_defaults()
    return coerce_ai_rate_dict(
        get_global_config(AI_VIDEO_MODELS_CONFIG_KEY, DEFAULT_AI_VIDEO_MODELS),
        DEFAULT_AI_VIDEO_MODELS,
    )


def get_ai_image_model_rates() -> dict[str, float]:
    ensure_ai_global_config_defaults()
    return coerce_ai_rate_dict(
        get_global_config(AI_IMAGE_MODELS_CONFIG_KEY, DEFAULT_AI_IMAGE_MODELS),
        DEFAULT_AI_IMAGE_MODELS,
    )


def set_ai_image_model_rates(models: dict[str, float]):
    set_global_config(AI_IMAGE_MODELS_CONFIG_KEY, coerce_ai_rate_dict(models, {}))


def get_ai_image_model() -> str:
    ensure_ai_global_config_defaults()
    configured_model = str(get_global_config(AI_IMAGE_MODEL_CONFIG_KEY, DEFAULT_AI_IMAGE_MODEL) or "").strip()
    image_models = get_ai_image_model_rates()
    if configured_model in image_models:
        return configured_model
    return DEFAULT_AI_IMAGE_MODEL if DEFAULT_AI_IMAGE_MODEL in image_models else next(iter(image_models), DEFAULT_AI_IMAGE_MODEL)


def set_ai_image_model(model: str):
    set_global_config(AI_IMAGE_MODEL_CONFIG_KEY, str(model or "").strip())


def get_ai_review_model() -> str:
    ensure_ai_global_config_defaults()
    return str(get_global_config(AI_REVIEW_MODEL_CONFIG_KEY, DEFAULT_AI_REVIEW_MODEL) or DEFAULT_AI_REVIEW_MODEL).strip()


def set_ai_review_model(model: str):
    set_global_config(AI_REVIEW_MODEL_CONFIG_KEY, str(model or "").strip())


def get_ai_report_model() -> str:
    ensure_ai_global_config_defaults()
    return str(get_global_config(AI_REPORT_MODEL_CONFIG_KEY, DEFAULT_AI_REPORT_MODEL) or DEFAULT_AI_REPORT_MODEL).strip()


def set_ai_report_model(model: str):
    set_global_config(AI_REPORT_MODEL_CONFIG_KEY, str(model or "").strip())


def is_ai_text_model(model: str) -> bool:
    return str(model or "") in get_ai_model_rates()


def get_ai_text_model_rate(model: str, default: float = 0.1) -> float:
    return float(get_ai_model_rates().get(model, default))


def create_ai_client() -> OpenAI:
    api_key = get_ai_api_key()
    if not api_key:
        raise RuntimeError("ai_api_key is not configured")
    return OpenAI(api_key=api_key, base_url=get_ai_endpoint())


def format_ai_models_for_display(model_rates: dict[str, float], vision_models=None) -> str:
    if not model_rates:
        return "(empty)"
    vision_model_names = set(vision_models or [])
    return "\n".join(
        f"- {model}: {rate:.2f}/C{' [vision]' if model in vision_model_names else ''}"
        for model, rate in model_rates.items()
    )


def guess_image_mime_type(image: bytes) -> str:
    if image.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if image.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if image.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if image.startswith(b"RIFF") and image[8:12] == b"WEBP":
        return "image/webp"
    return "image/png"


def content_with_image(content, image: bytes):
    parts = []
    if isinstance(content, list):
        parts.extend(content)
    else:
        text = str(content or "").strip()
        if text:
            parts.append({"type": "text", "text": text})
    mime_type = guess_image_mime_type(image)
    encoded_image = base64.b64encode(image).decode("ascii")
    parts.append({
        "type": "image_url",
        "image_url": {"url": f"data:{mime_type};base64,{encoded_image}"},
    })
    return parts


def attach_image_to_messages(messages: list, image: bytes | None) -> list:
    prepared = [dict(message) for message in (messages or [])]
    if not image:
        return prepared
    for message in reversed(prepared):
        if message.get("role") == "user":
            message["content"] = content_with_image(message.get("content"), image)
            return prepared
    prepared.append({"role": "user", "content": content_with_image("", image)})
    return prepared


def create_ai_chat_completion(*, model: str, messages: list, image: bytes | None = None, **kwargs):
    client = create_ai_client()
    return client.chat.completions.create(
        model=model,
        messages=attach_image_to_messages(messages, image),
        **kwargs,
    )


ensure_ai_global_config_defaults()
