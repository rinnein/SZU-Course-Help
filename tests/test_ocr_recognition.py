from __future__ import annotations

import numpy as np

import logic


def _make_captcha_like() -> np.ndarray:
    """Build an 80x250 synthetic click-captcha image.

    Layout mirrors the school captcha:
      * rows 0-1:   solid top border (excluded from target band)
      * rows 2-14:  4 compressed target glyphs at cols 82-131
      * rows 25+ :  6 candidate boxes
    Text strokes are rendered as blocks so segmentation is deterministic.
    """
    image = np.full((80, 250, 3), 255, dtype=np.uint8)

    # top border (must not be treated as a target glyph)
    image[0:2, :, :] = 0

    # 4 target glyphs at cols 82..93, 95..106, 108..119, 121..132 (rows 2-14)
    target_xs = [(82, 93), (95, 106), (108, 119), (121, 132)]
    for left, right in target_xs:
        image[2:14, left:right, :] = 0

    # 6 candidate boxes (rows 36-63)
    box_xs = [(3, 28), (40, 66), (77, 109), (128, 149), (164, 187), (197, 226)]
    for left, right in box_xs:
        image[36:63, left:right, :] = 0

    return image


def test_segment_columns_isolates_glyphs_and_skips_border_noise():
    binary = logic._binary_image(_make_captcha_like())
    result = logic._segment_columns(binary, 2, 14, 82, 132)
    assert len(result) == 4
    # x-ranges should roughly match the synthetic glyph placement
    assert result[0][1] - result[0][0] >= 3
    # intervals are strictly increasing and non-overlapping
    for i in range(1, len(result)):
        assert result[i][0] > result[i - 1][1]


def test_segmented_target_glyphs_returns_four_intervals():
    binary = logic._binary_image(_make_captcha_like())
    result = logic._segmented_target_glyphs(binary)
    assert len(result) == 4
    assert all(right - left >= 3 for left, right in result)


def test_segment_columns_merges_close_gaps():
    band = np.zeros((10, 20), dtype=np.uint8)
    # cols 3-5 and 7-8 sit two columns apart -> merged when merge_gap=2
    band[:, 3:6] = 255
    band[:, 7:9] = 255
    band[:, 15:16] = 255  # tiny noise segment
    result = logic._segment_columns(band, 0, 10, 0, 20, merge_gap=2, min_width=3)
    # columns 3-8 merge into one glyph; the 1px-wide segment at 15 is dropped
    assert len(result) == 1
    left, right = result[0]
    assert left == 3 and right == 8


def test_sanitize_candidate_boxes_discards_noise_and_duplicate_boxes():
    boxes = logic._sanitize_candidate_boxes(
        [[-2, 2, 24, 32], [0, 2, 24, 32], [40, 10, 70, 40], [60, 10, 71, 40], [90, 1, 95, 20]],
        (80, 250, 3),
    )

    assert boxes == [[0, 2, 24, 32], [40, 10, 70, 40], [60, 10, 71, 40]]


def test_all_distinct_rejects_duplicate_points():
    assert logic._all_distinct([[1, 2], [3, 4], [5, 6], [7, 8]]) is True
    assert logic._all_distinct([[1, 2], [1, 2], [5, 6], [7, 8]]) is False
    assert logic._all_distinct([[1, 2]]) is False


def test_all_in_range_bounds_points():
    assert logic._all_in_range([[1, 2], [3, 4], [5, 6], [7, 8]]) is True
    assert logic._all_in_range([[0, 0], [50, 50], [250, 80], [100, 10]]) is True
    assert logic._all_in_range([[-1, 2], [3, 4], [5, 6], [7, 8]]) is False
    assert logic._all_in_range([[901, 2], [3, 4], [5, 6], [7, 8]]) is False


def test_re_ocr_remaining_candidates_skips_used_indexes(monkeypatch):
    # Only unused candidates get OCR'd; used ones come back empty.
    calls = []

    class FakeOcr:
        def classification(self, data):
            calls.append(data)
            return "X"

    monkeypatch.setattr(logic, "_ocr_glyph", lambda ocr, img, **kw: "Y")

    glyphs = [np.zeros((3, 3, 3), dtype=np.uint8) for _ in range(6)]
    result = logic._re_ocr_remaining_candidates(FakeOcr(), glyphs, used_indexes={0, 2, 5})
    assert result == ["", "Y", "", "Y", "Y", ""]


def test_template_match_targets_requires_enough_boxes():
    image = _make_captcha_like()
    # fewer than 4 boxes -> no match
    assert logic._template_match_targets(image, [[1, 0, 10, 30], [40, 0, 60, 30]]) == []


def test_recognize_returns_empty_when_targets_not_covered(monkeypatch, tmp_path):
    # Simulate a captcha where the bottom OCR cannot cover all four targets:
    # the pipeline must return [] (never submit a guessed coordinate).
    monkeypatch.setattr(logic, "_captcha_image_path", lambda: tmp_path / "image.jpg")
    import cv2

    cv2.imwrite(str(tmp_path / "image.jpg"), _make_captcha_like())

    def fake_seg(binary):
        return [[x0, x1] for x0, x1 in [(82, 93), (95, 106), (108, 119), (121, 132)]]

    def fake_ocr_glyph(ocr, glyph, **kw):
        # Top glyphs all read as distinct targets; bottom candidates share none.
        return "吧"

    def fake_candidate_boxes(image):
        return [
            [5, 36, 30, 62],
            [42, 36, 65, 62],
            [80, 36, 110, 62],
            [125, 36, 150, 62],
            [160, 36, 190, 62],
            [200, 36, 225, 62],
        ]

    def fake_recognize_candidates(ocr, image, boxes):
        return ["宙", "莲", "蓄", "史", "蓝", "蓬"]

    def fake_re_ocr(ocr, glyphs, used):
        return ["" for _ in glyphs]

    monkeypatch.setattr(logic, "_segmented_target_glyphs", fake_seg)
    monkeypatch.setattr(logic, "_ocr_glyph", fake_ocr_glyph)
    monkeypatch.setattr(logic, "_candidate_boxes", fake_candidate_boxes)
    monkeypatch.setattr(logic, "_recognize_candidate_glyphs", fake_recognize_candidates)
    monkeypatch.setattr(logic, "_re_ocr_remaining_candidates", fake_re_ocr)

    assert logic.recognize_captcha_centers() == []


def test_recognize_returns_four_points_when_all_targets_match(monkeypatch, tmp_path):
    monkeypatch.setattr(logic, "_captcha_image_path", lambda: tmp_path / "image.jpg")
    import cv2

    cv2.imwrite(str(tmp_path / "image.jpg"), _make_captcha_like())

    boxes = [
        [5, 36, 30, 62],
        [42, 36, 65, 62],
        [80, 36, 110, 62],
        [125, 36, 150, 62],
        [160, 36, 190, 62],
        [200, 36, 225, 62],
    ]

    target_order = ["甲", "乙", "丙", "丁"]
    target_counter = {"i": 0}

    def fake_seg(binary):
        return [[x0, x1] for x0, x1 in [(82, 93), (95, 106), (108, 119), (121, 132)]]

    def fake_ocr_glyph(ocr, glyph, **kw):
        # Target glyphs are OCR'd in left-to-right order inside recognize_captcha_centers.
        char = target_order[target_counter["i"] % 4]
        target_counter["i"] += 1
        return char

    def fake_recognize_candidates(ocr, image, box_list):
        # Bottom candidates contain the four targets followed by two fillers.
        return [target_order[0], target_order[1], "戊", target_order[2], "己", target_order[3]]

    def fake_re_ocr(ocr, glyphs, used):
        return ["" for _ in glyphs]

    monkeypatch.setattr(logic, "_segmented_target_glyphs", fake_seg)
    monkeypatch.setattr(logic, "_ocr_glyph", fake_ocr_glyph)
    monkeypatch.setattr(logic, "_candidate_boxes", lambda image: boxes)
    monkeypatch.setattr(logic, "_recognize_candidate_glyphs", fake_recognize_candidates)
    monkeypatch.setattr(logic, "_re_ocr_remaining_candidates", fake_re_ocr)

    points = logic.recognize_captcha_centers()
    assert len(points) == 4
    assert logic.serialize_captcha_coordinates(points)
    # each returned coordinate is the center of a distinct candidate box
    centers = {(x1 + x2) // 2 for x1, y1, x2, y2 in boxes}
    assert {point[0] for point in points} <= centers
