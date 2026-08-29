from __future__ import annotations

import logic


def test_check_ocr_runtime_accepts_predict_adapters(monkeypatch):
    class Engine:
        def predict(self, image):
            return image

    monkeypatch.setattr(logic, "_ddddocr_engines", lambda: (Engine(), Engine()))

    assert logic.check_ocr_runtime() == (True, "OCR 检测与识别引擎已就绪")


def test_check_ocr_runtime_reports_initialization_failure(monkeypatch):
    monkeypatch.setattr(
        logic,
        "_ddddocr_engines",
        lambda: (_ for _ in ()).throw(RuntimeError("broken engine")),
    )

    ready, message = logic.check_ocr_runtime()

    assert ready is False
    assert "broken engine" in message
