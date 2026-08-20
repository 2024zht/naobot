import sys
from types import SimpleNamespace

import nao_bot.image_scan as image_scan


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

    result = image_scan._scan_video_bytes(b"video")

    assert captures[0].positions == [0, 150, 300]
    assert len(scanned_frames) == 3
    assert result == image_scan.ImageScanResult("第一帧\n第二帧\n第三帧", True)
