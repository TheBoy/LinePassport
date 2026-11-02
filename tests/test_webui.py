"""The extra screens are stitched into the main page correctly."""

from __future__ import annotations

import json
import re
import shutil
import subprocess

import pytest

from okline import webui
from okline.webapp import GOD_HTML, INDEX_HTML


def test_every_marker_is_filled_once():
    assert "/*ext:" not in INDEX_HTML
    assert "<!--ext:" not in INDEX_HTML
    for element_id in ("tabCustomers", "tabPanelCustomers", "crmDrawer", "crmSide",
                       "crmChatFilters", "crmPanelToggle", "tabInbox", "tabPanelInbox",
                       "inboxRows", "inboxMessages", "inboxSide", "navInboxBadge"):
        assert INDEX_HTML.count(f'id="{element_id}"') == 1, element_id


def test_all_chats_comes_first_in_the_menu():
    # Above the account switcher: it covers every account, not the selected one.
    assert INDEX_HTML.index('id="tabInbox"') < INDEX_HTML.index('id="accountCard"')
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
    # LINE and Telegram are listed apart; the All chats script adds its entry on top.
    assert "accountsByChannel(state.accounts)" in gate
    assert "baseRenderGateAccountsForInbox = renderGateAccounts" in INDEX_HTML


def test_coming_back_reopens_all_chats_and_reloads_it_after_sign_in():
    # The remembered account opens underneath, but All chats stays the page shown.
    assert 'TABS_KEPT_ON_RETURN.push("inbox");' in INDEX_HTML
    status = _block("async function refreshStatus", "setView(\"workspace\");")
    assert "if (TABS_KEPT_ON_RETURN.includes(lastTab)) setTab(lastTab);" in status
    assert status.index("const opening = selectAccount();") < status.index("setTab(lastTab)")
    # Signing in again on an open page restarts the list instead of keeping its error.
    assert "baseSubmitWebAuthForInbox = submitWebAuth" in INDEX_HTML


def test_account_management_keeps_line_and_telegram_apart():
    # Settings has a LINE section and a Telegram section, each with its own add buttons.
    assert INDEX_HTML.count('id="lineAccountGroup"') == 1
    assert INDEX_HTML.count('id="telegramAccountGroup"') == 1
    assert INDEX_HTML.index('id="lineAccountList"') < INDEX_HTML.index('id="telegramAccountList"')
    assert "(accounts || []).filter(accountIsLine)" in _block(
        "function renderLineAccountList", "async function editLineAccount"
    )
    assert "renderTelegramAccountList(accounts);" in INDEX_HTML
    # The switcher and team access lists group accounts by channel too.
    assert "accountsByChannel(state.accounts)" in _block(
        "function renderAccountPopover", "function closeAccountPopover"
    )
    assert INDEX_HTML.count('ACCOUNT_ADD_ACTIONS.push({\n      channel: "telegram"') == 2
    assert "accountChecklist(data.accounts" in _block("function renderTeam", "async function createStaff")
    # Lost connections use the account's own words (QR for LINE, token or sign-in for Telegram).
    assert "const fix = reconnectKeys(account);" in INDEX_HTML
    assert "baseReconnectKeysForTg = reconnectKeys" in INDEX_HTML


def test_line_oa_accounts_have_their_own_section_and_labels():
    # LINE, LINE OA, Telegram: three sections in that order.
    assert INDEX_HTML.count('id="lineOaAccountGroup"') == 1
    assert (
        INDEX_HTML.index('id="lineAccountList"')
        < INDEX_HTML.index('id="lineOaAccountList"')
        < INDEX_HTML.index('id="telegramAccountList"')
    )
    helpers = _block("function accountChannel", "function channelHeading")
    assert '"line_oa"' in helpers and "{key: \"line_oa\", label: \"LINE OA\"" in helpers
    # The Telegram screens leave LINE OA accounts alone...
    assert "(accounts || []).filter(accountIsTelegram)" in INDEX_HTML
    assert "if (target && accountIsTelegram(target)) {" in INDEX_HTML
    # ...and the LINE OA script wraps after them, so it sees its accounts first.
    assert INDEX_HTML.index("baseReconnectKeysForTg = reconnectKeys") < INDEX_HTML.index(
        "baseReconnectKeysForOa = reconnectKeys"
    )
    assert 'ACCOUNT_ADD_ACTIONS.push({\n      channel: "line_oa"' in INDEX_HTML
    assert '"/api/lineoa/login/state"' in INDEX_HTML and '"/api/lineoa/login/pick"' in INDEX_HTML
    # All chats and the segment sender name the channel the shared way.
    assert "channelName(account) : (chat.channel" in INDEX_HTML
    assert 'a.channel !== "line_oa"' in INDEX_HTML


def test_proxy_fields_say_where_to_get_a_proxy():
    for url in ("https://iproyal.com/", "https://oxylabs.io/", "https://dataimpulse.com/"):
        assert f'"{url}"' in INDEX_HTML
    assert 'link.rel = "noopener noreferrer";' in _block("function proxyProvidersHint", "function applyI18n")
    # Every place that takes a proxy shows it: the add-account wizard and the shared form.
    assert "renderLoginProxyProviders();" in _block("function applyI18n", "function setLang")
    assert "fields.appendChild(proxyProvidersHint());" in _block(
        "function buildProxyForm", "function proxyValuesMissing"
    )
    assert "buildProxyForm(account.proxyConfigured, proxyRequiredFor(account))" in _block(
        "async function editLineAccount", "async function deleteLineAccount"
    )


def test_a_proxy_the_god_page_requires_cannot_be_switched_off():
    assert "state.proxyPolicy = data.proxyPolicy || {};" in _block(
        "async function refreshWebAuth", "state.simpleMode = !!data.simpleMode"
    )
    form = _block("function buildProxyForm", "function proxyValuesMissing")
    assert "enabled.disabled = required;" in form and 't("proxy.required_by_admin")' in form
    wizard = _block("function syncLoginProxyInput", "function openAddAccount")
    assert '$("loginProxyEnabled").disabled = required;' in wizard
    assert 'buildProxyForm(false, proxyRequiredFor({channel: "line_oa"}))' in INDEX_HTML
    assert 'data-god-page-target="system"' in GOD_HTML
    assert '"/api/god/settings"' in GOD_HTML and 'id="proxyRequiredLineOa"' in GOD_HTML


def test_line_oa_accounts_take_a_proxy_like_line_accounts():
    # A new sign-in asks for the proxy before the QR; rows edit name and proxy.
    settings = _block("function lineOaLoginSettings", "async function openLineOaLogin")
    assert "buildProxyForm(false, " in settings
    assert "proxyValuesMissing(proxy, false)" in _block(
        "async function openLineOaLogin", "const baseOpenReconnectForOa"
    )
    assert "editLineAccount(account)" in _block(
        "function renderLineOaAccountList", "const baseRenderLineAccountListForOa"
    )


def test_the_chat_alert_is_a_soft_beep_that_repeats_while_a_customer_waits():
    sound = _block("async function playNotificationSound", "function messageNotificationKey")
    assert "chatBeepSamples(context.sampleRate, urgent)" in sound
    assert "playNotificationSound({urgent: true})" in _block(
        "function checkEscalationsAndAiHolds", "function trackUnreadRooms"
    )
    # All chats sees every account, so the ring listens there (after inbox.js).
    assert INDEX_HTML.index("async function loadInbox") < INDEX_HTML.index(
        "baseLoadInboxForRing = loadInbox"
    )
    assert "if (state.inbox !== before) noteInboxForRing(before, state.inbox);" in INDEX_HTML
    ring = _block("function ringTick", "function ringBeep")
    assert "setTimeout(() => ringTick(), RING_EVERY_MS)" in ring and "if (!ringRepeats())" in ring
    assert 'ringBar.id = "chatRingBar";' in INDEX_HTML
    # Repeating is a per-browser setting, on unless turned off; the beep can be tried.
    assert 'id="notifyRepeat"' in INDEX_HTML
    assert INDEX_HTML.count('localStorage.getItem(NOTIFY_REPEAT_KEY) !== "off"') == 2
    assert "playNotificationSound({preview: true})" in INDEX_HTML


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is not installed")
def test_the_chat_beep_is_quiet_short_and_click_free(tmp_path):
    source = _block("function chatBeepSamples", "\n    }\n") + "\n    }\n"
    probe = source + (
        "\nfor (const urgent of [false, true]) {\n"
        "  const samples = chatBeepSamples(48000, urgent);\n"
        "  let peak = 0;\n"
        "  for (const value of samples) peak = Math.max(peak, Math.abs(value));\n"
        "  console.log(JSON.stringify({seconds: samples.length / 48000, peak,"
        " first: samples[0], last: samples[samples.length - 1]}));\n"
        "}\n"
    )
    path = tmp_path / "beep.js"
    path.write_text(probe, encoding="utf-8")
    result = subprocess.run(["node", str(path)], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    normal, urgent = (json.loads(line) for line in result.stdout.splitlines())
    assert normal["seconds"] < 0.6 and urgent["seconds"] < 0.8
    # Soft, and the reminder past the reply target a little louder.
    assert 0.08 < normal["peak"] < 0.15 and normal["peak"] < urgent["peak"] < 0.2
    # Starts and ends at silence, so no click.
    for beep in (normal, urgent):
        assert abs(beep["first"]) < 1e-3 and abs(beep["last"]) < 1e-3


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
