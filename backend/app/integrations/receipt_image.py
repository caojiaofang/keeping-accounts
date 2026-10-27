"""校验图片真实内容，清除元数据后内联发送；不保存图片到磁盘。"""

import base64
import binascii
from io import BytesIO

from PIL import Image, ImageOps, UnidentifiedImageError

from app.core.errors import ServiceError

MAX_IMAGE_BYTES = 4 * 1024 * 1024
MAX_IMAGE_PIXELS = 24_000_000
IMAGE_TYPES = {"image/jpeg": "JPEG", "image/png": "PNG", "image/webp": "WEBP"}


def prepare_receipt_image(data_url: str) -> str:
    try:
        header, encoded = data_url.split(",", 1)
        mime = header.removeprefix("data:").removesuffix(";base64")
        if header != f"data:{mime};base64" or mime not in IMAGE_TYPES:
            raise ValueError("图片格式不支持")
        if len(encoded) > ((MAX_IMAGE_BYTES + 2) // 3) * 4:
            raise ValueError("图片过大")
        raw = base64.b64decode(encoded, validate=True)
        if not raw or len(raw) > MAX_IMAGE_BYTES:
            raise ValueError("图片过大或为空")
        with Image.open(BytesIO(raw)) as source:
            if source.format != IMAGE_TYPES[mime] or getattr(source, "n_frames", 1) != 1:
                raise ValueError("图片类型不匹配或为动画")
            if source.width * source.height > MAX_IMAGE_PIXELS:
                raise ValueError("图片像素过多")
            source.verify()
        with Image.open(BytesIO(raw)) as source:
            image = ImageOps.exif_transpose(source)
            image.thumbnail((4096, 4096))
            # 将透明区域铺白，避免小票文字在转成 JPEG 后变黑。
            background = Image.new("RGB", image.size, "white")
            if image.mode in {"RGBA", "LA"} or "transparency" in image.info:
                rgba = image.convert("RGBA")
                background.paste(rgba, mask=rgba.getchannel("A"))
            else:
                background.paste(image.convert("RGB"))
            output = BytesIO()
            background.save(output, format="JPEG", quality=90)
        return "data:image/jpeg;base64," + base64.b64encode(output.getvalue()).decode("ascii")
    except (
        ValueError,
        binascii.Error,
        OSError,
        UnidentifiedImageError,
        Image.DecompressionBombError,
    ):
        raise ServiceError(422, "请上传清晰的 JPG、PNG 或 WebP 静态图片，单张不超过 4 MB") from None
