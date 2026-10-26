"""Extra LinePassport screens kept outside ``webapp.py``.

Each feature ships plain ``.html`` / ``.css`` / ``.js`` fragments next to this
file. ``compose_index`` drops them into the main page at named markers when the
web app is imported, so the page is still served as one document.
"""

from __future__ import annotations

from importlib import resources

# Marker in INDEX_HTML -> fragments placed there, in order.
SLOTS: dict[str, list[str]] = {
    "/*ext:css*/": ["crm.css"],
    "<!--ext:nav-->": ["crm_nav.html"],
    "<!--ext:panels-->": ["crm_panels.html"],
    "<!--ext:chat-filters-->": ["crm_chat_filters.html"],
    "<!--ext:convo-actions-->": ["crm_convo_button.html"],
    "<!--ext:chat-side-->": ["crm_chat_side.html"],
    "/*ext:script*/": ["crm.js"],
}


def fragment(name: str) -> str:
    return resources.files(__package__).joinpath(name).read_text(encoding="utf-8")


def compose_index(html: str) -> str:
    for marker, names in SLOTS.items():
        if html.count(marker) != 1:
            raise RuntimeError(f"LinePassport page is missing the {marker} marker")
        html = html.replace(marker, "".join(fragment(name) for name in names))
    return html
