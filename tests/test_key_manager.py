from __future__ import annotations

from security import key_manager


def test_card_key_v3_is_bound_to_student_and_tamper_resistant(tmp_path, monkeypatch):
    monkeypatch.setenv("COURSE_SELECT_KEY_DIR", str(tmp_path))
    signing_key = key_manager.get_or_create_key_pair()
    card_key = key_manager.generate_card_key("2024110122", signing_key)

    assert card_key.startswith("SZU3.")
    assert key_manager.verify_card_key("2024110122", card_key)
    assert not key_manager.verify_card_key("2024110123", card_key)

    replacement = "A" if card_key[-1] != "A" else "B"
    assert not key_manager.verify_card_key("2024110122", card_key[:-1] + replacement)


def test_key_pair_is_persistent_and_public_key_matches(tmp_path, monkeypatch):
    monkeypatch.setenv("COURSE_SELECT_KEY_DIR", str(tmp_path))
    first = key_manager.get_or_create_key_pair()
    first_fingerprint = key_manager.get_public_key_fingerprint()
    second = key_manager.get_or_create_key_pair()

    assert first_fingerprint == key_manager.get_public_key_fingerprint()
    assert first.public_key().export_key(format="DER") == second.public_key().export_key(
        format="DER"
    )
    assert (tmp_path / "card_signing_private.pem").exists()
    assert (tmp_path / "card_signing_public.pem").exists()


def test_invalid_student_id_is_rejected(tmp_path, monkeypatch):
    monkeypatch.setenv("COURSE_SELECT_KEY_DIR", str(tmp_path))
    signing_key = key_manager.get_or_create_key_pair()

    try:
        key_manager.generate_card_key("student", signing_key)
    except ValueError as exc:
        assert "学号" in str(exc)
    else:
        raise AssertionError("invalid student id was accepted")
