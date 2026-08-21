import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

import nao_bot.image_scan as image_scan


def test_video_file_name_matches_supported_extensions():
    for extension in ("mp4", "mov", "m4v", "mkv", "webm", "avi", "flv", "wmv", "3gp", "ts"):
        assert image_scan.is_video_file_name(f"/group/files/clip.{extension}") is True
    assert image_scan.is_video_file_name("宣传视频.MP4") is True
    assert image_scan.is_video_file_name("说明文档.pdf") is False
    assert image_scan.is_video_file_name("") is False


def test_scan_video_url_removes_downloaded_video_after_scan(monkeypatch):
    paths = []

    async def fake_download(_url, _max_bytes, _too_large_message, destination):
        paths.append(destination.name)
        destination.write(b"video")

    def fake_scan(path, _should_stop=None):
        assert Path(path).exists()
        return image_scan.ImageScanResult("", False)

    monkeypatch.setattr(image_scan, "_download_media", fake_download)
    monkeypatch.setattr(image_scan, "_scan_video_file", fake_scan)

    result = asyncio.run(image_scan.scan_video_url("https://example.invalid/video.mp4"))

    assert result == image_scan.ImageScanResult("", False)
    assert paths and not Path(paths[0]).exists()


def test_scan_image_returns_on_qr_before_ocr(monkeypatch):
    class FakeDetector:
        def detectAndDecode(self, _image):
            return "", object(), None

    class FakeCv2:
        IMREAD_COLOR = 1

        @staticmethod
        def imdecode(_data, _mode):
            return object()

        @staticmethod
        def QRCodeDetector():
            return FakeDetector()

    monkeypatch.setitem(sys.modules, "cv2", FakeCv2)
    monkeypatch.setattr(image_scan, "_prepare_image", lambda _data: b"prepared")

    def fail_if_ocr_starts():
        raise AssertionError("OCR should not start after QR detection")

    monkeypatch.setattr(image_scan, "_get_ocr_engine", fail_if_ocr_starts)

    assert image_scan._scan_image_bytes(b"image") == image_scan.ImageScanResult("", True)


def test_scan_video_bytes_ocr_scans_one_frame_every_five_seconds(monkeypatch):
    captures = []

    class FakeCapture:
        def __init__(self, _path):
            self.positions = []
            captures.append(self)

        def isOpened(self):
            return True

        def get(self, property_id):
            return {5: 30.0, 7: 301.0}[property_id]

        def set(self, _property_id, frame_index):
            self.positions.append(frame_index)
            return True

        def read(self):
            return True, object()

        def release(self):
            return None

    class FakeCv2:
        CAP_PROP_FPS = 5
        CAP_PROP_FRAME_COUNT = 7
        CAP_PROP_POS_FRAMES = 1
        VideoCapture = FakeCapture

        @staticmethod
        def imencode(_extension, frame):
            return True, SimpleNamespace(tobytes=lambda: str(frame).encode())

    scan_results = iter(
        [
            image_scan.ImageScanResult("第一帧", False),
            image_scan.ImageScanResult("第二帧", True),
            image_scan.ImageScanResult("第三帧", False),
        ]
    )
    scanned_frames = []

    monkeypatch.setitem(sys.modules, "cv2", FakeCv2)
    monkeypatch.setattr(
        image_scan,
        "_scan_image_bytes",
        lambda data: scanned_frames.append(data) or next(scan_results),
    )

    result = image_scan._scan_video_bytes(
        b"video",
        should_stop=lambda frame: frame.has_qr_code,
    )

    assert captures[0].positions == [0, 150]
    assert len(scanned_frames) == 2
    assert result == image_scan.ImageScanResult("第一帧\n第二帧", True)
