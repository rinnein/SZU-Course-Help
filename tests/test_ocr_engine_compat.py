from __future__ import annotations

import sys
from types import ModuleType

import numpy as np
import pytest

import logic


@pytest.fixture(autouse=True)
def clear_engine_cache():
    logic._ddddocr_engines.cache_clear()
    yield
    logic._ddddocr_engines.cache_clear()


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


def test_ocr_pipeline_uses_predict_contract(monkeypatch):
    class Detector:
        def predict(self, image):
            return [[1, 1, 10, 20]]

    class Recognizer:
        def predict(self, image):
            return "候"

    image = np.full((80, 250, 3), 255, dtype=np.uint8)

    with monkeypatch.context() as patch:
        patch.setattr(logic, "_ddddocr_engines", lambda: (Detector(), Recognizer()))
        assert logic._candidate_boxes(image) == [[1, 26, 10, 45]]
    assert logic._ocr_glyph(Recognizer(), image[2:14, 82:94]) == "候"
