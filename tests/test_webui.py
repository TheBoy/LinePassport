"""The extra screens are stitched into the main page correctly."""

from __future__ import annotations

import re
import shutil
import subprocess

import pytest

from okline import webui
from okline.webapp import INDEX_HTML


def test_every_marker_is_filled_once():
    assert "/*ext:" not in INDEX_HTML
    assert "<!--ext:" not in INDEX_HTML
    for element_id in ("tabCustomers", "tabPanelCustomers", "crmDrawer", "crmSide",
                       "crmChatFilters", "crmPanelToggle", "tabInbox", "tabPanelInbox",
                       "inboxRows", "inboxMessages", "inboxSide", "navInboxBadge"):
        assert INDEX_HTML.count(f'id="{element_id}"') == 1, element_id


def test_all_chats_comes_first_in_the_menu():
    assert INDEX_HTML.index('id="tabInbox"') < INDEX_HTML.index('id="tabLine"')


def _block(start: str, end: str) -> str:
    begin = INDEX_HTML.index(start)
    return INDEX_HTML[begin:INDEX_HTML.index(end, begin)]


def test_account_gate_lists_accounts_and_remembers_the_last_one():
    # The header has no account picker on desktop, so the gate must offer the accounts.
    assert INDEX_HTML.count('id="accountGateList"') == 1
    assert "if (!noAccess) renderGateAccounts();" in _block("function showGate", "function renderGateAccounts")
    gate = _block("function renderGateAccounts", "const LAST_ACCOUNT_KEY")
    assert '$("accountSelect").value = account.id;' in gate and "selectAccount()" in gate
    # A returning browser goes straight to the account it used last.
    assert "rememberAccount(accountId);" in _block("async function selectAccount", "async function refreshStatus")
    status = _block("async function refreshStatus", "setView(\"workspace\");")
    assert "rememberedAccountId()" in status and "if (!accountId && preset)" in status
    # Extensions decorate the same list: Telegram labels and the All chats entry.
    assert "baseRenderGateAccountsForTg = renderGateAccounts" in INDEX_HTML
    assert "baseRenderGateAccountsForInbox = renderGateAccounts" in INDEX_HTML


def test_compose_refuses_a_page_without_markers():
    with pytest.raises(RuntimeError):
        webui.compose_index("<html></html>")


def test_crm_script_runs_before_the_page_starts():
    script = INDEX_HTML[INDEX_HTML.index("<script>"):INDEX_HTML.index("</script>")]
    assert script.index('TABS.push("customers")') < script.index("setTab(localStorage.getItem")
    assert script.index('TABS.push("inbox")') < script.index("setTab(localStorage.getItem")


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is not installed")
def test_main_script_is_valid_javascript(tmp_path):
    script = re.search(r"<script>([\s\S]*?)</script>", INDEX_HTML).group(1)
    path = tmp_path / "page.js"
    path.write_text(script, encoding="utf-8")
    result = subprocess.run(
        ["node", "--check", str(path)], capture_output=True, text=True, timeout=60
    )
    assert result.returncode == 0, result.stderr
