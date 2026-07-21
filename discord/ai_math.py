import io
import re
import threading
from functools import lru_cache
from uuid import uuid4

from PIL import Image, ImageOps


DISPLAY_MATH_PATTERN = re.compile(r"(?<!\\)\$\$(.+?)(?<!\\)\$\$", re.DOTALL)
CODE_PATTERN = re.compile(r"```.*?(?:```|\Z)|`[^`\n]*`", re.DOTALL)
MAX_MATH_EXPRESSION_LENGTH = 1000
MAX_MATH_IMAGE_WIDTH = 2400
MAX_MATH_IMAGE_HEIGHT = 1200
MATH_BACKGROUND_COLOR = (43, 45, 49, 255)
MATH_FOREGROUND_COLOR = (242, 243, 245, 255)
_MATH_RENDER_LOCK = threading.Lock()


@lru_cache(maxsize=128)
def render_math_png(expression: str) -> bytes:
    from matplotlib import mathtext
    from matplotlib.font_manager import FontProperties

    normalized = str(expression or "").strip()
    if normalized.startswith(r"\displaystyle"):
        normalized = normalized[len(r"\displaystyle"):].lstrip()
    if not normalized:
        raise ValueError("empty math expression")

    rendered = io.BytesIO()
    with _MATH_RENDER_LOCK:
        mathtext.math_to_image(
            f"${normalized}$",
            rendered,
            prop=FontProperties(size=24),
            dpi=180,
            format="png",
            color="black",
        )

    rendered.seek(0)
    with Image.open(rendered) as source:
        grayscale = source.convert("L")

    glyph_mask = ImageOps.invert(grayscale)
    mask_bbox = glyph_mask.getbbox()
    if mask_bbox is None:
        raise ValueError("math expression rendered an empty image")
    glyph_mask = glyph_mask.crop(mask_bbox)
    foreground = Image.new("RGBA", glyph_mask.size, MATH_FOREGROUND_COLOR)
    foreground.putalpha(glyph_mask)

    padding_x = 36
    padding_y = 28
    width = foreground.width + padding_x * 2
    height = foreground.height + padding_y * 2
    scale = min(
        1.0,
        MAX_MATH_IMAGE_WIDTH / width,
        MAX_MATH_IMAGE_HEIGHT / height,
    )
    if scale < 1.0:
        foreground = foreground.resize(
            (
                max(1, round(foreground.width * scale)),
                max(1, round(foreground.height * scale)),
            ),
            Image.Resampling.LANCZOS,
        )
        width = foreground.width + padding_x * 2
        height = foreground.height + padding_y * 2

    canvas = Image.new("RGBA", (width, height), MATH_BACKGROUND_COLOR)
    canvas.alpha_composite(foreground, dest=(padding_x, padding_y))

    output = io.BytesIO()
    canvas.save(output, format="PNG", optimize=True)
    return output.getvalue()


def render_display_math(response_text: str, *, max_images: int = 6) -> tuple[str, list[dict]]:
    text = str(response_text or "")
    if max_images <= 0 or "$$" not in text:
        return text, []

    protected_blocks: list[str] = []

    def protect_code(match: re.Match) -> str:
        protected_blocks.append(match.group(0))
        return f"\x01MATHCODE{len(protected_blocks) - 1}\x01"

    protected_text = CODE_PATTERN.sub(protect_code, text)
    output_parts: list[str] = []
    attachments: list[dict] = []
    cursor = 0

    for match in DISPLAY_MATH_PATTERN.finditer(protected_text):
        output_parts.append(protected_text[cursor:match.start()])
        original = match.group(0)
        expression = str(match.group(1) or "").strip()
        replacement = original

        if len(attachments) < max_images and 0 < len(expression) <= MAX_MATH_EXPRESSION_LENGTH:
            try:
                image_bytes = render_math_png(expression)
            except Exception:
                pass
            else:
                filename = f"ai-math-{uuid4().hex[:12]}.png"
                attachments.append(
                    {
                        "filename": filename,
                        "content": image_bytes,
                        "size_bytes": len(image_bytes),
                        "kind": "math",
                        "math_source": original,
                    }
                )
                replacement = f"<generated_image>attachment://{filename}</generated_image>"

        output_parts.append(replacement)
        cursor = match.end()

    output_parts.append(protected_text[cursor:])
    rendered_text = "".join(output_parts)
    for index, code_block in enumerate(protected_blocks):
        rendered_text = rendered_text.replace(f"\x01MATHCODE{index}\x01", code_block)

    return rendered_text, attachments
