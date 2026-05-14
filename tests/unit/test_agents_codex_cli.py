from claudeteam.agents.codex_cli import CodexCliAdapter


def test_codex_rate_limit_markers_do_not_include_bare_429():
    markers = CodexCliAdapter().rate_limit_markers()

    assert markers == []
    assert "429" not in markers
