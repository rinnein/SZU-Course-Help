from __future__ import annotations

import sys
from types import ModuleType

import pytest

import logic


@pytest.fixture(autouse=True)
def clear_engine_cache():
    original_factory = logic._ddddocr_engines
    original_factory.cache_clear()
    yield
    original_factory.cache_clear()


def test_engine_factory_uses_core_exports_when_top_level_only_has_legacy(monkeypatch):
    package = ModuleType("ddddocr")
    package.__path__ = []
    package.DdddOcr = object
    core = ModuleType("ddddocr.core")

    class DetectionEngine:
        def predict(self, image):
            return [[1, 2, 3, 4]]

    class OCREngine:
        def __init__(self, *, beta=False):
            self.beta = beta

        def predict(self, image):
            return "目标"

    core.DetectionEngine = DetectionEngine
    core.OCREngine = OCREngine
    monkeypatch.setitem(sys.modules, "ddddocr", package)
    monkeypatch.setitem(sys.modules, "ddddocr.core", core)

    detector, recognizer = logic._ddddocr_engines()

    assert detector.predict(b"image") == [[1, 2, 3, 4]]
    assert recognizer.predict(b"image") == "目标"
    assert recognizer.beta is True


def test_engine_factory_adapts_legacy_ddddocr_api(monkeypatch):
    package = ModuleType("ddddocr")
    package.__path__ = []
    constructor_options = []

    class DdddOcr:
        def __init__(self, **options):
            constructor_options.append(options)

        def detection(self, image):
            return [[5, 6, 7, 8]]

        def classification(self, image):
            return "候选"

    package.DdddOcr = DdddOcr
    monkeypatch.setitem(sys.modules, "ddddocr", package)
    monkeypatch.delitem(sys.modules, "ddddocr.core", raising=False)

    detector, recognizer = logic._ddddocr_engines()

    assert detector.predict(b"image") == [[5, 6, 7, 8]]
    assert recognizer.predict(b"image") == "候选"
    assert constructor_options == [
        {"det": True, "ocr": False, "show_ad": False},
        {"ocr": True, "det": False, "beta": True, "show_ad": False},
    ]


def test_ocr_runtime_check_accepts_predict_contract(monkeypatch):
    class Engine:
        def predict(self, image):
            return image

    monkeypatch.setattr(logic, "_ddddocr_engines", lambda: (Engine(), Engine()))

    ready, message = logic.check_ocr_runtime()

    assert ready is True
    assert "已就绪" in message


def test_ocr_runtime_check_reports_api_incompatibility(monkeypatch):
    monkeypatch.setattr(logic, "_ddddocr_engines", lambda: (object(), object()))

    ready, message = logic.check_ocr_runtime()

    assert ready is False
    assert "predict" in message


def test_ocr_runtime_check_reports_import_failure(monkeypatch):
    def unavailable():
        raise ImportError("DetectionEngine export missing")

    monkeypatch.setattr(logic, "_ddddocr_engines", unavailable)

    ready, message = logic.check_ocr_runtime()

    assert ready is False
    assert "版本不兼容" in message


def test_ocr_runtime_check_keeps_manual_login_available_on_engine_failure(monkeypatch):
    def broken_runtime():
        raise RuntimeError("ONNX provider unavailable")

    monkeypatch.setattr(logic, "_ddddocr_engines", broken_runtime)

    ready, message = logic.check_ocr_runtime()

    assert ready is False
    assert "ONNX provider unavailable" in message
