import io
import re
import threading
from functools import lru_cache
from uuid import uuid4

from PIL import Image, ImageOps


DISPLAY_MATH_PATTERN = re.compile(r"(?<!\\)\$\$(.+?)(?<!\\)\$\$", re.DOTALL)
CODE_PATTERN = re.compile(r"```.*?(?:```|\Z)|`[^`\n]*`", re.DOTALL)
ALIGN_ENV_PATTERN = re.compile(r"\\(?:begin|end)\{(?:aligned|align\*?|gathered|split)\}")
ALIGN_LINE_BREAK_PATTERN = re.compile(r"\\\\(?:\s*\[[^\]]*\])?")
MATH_TOKEN_PATTERN = r"(?:[A-Za-z0-9]|\\[A-Za-z]+)"
FRAC_SHORTHAND_PATTERN = re.compile(
    rf"\\frac\s*({MATH_TOKEN_PATTERN})\s*({MATH_TOKEN_PATTERN})"
)
SQRT_SHORTHAND_PATTERN = re.compile(
    rf"\\sqrt(\[[^\]]+\])?\s*({MATH_TOKEN_PATTERN})"
)
BOXED_SHORTHAND_PATTERN = re.compile(rf"\\boxed\s*({MATH_TOKEN_PATTERN})")
BOXED_SIMPLE_PATTERN = re.compile(r"\\boxed\s*\{([^{}]+)\}")
MAX_MATH_EXPRESSION_LENGTH = 1000
MAX_MATH_IMAGE_WIDTH = 2400
MAX_MATH_IMAGE_HEIGHT = 1200
MATH_BACKGROUND_COLOR = (43, 45, 49, 255)
MATH_FOREGROUND_COLOR = (242, 243, 245, 255)
_MATH_RENDER_LOCK = threading.Lock()


def _normalize_math_line(expression: str) -> str:
    normalized = str(expression or "").strip().replace("&", "")
    normalized = re.sub(r"\\(?:dfrac|tfrac|cfrac)", r"\\frac", normalized)
    normalized = re.sub(r"\\operatorname\s*\{([^{}]+)\}", r"\\mathrm{\1}", normalized)

    previous = None
    while normalized != previous:
        previous = normalized
        normalized = FRAC_SHORTHAND_PATTERN.sub(r"\\frac{\1}{\2}", normalized)
        normalized = SQRT_SHORTHAND_PATTERN.sub(
            lambda match: rf"\sqrt{match.group(1) or ''}{{{match.group(2)}}}",
            normalized,
        )
        normalized = BOXED_SHORTHAND_PATTERN.sub(r"\\boxed{\1}", normalized)

    normalized = BOXED_SIMPLE_PATTERN.sub(
        r"\\left[\\mathbf{\1}\\right]",
        normalized,
    )
    if normalized.startswith(r"\displaystyle"):
        normalized = normalized[len(r"\displaystyle"):].lstrip()
    return normalized.strip()


def normalize_math_lines(expression: str) -> list[str]:
    raw = str(expression or "").strip()
    has_alignment_environment = bool(ALIGN_ENV_PATTERN.search(raw))
    normalized = ALIGN_ENV_PATTERN.sub("", raw)
    has_explicit_line_break = bool(ALIGN_LINE_BREAK_PATTERN.search(normalized))

    if has_alignment_environment or has_explicit_line_break:
        chunks = ALIGN_LINE_BREAK_PATTERN.split(normalized)
        raw_lines = []
        for chunk in chunks:
            raw_lines.extend(chunk.splitlines())
    else:
        raw_lines = [" ".join(normalized.splitlines())]

    lines = []
    for raw_line in raw_lines:
        line = re.sub(r"\\\s*$", "", raw_line.strip())
        line = _normalize_math_line(line)
        if line:
            lines.append(line)
    return lines


def _render_math_mask(expression: str, mathtext, font_properties) -> Image.Image:
    rendered = io.BytesIO()
    mathtext.math_to_image(
        f"${expression}$",
        rendered,
        prop=font_properties,
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
    return glyph_mask.crop(mask_bbox)


@lru_cache(maxsize=128)
def render_math_png(expression: str) -> bytes:
    from matplotlib import mathtext
    from matplotlib.font_manager import FontProperties

    lines = normalize_math_lines(expression)
    if not lines:
        raise ValueError("empty math expression")

    with _MATH_RENDER_LOCK:
        font_properties = FontProperties(size=24)
        line_masks = [
            _render_math_mask(line, mathtext, font_properties)
            for line in lines
        ]

    line_gap = 18
    foreground_width = max(mask.width for mask in line_masks)
    foreground_height = sum(mask.height for mask in line_masks) + line_gap * (len(line_masks) - 1)
    foreground = Image.new("RGBA", (foreground_width, foreground_height), (0, 0, 0, 0))
    current_y = 0
    for mask in line_masks:
        line_image = Image.new("RGBA", mask.size, MATH_FOREGROUND_COLOR)
        line_image.putalpha(mask)
        foreground.alpha_composite(line_image, dest=(0, current_y))
        current_y += mask.height + line_gap

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
