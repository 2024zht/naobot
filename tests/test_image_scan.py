import sys
from types import SimpleNamespace

import nao_bot.image_scan as image_scan


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
