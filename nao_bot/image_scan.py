import asyncio
from dataclasses import dataclass
from io import BytesIO

import httpx
from PIL import Image, ImageOps


MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_IMAGE_PIXELS = 20_000_000
MAX_IMAGE_SIDE = 1920


@dataclass(frozen=True)
class ImageScanResult:
    text: str
    has_qr_code: bool


_ocr_engine = None
_scan_lock = asyncio.Lock()


async def _download_image(url: str) -> bytes:
    timeout = httpx.Timeout(15, connect=5)
    chunks: list[bytes] = []
    size = 0
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        async with client.stream("GET", url) as response:
            response.raise_for_status()
            content_length = response.headers.get("content-length")
            if content_length and int(content_length) > MAX_IMAGE_BYTES:
                raise ValueError("图片超过 8 MB 限制")
            async for chunk in response.aiter_bytes():
                size += len(chunk)
                if size > MAX_IMAGE_BYTES:
                    raise ValueError("图片超过 8 MB 限制")
                chunks.append(chunk)
    return b"".join(chunks)


def _prepare_image(data: bytes) -> bytes:
    with Image.open(BytesIO(data)) as source:
        if source.width * source.height > MAX_IMAGE_PIXELS:
            raise ValueError("图片像素过大")
        image = ImageOps.exif_transpose(source).convert("RGB")
        image.thumbnail((MAX_IMAGE_SIDE, MAX_IMAGE_SIDE), Image.Resampling.LANCZOS)
        output = BytesIO()
        image.save(output, format="PNG", optimize=True)
    return output.getvalue()


def _get_ocr_engine():
    global _ocr_engine
    if _ocr_engine is None:
        from rapidocr_onnxruntime import RapidOCR

        _ocr_engine = RapidOCR(intra_op_num_threads=1, inter_op_num_threads=1)
    return _ocr_engine


def _scan_image_bytes(data: bytes) -> ImageScanResult:
    import cv2
    import numpy as np

    prepared = _prepare_image(data)
    image = cv2.imdecode(np.frombuffer(prepared, dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError("无法解析图片")

    _, qr_points, _ = cv2.QRCodeDetector().detectAndDecode(image)
    result, _ = _get_ocr_engine()(prepared)
    text = "\n".join(str(item[1]) for item in result or [] if len(item) >= 2)
    return ImageScanResult(text=text, has_qr_code=qr_points is not None)


async def scan_image_url(url: str) -> ImageScanResult:
    data = await _download_image(url)
    async with _scan_lock:
        return await asyncio.to_thread(_scan_image_bytes, data)
