from companion.app.ui_actions import UiActions


def test_navigation_is_honest_before_shell_binds() -> None:
    actions = UiActions()
    assert actions.navigate("library") is False


def test_navigation_calls_bound_shell_callback() -> None:
    seen: list[str] = []
    actions = UiActions()
    actions.bind_navigation(seen.append)

    assert actions.navigate("player") is True
    assert seen == ["player"]


def test_review_calls_bound_shell_callback() -> None:
    seen: list[str] = []
    actions = UiActions()
    assert actions.review() is False

    actions.bind_review(lambda: seen.append("review"))

    assert actions.review() is True
    assert seen == ["review"]


def test_signing_bind_is_preferred_over_navigation_fallback() -> None:
    seen: list[dict] = []
    navigated: list[str] = []
    actions = UiActions()
    actions.bind_navigation(navigated.append)
    actions.bind_signing(lambda card: seen.append(dict(card)))

    assert actions.open_signing({"name": "Zidane", "year": 26}) is True
    assert seen == [{"name": "Zidane", "year": 26}]
    assert navigated == []
    assert actions.take_signing_card() == {"name": "Zidane", "year": 26}


def test_player_card_handoff_prefers_bind_over_navigation() -> None:
    seen: list[dict] = []
    navigated: list[str] = []
    actions = UiActions()
    actions.bind_navigation(navigated.append)
    actions.bind_player_card(lambda card: seen.append(dict(card)))

    assert actions.open_player_card({"name": "CR7", "year": 9}) is True
    assert seen == [{"name": "CR7", "year": 9}]
    assert navigated == []
    assert actions.take_player_card() == {"name": "CR7", "year": 9}
    assert actions.take_player_card() is None
