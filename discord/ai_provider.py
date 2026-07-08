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
}
_GLOBAL_CONFIG_MISSING = object()


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


def get_ai_endpoint() -> str:
    ensure_ai_global_config_defaults()
    endpoint = str(get_global_config(AI_ENDPOINT_CONFIG_KEY, DEFAULT_AI_ENDPOINT) or "").strip()
    return (endpoint or DEFAULT_AI_ENDPOINT).rstrip("/")


def set_ai_endpoint(endpoint: str):
    set_global_config(AI_ENDPOINT_CONFIG_KEY, str(endpoint or "").strip().rstrip("/"))


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
    set_global_config(AI_MODELS_CONFIG_KEY, coerce_ai_rate_dict(models, {}))


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


def format_ai_models_for_display(model_rates: dict[str, float]) -> str:
    if not model_rates:
        return "(empty)"
    return "\n".join(f"- {model}: {rate:.2f}/C" for model, rate in model_rates.items())


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
