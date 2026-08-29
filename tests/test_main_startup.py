from __future__ import annotations

import sys
from types import SimpleNamespace

import main


def test_start_course_system_does_not_prompt_for_student_id(monkeypatch, capsys):
    started = []
    fake_app = SimpleNamespace(
        get_login_url=lambda: "http://127.0.0.1:8000/login.html",
        start_server=lambda: started.append(True),
    )
    monkeypatch.setitem(sys.modules, "app", fake_app)
    monkeypatch.setattr(
        "builtins.input",
        lambda prompt: (_ for _ in ()).throw(AssertionError(f"unexpected prompt: {prompt}")),
    )

    main.start_course_system()

    assert started == [True]
    assert "请输入" not in capsys.readouterr().out
