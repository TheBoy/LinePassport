"""Offline tests for the interactive terminal menu (:mod:`okline.menu`).

These exercise the pure, non-network parts of the Thai/English TUI:

* ``is_mid`` routing — a raw mid short-circuits the resolver / picker with no
  API calls, while a name still goes through name matching.
* the numeric-prompt helpers re-ask on non-numbers instead of crashing.
* every menu label id has a translation in both languages (no missing keys).

No network, Node.js or ``qrcode`` is needed; :func:`okline.ui.prompt` is stubbed
so the pickers/validators can be driven deterministically.
"""

from __future__ import annotations

import pytest

import okline.menu as menu
import okline.ui as ui

# A syntactically valid user mid (u/c/r + 32 hex chars) per okline._util.is_mid.
USER_MID = "u" + "0" * 32
GROUP_MID = "c" + "a" * 32


class PromptStub:
    """Stand-in for :func:`okline.ui.prompt` that returns queued answers.

    Each call pops the next answer; once exhausted it echoes the default, just
    like the real prompt on an empty line.
    """

    def __init__(self, *answers: str) -> None:
        self.answers = list(answers)
        self.calls: list[tuple[str, str]] = []

    def __call__(self, label: str, default: str = "") -> str:
        self.calls.append((label, default))
        return self.answers.pop(0) if self.answers else default


class BoomApi:
    """Any attribute access explodes — proves a code path made no API calls."""

    def __getattr__(self, name: str):
        raise AssertionError(f"api.{name} must not be called")


@pytest.fixture(autouse=True)
def _reset_lang():
    """Keep the module-level language from leaking between tests."""
    saved = menu._LANG
    yield
    menu._LANG = saved


# ---------------------------------------------------------------------------
# is_mid routing
# ---------------------------------------------------------------------------
def test_resolve_to_returns_raw_mid_without_api_calls():
    """A raw mid resolves to itself and never touches the API."""
    assert menu._resolve_to(BoomApi(), USER_MID) == USER_MID
    assert menu._resolve_to(BoomApi(), GROUP_MID) == GROUP_MID


def test_resolve_to_blank_is_none_without_api_calls():
    """An empty target is rejected before any lookup."""
    assert menu._resolve_to(BoomApi(), "") is None


def test_pick_from_accepts_raw_mid(monkeypatch):
    """Typing a raw mid into the picker returns it verbatim (fallback path)."""
    monkeypatch.setattr(ui, "prompt", PromptStub(USER_MID))
    entries = [(GROUP_MID, "Family")]
    assert menu._pick_from(entries, "label") == USER_MID


def test_pick_from_selects_by_number(monkeypatch):
    """A number picks the matching row's mid."""
    monkeypatch.setattr(ui, "prompt", PromptStub("2"))
    entries = [(USER_MID, "Alice"), (GROUP_MID, "Bob")]
    assert menu._pick_from(entries, "label") == GROUP_MID


def test_pick_from_matches_unique_name(monkeypatch):
    """A unique name substring resolves to that row's mid."""
    monkeypatch.setattr(ui, "prompt", PromptStub("ali"))
    entries = [(USER_MID, "Alice"), (GROUP_MID, "Bob")]
    assert menu._pick_from(entries, "label") == USER_MID


def test_pick_from_zero_and_blank_cancel(monkeypatch):
    """``0`` or a blank line cancels the picker."""
    monkeypatch.setattr(ui, "prompt", PromptStub("0"))
    assert menu._pick_from([(USER_MID, "Alice")], "label") is None
    monkeypatch.setattr(ui, "prompt", PromptStub(""))
    assert menu._pick_from([(USER_MID, "Alice")], "label") is None


def test_pick_from_empty_entries_is_none(monkeypatch):
    """No entries -> None, and the prompt is never shown."""
    stub = PromptStub("1")
    monkeypatch.setattr(ui, "prompt", stub)
    assert menu._pick_from([], "label") is None
    assert stub.calls == []


# ---------------------------------------------------------------------------
# numeric prompt re-prompting
# ---------------------------------------------------------------------------
def test_prompt_int_reprompts_on_non_number(monkeypatch):
    """A non-number re-asks instead of raising; a later number is accepted."""
    stub = PromptStub("abc", "42")
    monkeypatch.setattr(ui, "prompt", stub)
    assert menu._prompt_int("how many", 10) == 42
    assert len(stub.calls) == 2  # asked twice: rejected "abc", accepted "42"


def test_prompt_int_blank_returns_default(monkeypatch):
    monkeypatch.setattr(ui, "prompt", PromptStub(""))
    assert menu._prompt_int("how many", 7) == 7


def test_prompt_int_accepts_number(monkeypatch):
    monkeypatch.setattr(ui, "prompt", PromptStub("5"))
    assert menu._prompt_int("how many", 30) == 5


def test_prompt_float_reprompts_then_accepts(monkeypatch):
    stub = PromptStub("north", "35.66")
    monkeypatch.setattr(ui, "prompt", stub)
    assert menu._prompt_float("latitude", 0.0) == pytest.approx(35.66)
    assert len(stub.calls) == 2


# ---------------------------------------------------------------------------
# translation completeness
# ---------------------------------------------------------------------------
def test_every_menu_label_has_both_languages():
    """Every section / action / title id used by the menu is translated th+en."""
    missing = []
    for key in menu._menu_keys():
        entry = menu._I18N.get(key)
        if not entry or not entry.get("th") or not entry.get("en"):
            missing.append(key)
    assert missing == [], f"menu label ids missing a translation: {missing}"


def test_all_catalogue_entries_have_both_languages():
    """Guard against a half-translated catalogue entry anywhere."""
    partial = [k for k, v in menu._I18N.items() if not v.get("th") or not v.get("en")]
    assert partial == [], f"entries missing a language: {partial}"


def test_translate_falls_back_for_unknown_key():
    """A missing key returns the key itself rather than raising."""
    assert menu.t("no.such.key") == "no.such.key"


def test_translate_respects_active_language():
    menu._LANG = "th"
    assert menu.t("ui.quit") == "ออก"
    menu._LANG = "en"
    assert menu.t("ui.quit") == "Quit"


def test_translate_interpolates_placeholders():
    menu._LANG = "en"
    assert "Bob" in menu.t("msg.added", name="Bob")


# ---------------------------------------------------------------------------
# developer-section gating
# ---------------------------------------------------------------------------
def test_dev_section_hidden_by_default(monkeypatch):
    monkeypatch.delenv("OKLINE_DEV", raising=False)
    menu._LANG = "en"
    labels = [label for label, _ in menu._menu()]
    assert menu.t("sec.dev") not in labels


def test_dev_section_shown_when_flag_set(monkeypatch):
    monkeypatch.setenv("OKLINE_DEV", "1")
    menu._LANG = "en"
    labels = [label for label, _ in menu._menu()]
    assert menu.t("sec.dev") in labels


def test_language_toggle_present_at_root():
    menu._LANG = "en"
    labels = [label for label, _ in menu._menu()]
    assert menu.t("sec.language") in labels
