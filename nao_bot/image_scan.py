import asyncio
import logging
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from io import BytesIO
from tempfile import NamedTemporaryFile
from time import monotonic
from typing import BinaryIO

import httpx
from PIL import Image, ImageOps


MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_IMAGE_PIXELS = 20_000_000
MAX_IMAGE_SIDE = 1920
MAX_VIDEO_BYTES = 48 * 1024 * 1024
VIDEO_FRAME_INTERVAL_SECONDS = 5
VIDEO_FILE_EXTENSIONS = frozenset(
    {".3gp", ".avi", ".flv", ".m4v", ".mkv", ".mov", ".mp4", ".ts", ".webm", ".wmv"}
)


@dataclass(frozen=True)
class ImageScanResult:
    text: str
    has_qr_code: bool


def is_video_file_name(file_name: str) -> bool:
    return file_name.rsplit("/", 1)[-1].lower().endswith(tuple(VIDEO_FILE_EXTENSIONS))


def first_video_file_id(segments: Iterable[object]) -> str | None:
    for segment in segments:
        if getattr(segment, "type", "") != "file":
            continue
        data = getattr(segment, "data", {})
        if not isinstance(data, dict) or not is_video_file_name(str(data.get("file_name", ""))):
            continue
        if file_id := data.get("file_id"):
            return str(file_id)
    return None


_ocr_engine = None
_scan_lock = asyncio.Lock()
logger = logging.getLogger(__name__)


async def _download_media(
    url: str,
    max_bytes: int,
    too_large_message: str,
    destination: BinaryIO | None = None,
) -> bytes:
    timeout = httpx.Timeout(15, connect=5)
    chunks: list[bytes] = []
    size = 0
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        async with client.stream("GET", url) as response:
            response.raise_for_status()
            content_length = response.headers.get("content-length")
            if content_length and int(content_length) > max_bytes:
                raise ValueError(too_large_message)
            async for chunk in response.aiter_bytes():
                size += len(chunk)
                if size > max_bytes:
                    raise ValueError(too_large_message)
                if destination is None:
                    chunks.append(chunk)
                else:
                    destination.write(chunk)
    return b"".join(chunks)


async def _download_image(url: str) -> bytes:
    return await _download_media(url, MAX_IMAGE_BYTES, "图片超过 8 MB 限制")


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
    if qr_points is not None:
        return ImageScanResult(text="", has_qr_code=True)

    result, _ = _get_ocr_engine()(prepared)
    text = "\n".join(str(item[1]) for item in result or [] if len(item) >= 2)
    return ImageScanResult(text=text, has_qr_code=False)


def _scan_video_file(
    path: str,
    should_stop: Callable[[ImageScanResult], bool] | None = None,
) -> ImageScanResult:
    import cv2

    texts: list[str] = []
    has_qr_code = False
    started = monotonic()
    frames_scanned = 0
    stopped_early = False
    capture = cv2.VideoCapture(path)
    try:
        if not capture.isOpened():
            raise ValueError("无法解析视频")
        fps = capture.get(cv2.CAP_PROP_FPS)
        frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
        if fps <= 0 or frame_count <= 0:
            raise ValueError("无法读取视频时长")

        timestamp = 0.0
        while True:
            frame_index = int(round(timestamp * fps))
            if frame_index >= frame_count:
                break
            capture.set(cv2.CAP_PROP_POS_FRAMES, frame_index)
            success, frame = capture.read()
            if not success:
                break
            encoded, image = cv2.imencode(".png", frame)
            if not encoded:
                raise ValueError("无法编码视频帧")
            frame_result = _scan_image_bytes(image.tobytes())
            if frame_result.text:
                texts.append(frame_result.text)
            has_qr_code = has_qr_code or frame_result.has_qr_code
            frames_scanned += 1
            if should_stop is not None and should_stop(frame_result):
                stopped_early = True
                break
            timestamp += VIDEO_FRAME_INTERVAL_SECONDS

        if frames_scanned == 0:
            raise ValueError("视频没有可读取的帧")
    finally:
        capture.release()

    logger.info(
        "Anti-fraud video frames scanned: count=%d elapsed=%.2fs stopped_early=%s",
        frames_scanned,
        monotonic() - started,
        stopped_early,
    )
    return ImageScanResult(text="\n".join(texts), has_qr_code=has_qr_code)


def _scan_video_bytes(
    data: bytes,
    should_stop: Callable[[ImageScanResult], bool] | None = None,
) -> ImageScanResult:
    with NamedTemporaryFile(suffix=".mp4") as video_file:
        video_file.write(data)
        video_file.flush()
        return _scan_video_file(video_file.name, should_stop)


async def scan_image_url(url: str) -> ImageScanResult:
    data = await _download_image(url)
    async with _scan_lock:
        return await asyncio.to_thread(_scan_image_bytes, data)


async def scan_video_url(
    url: str,
    should_stop: Callable[[ImageScanResult], bool] | None = None,
) -> ImageScanResult:
    async with _scan_lock:
        with NamedTemporaryFile(suffix=".mp4") as video_file:
            download_started = monotonic()
            await _download_media(
                url,
                MAX_VIDEO_BYTES,
                "视频超过 48 MB 限制",
                video_file,
            )
            logger.info(
                "Anti-fraud video download complete: bytes=%d elapsed=%.2fs",
                video_file.tell(),
                monotonic() - download_started,
            )
            return await asyncio.to_thread(_scan_video_file, video_file.name, should_stop)
