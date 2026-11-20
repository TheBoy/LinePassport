"""The extra screens are stitched into the main page correctly."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess

import pytest

from okline import webui
from okline.webapp import GOD_HTML, INDEX_HTML, PROXY_PROVIDERS


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


def test_proxy_fields_say_what_proxy_to_pick_without_provider_links():
    hint = _block("function proxyHint", "function applyI18n")
    assert 'box.textContent = t("proxy.pick_tips");' in hint
    assert 'createElement("a")' not in hint
    # The owner took the provider links out, old and new alike.
    for gone in ("proxy.where_lead", "examples, not endorsements"):
        assert gone not in INDEX_HTML, gone
    for domain in ("corecasys.com", "brightdata.com", "oxylabs.io", "litport.net",
                   "shifter.io", "decodo.com", "iproyal.com", "dataimpulse.com"):
        assert f"https://{domain}" not in INDEX_HTML and f"https://www.{domain}" not in INDEX_HTML
    # Every place that takes a proxy shows it: the add-account wizard and the shared form.
    assert "renderLoginProxyHint();" in _block("function applyI18n", "function setLang")
    assert "const tips = proxyHint();" in _block(
        "function buildProxyForm", "async function editLineAccount"
    )
    assert "buildProxyForm(account.proxyConfigured, proxyRequiredFor(account), account.id)" in _block(
        "async function editLineAccount", "async function deleteLineAccount"
    )


def test_both_proxy_forms_offer_providers_next_to_manual_entry():
    forms = _block("const PROXY_PROVIDER_FORMS = {", "};")
    assert '"": {user: "proxy.username"' in forms  # manual entry stays, first
    # The page lists exactly the providers the server can build.
    for provider in PROXY_PROVIDERS:
        assert f"{provider}: {{" in forms, provider
    picker = _block("function attachProxyProviderPicker", "function proxyProblem")
    assert 'post("/api/proxy/test", {' in picker
    assert 'values.proxyHost = "";' in picker  # only Litport sends a host
    assert "state.loginProxyPicker = attachProxyProviderPicker({" in INDEX_HTML
    assert "const picker = attachProxyProviderPicker({" in _block(
        "function buildProxyForm", "async function editLineAccount"
    )
    assert "state.loginProxyPicker.payload()" in _block("async function startLogin", "async function pollLogin")


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is not installed")
def test_proxy_checks_know_what_each_provider_needs(tmp_path):
    source = _block("function proxyProblem", "\n    }\n") + "\n    }\n"
    probe = source + (
        "const cases = [\n"
        "  [{proxyEnabled: false}, false],\n"
        "  [{proxyEnabled: true, proxyProvider: '', proxyHost: 'h'}, false],\n"
        "  [{proxyEnabled: true, proxyProvider: '', proxyHost: 'h', proxyPort: '1'}, false],\n"
        "  [{proxyEnabled: true, proxyProvider: 'decodo', proxyUsername: 'u'}, false],\n"
        "  [{proxyEnabled: true, proxyProvider: 'decodo', proxyUsername: 'u', proxyPassword: 'p'}, false],\n"
        "  [{proxyEnabled: true, proxyProvider: 'litport', proxyUsername: 'u', proxyPassword: 'p'}, false],\n"
        "  [{proxyEnabled: true, proxyProvider: 'litport', proxyUsername: 'u', proxyPassword: 'p',"
        " proxyHost: 'hub'}, false],\n"
        "  [{proxyEnabled: true, proxyProvider: 'oxylabs'}, true],\n"
        "  [{proxyEnabled: true, proxyProvider: '', proxyHost: ''}, true],\n"
        "  [{proxyEnabled: true, proxyProvider: 'brightdata', proxyProviderIp: '1.2.3.4'}, true],\n"
        "  [{proxyEnabled: true, proxyProvider: 'litport', proxyPort: '1337'}, true],\n"
        "];\n"
        "console.log(JSON.stringify(cases.map(([values, configured]) => proxyProblem(values, configured))));\n"
    )
    path = tmp_path / "proxy.js"
    path.write_text(probe, encoding="utf-8")
    result = subprocess.run(["node", str(path)], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == [
        "", "proxy.host_port_required", "", "proxy.credentials_required", "",
        "proxy.hub_required", "",
        "",  # a saved proxy with nothing typed is kept
        "",
        "proxy.credentials_required",  # typing only the IP still needs the zone login
        "",  # Litport's prefilled port is not typed: the saved proxy is kept
    ]


# Just enough DOM for the proxy picker: elements, events that bubble, class lists.
FAKE_DOM = r"""
class El {
  constructor(tag) {
    this.tag = tag; this.children = []; this.parent = null; this.dataset = {};
    this.listeners = {}; this.attrs = {}; this.value = ""; this.textContent = "";
    this.className = ""; this.disabled = false; this.placeholder = ""; this.type = "";
    this.classes = new Set();
  }
  get classList() {
    const set = this.classes;
    return {
      toggle(name, on) { if (on === undefined ? !set.has(name) : on) set.add(name); else set.delete(name); },
      add(...names) { names.forEach((name) => set.add(name)); },
      remove(...names) { names.forEach((name) => set.delete(name)); },
      contains(name) { return set.has(name); }
    };
  }
  appendChild(child) { child.parent = this; this.children.push(child); return child; }
  append(...children) { children.forEach((child) => this.appendChild(child)); }
  prepend(child) { child.parent = this; this.children.unshift(child); }
  before(...children) {
    const siblings = this.parent.children;
    const at = siblings.indexOf(this);
    children.forEach((child, index) => { child.parent = this.parent; siblings.splice(at + index, 0, child); });
  }
  after(...children) {
    const siblings = this.parent.children;
    const at = siblings.indexOf(this);
    children.forEach((child, index) => { child.parent = this.parent; siblings.splice(at + 1 + index, 0, child); });
  }
  setAttribute(name, value) { this.attrs[name] = value; }
  replaceChildren(...children) { this.children = []; this.append(...children); }
  focus() { this.focused = true; }
  addEventListener(type, listener) { (this.listeners[type] = this.listeners[type] || []).push(listener); }
  fire(type) {
    for (let node = this; node; node = node.parent) (node.listeners[type] || []).forEach((listener) => listener({target: this}));
  }
  get firstChild() { return this.children[0]; }
  find(test) {
    for (const child of this.children) {
      if (test(child)) return child;
      const found = child.find(test);
      if (found) return found;
    }
    return null;
  }
}
const document = {createElement: (tag) => new El(tag)};
const I18N = {};
const t = (key, vars) => vars ? key + " " + JSON.stringify(vars) : key;
function textSpan(text, cls) { const span = new El("span"); span.className = cls || ""; span.textContent = text; return span; }
function labeledInput(text, type) {
  const label = new El("label");
  label.append(textSpan(text));
  const input = new El("input");
  input.type = type || "text";
  label.append(input);
  return {label, input};
}
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is not installed")
def test_a_proxy_test_verdict_and_session_belong_to_the_values_tested(tmp_path):
    picker = _block("const PROXY_PROVIDER_FORMS = {", "    // What a switched-on proxy still lacks")
    probe = FAKE_DOM + picker + r"""
const answers = [];
const posted = [];
function post(url, body) { posted.push(body); return new Promise((resolve) => answers.push(resolve)); }
const grid = new El("div");
const field = (type) => { const part = labeledInput("x", type); grid.append(part.label); return {...part, text: part.label.firstChild}; };
const scheme = {label: new El("label")};
grid.append(scheme.label);
const host = field("text"), port = field("number"), username = field("text"), password = field("password");
const picker = attachProxyProviderPicker({
  grid, scheme, host, port, username, password, tips: () => null, account: () => "",
  base: () => ({proxyEnabled: true, proxyScheme: "http", proxyHost: host.input.value,
    proxyPort: port.input.value, proxyUsername: username.input.value, proxyPassword: password.input.value})
});
const source = grid.find((el) => el.tag === "select");
const box = grid.find((el) => el.className === "proxy-test");
const [button, result] = box.children;
const click = () => button.listeners.click[0]();
const out = {};
(async () => {
  source.value = "decodo"; source.fire("change");
  username.input.value = "spabc"; password.input.value = "pw";
  let run = click();
  answers.shift()({ip: "203.0.113.9", city: "Bangkok", country: "TH", thai: true, session: "0123456789"});
  await run;
  out.verdict = result.textContent;
  out.session = picker.payload().proxyProviderSession;
  username.input.value = "spabc2"; username.input.fire("input");
  out.afterEdit = [result.textContent, picker.payload().proxyProviderSession];
  run = click();
  password.input.value = "pw2"; password.input.fire("input");
  answers.shift()({ip: "198.51.100.1", city: "Bangkok", country: "TH", thai: true, session: "abcdef0123"});
  await run;
  out.stale = [result.textContent, picker.payload().proxyProviderSession, button.disabled];
  run = click();
  answers.shift()({city: "Bangkok", country: "TH", thai: true, session: "aaaaabbbbb"});
  await run;
  out.noIp = result.textContent;
  picker.reset();
  out.reset = [source.value, picker.payload().proxyProvider, result.textContent,
    picker.payload().proxyProviderSession];
  console.log(JSON.stringify(out));
})().catch((err) => { console.error(err); process.exit(1); });
"""
    path = tmp_path / "picker.js"
    path.write_text(probe, encoding="utf-8")
    result = subprocess.run(["node", str(path)], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    out = json.loads(result.stdout)
    assert out["verdict"].startswith("proxy.test_thai ") and "203.0.113.9" in out["verdict"]
    assert out["session"] == "0123456789"  # Start or Save keeps the tested IP
    assert out["afterEdit"] == ["", None]  # changed values: no verdict, no session
    assert out["stale"] == ["", None, False]  # a test for older values is dropped
    assert out["noIp"].startswith("proxy.test_thai_place ")
    assert out["reset"] == ["", "", "", None]  # the next account starts on manual entry


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is not installed")
def test_refreshed_models_fill_the_model_box_and_any_name_can_still_be_typed(tmp_path):
    choice = _block("    function aiModelChoice", "    function renderBrainForm")
    probe = FAKE_DOM + choice + r"""
const out = {};
const chat = new El("input"); chat.value = "my-tuned-model";
const chatChoice = aiModelChoice(chat, true, "Chat model");
// The label names the box until there is a list, then the list.
out.labelBefore = [chatChoice.label.children[0].textContent, chatChoice.label.htmlFor === chat.id];
chatChoice.fill(["gemini-2.5-flash", "gemini-2.5-pro"]);
out.labelAfter = chatChoice.label.htmlFor === chatChoice.select.id && chatChoice.select.id !== chat.id;
out.kept = [chatChoice.select.children.map((option) => option.value), chatChoice.select.value, chat.hidden];
chatChoice.select.value = "gemini-2.5-pro"; chatChoice.select.fire("change");
out.picked = [chat.value, chat.hidden];
chatChoice.select.value = ""; chatChoice.select.fire("change");
out.typing = [chat.value, chat.hidden, Boolean(chat.focused)];
// Typing a name: the label names the text box, and the list keeps its own name.
out.typingLabel = [chatChoice.label.htmlFor === chat.id, chatChoice.select.attrs["aria-label"]];
const empty = new El("input");
const emptyChat = aiModelChoice(empty, true, "Chat model");
emptyChat.fill(["qwen2.5:7b"]);
out.firstPicked = [empty.value, emptyChat.select.value];
const embed = new El("input");
const embedChoice = aiModelChoice(embed, false, "Search model");
embedChoice.fill(["nomic-embed-text:latest"]);
out.optional = [embed.value, embedChoice.select.value, embed.hidden, embedChoice.label.htmlFor === embed.id];
embedChoice.clear();
out.cleared = [embedChoice.select.hidden, embedChoice.select.children.length, embed.hidden,
  embedChoice.label.htmlFor === embed.id];
out.uniqueIds = new Set([chat.id, chatChoice.select.id, empty.id, emptyChat.select.id, embed.id, embedChoice.select.id]).size;
console.log(JSON.stringify(out));
"""
    path = tmp_path / "models.js"
    path.write_text(probe, encoding="utf-8")
    result = subprocess.run(["node", str(path)], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    out = json.loads(result.stdout)
    assert out["labelBefore"] == ["Chat model", True]
    assert out["labelAfter"] is True
    assert out["uniqueIds"] == 6
    # A saved model the provider does not list stays first and chosen.
    assert out["kept"] == [["my-tuned-model", "gemini-2.5-flash", "gemini-2.5-pro", ""], "my-tuned-model", True]
    assert out["picked"] == ["gemini-2.5-pro", True]
    assert out["typing"] == ["gemini-2.5-pro", False, True]
    assert out["typingLabel"] == [True, "Chat model"]
    assert out["firstPicked"] == ["qwen2.5:7b", "qwen2.5:7b"]  # a chat model is required
    assert out["optional"] == ["", "", False, True]  # the search model stays optional, box named
    assert out["cleared"] == [True, 0, False, True]


def test_settings_ai_has_refresh_models_for_both_ai_sections():
    brain = _block("    function renderBrainForm", "    async function loadKpiSettingsForm")
    assert 'post("/api/ai/models", {' in brain
    assert "modelChoice.fill(data.chatModels || [])" in brain
    assert "embedChoice.fill(data.embeddingModels || [])" in brain
    assert 'id="aiModelRefresh"' in INDEX_HTML
    image = _block("    function renderAiProvider", "    function updateAiModelPrice")
    assert '$("aiModelRefresh").classList.toggle("hidden", name !== "google");' in image
    assert "if (p.model && !models.includes(p.model)) models.push(p.model);" in image
    assert 'post("/api/ai/models", {provider: "gemini", purpose: "image"' in _block(
        "    async function refreshAiImageModels", "    async function loadAiSettings"
    )


def test_the_new_error_codes_have_thai_and_english_text():
    for code in (
        "proxy_username_invalid", "proxy_zone_username_invalid", "proxy_zone_ip_invalid",
        "proxy_pool_invalid", "proxy_provider_unknown", "proxy_credentials_required",
        "proxy_auth_failed", "proxy_forbidden", "proxy_unreachable", "proxy_check_failed",
        "proxy_missing", "ai_key_required", "ai_key_invalid", "ollama_url_invalid",
        "proxy_host_invalid", "proxy_port_invalid", "proxy_credentials_invalid",
        "proxy_scheme_invalid", "invalid_proxy_url",
    ):
        match = re.search(rf'"errors\.{code}": {{th: "[^"]+", en: "[^"]+"}}', INDEX_HTML)
        assert match, code


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is not installed")
def test_times_are_shown_and_picked_on_a_24_hour_clock(tmp_path):
    helpers = _block("    function fmtTime(ms)", "    function chatEventLabel")
    probe = FAKE_DOM + r"""
// Inputs keep value and disabled on the prototype, as in a browser.
class HTMLInputElement extends El {}
for (const name of ["value", "disabled"]) {
  Object.defineProperty(HTMLInputElement.prototype, name, {
    configurable: true,
    get() { return this["_" + name] === undefined ? (name === "value" ? "" : false) : this["_" + name]; },
    set(next) { this["_" + name] = name === "value" ? String(next) : Boolean(next); }
  });
}
document.createElement = (tag) => tag === "input" ? new HTMLInputElement(tag) : new El(tag);
class Event { constructor(type) { this.type = type; } }
El.prototype.dispatchEvent = function (event) { this.fire(event.type); };
const state = {lang: "en"};
""" + helpers + r"""
const out = {};
const at = Date.UTC(2026, 10, 20, 14, 5);
out.en = fmtTime(at);
state.lang = "th";
out.th = fmtTime(at);
const box = new El("label");
const input = document.createElement("input");
input.type = "time"; input.value = "09:30";
box.append(input);
const events = [];
input.addEventListener("input", () => events.push("input"));
input.addEventListener("change", () => events.push("change"));
useTime24(input);
// A <label> labels its first control: the lists come before the hidden input.
out.firstInLabel = box.children.map((el) => el.className || el.tag);
const lists = () => box.find((el) => el.className === "hm-pick time24").children.filter((el) => el.tag === "select");
out.shown = [input.hidden, lists().map((list) => list.value), lists()[0].children.length, lists()[0].children[23].value];
input.value = "18:05";
out.set = lists().map((list) => list.value);
lists()[0].value = "07"; lists()[0].fire("change");
out.picked = [input.value, events];
input.disabled = true;
out.disabled = lists().map((list) => list.disabled);
const seconds = document.createElement("input");
seconds.type = "time"; seconds.step = "1"; seconds.value = "23:59:59";
new El("label").append(seconds);
useTime24(seconds);
out.seconds = seconds.parent.find((el) => el.className === "hm-pick time24").children.filter((el) => el.tag === "select").map((list) => list.value);
const empty = document.createElement("input");
empty.type = "time";
new El("label").append(empty);
useTime24(empty);
out.empty = empty.value;
const due = document.createElement("input");
due.type = "datetime-local";
const dueBox = new El("label");
dueBox.append(due);
useDateTime24(due);
out.dueOrder = [dueBox.children.map((el) => el.className || el.tag),
  dueBox.children[0].children.map((el) => el.className || el.type || el.tag)];
due.value = "2026-11-21T10:00";
const date = dueBox.find((el) => el.type === "date");
const dueLists = dueBox.find((el) => el.className === "hm-pick time24").children.filter((el) => el.tag === "select");
out.due = [date.value, dueLists.map((list) => list.value)];
dueLists[1].value = "45"; dueLists[1].fire("change");
out.dueTime = due.value;
date.value = ""; date.fire("change");
out.dueCleared = due.value;
console.log(JSON.stringify(out));
"""
    path = tmp_path / "time24.js"
    path.write_text(probe, encoding="utf-8")
    result = subprocess.run(["node", str(path)], capture_output=True, text=True, timeout=60,
                            env={**os.environ, "TZ": "UTC"})
    assert result.returncode == 0, result.stderr
    out = json.loads(result.stdout)
    assert "14:05" in out["en"] and "PM" not in out["en"], out["en"]
    assert "14:05" in out["th"], out["th"]
    assert out["firstInLabel"] == ["hm-pick time24", "input"]
    assert out["dueOrder"] == [["hm-pick datetime24", "input"], ["date", "hm-pick time24", "time"]]
    assert out["shown"] == [True, ["09", "30"], 24, "23"]
    assert out["set"] == ["18", "05"]
    assert out["picked"] == ["07:05", ["input", "change"]]
    assert out["disabled"] == [True, True]
    assert out["seconds"] == ["23", "59", "59"]
    assert out["empty"] == "00:00"
    assert out["due"] == ["2026-11-21", ["10", "00"]]
    assert out["dueTime"] == "2026-11-21T10:45"
    assert out["dueCleared"] == ""


def test_every_time_box_and_time_format_uses_24_hours():
    assert "document.querySelectorAll('input[type=\"time\"]').forEach(useTime24);" in INDEX_HTML
    shifts = _block("    function renderKpiSettingsForm", "    async function saveKpiSettings")
    assert "useTime24(start);" in shifts and "useTime24(end);" in shifts
    assert "useDateTime24(due);" in INDEX_HTML  # the CRM task due date
    # Three lists (with seconds) fit a narrow column, and wrap rather than overlap.
    assert ".hm-pick.time24 { flex-wrap: wrap; gap: 5px 3px; }" in INDEX_HTML
    assert ".hm-pick.time24 select { flex: 0 0 auto; padding-left: 6px; padding-right: 6px; }" in INDEX_HTML
    # Every place that formats an hour fixes the clock at 24 hours.
    for match in re.finditer(r'hour: "2-digit"', INDEX_HTML):
        call = INDEX_HTML[match.start():INDEX_HTML.index("}", match.start())]
        assert 'hourCycle: "h23"' in call, call


def test_every_account_delete_dialog_warns_about_the_customers_it_deletes():
    assert '"/api/accounts/delete-impact?accountId="' in INDEX_HTML
    for start, end in (
        ("    async function deleteLineAccount", "    function renderRoleOptions"),
        ("    async function deleteLineOaAccount", "\n    }\n"),
        ("    async function deleteTelegramAccount", "\n    }\n"),
    ):
        dialog = _block(start, end)
        assert "const impact = await accountDeleteImpact(account);" in dialog, start
        assert "if (impact) body.appendChild(impact);" in dialog, start
        assert "accountDeletedToast(result);" in dialog, start


def test_the_wizard_resets_the_picker_and_a_reconnect_keeps_the_saved_proxy():
    assert "state.loginProxyPicker.reset();" in _block("function resetLoginUi", "function syncLoginProxyInput")
    start = _block("async function startLogin", "async function pollLogin")
    assert "proxyProblem(proxySettings, Boolean(reconnecting && reconnecting.proxyConfigured))" in start


def test_a_proxy_the_god_page_requires_cannot_be_switched_off():
    assert "state.proxyPolicy = data.proxyPolicy || {};" in _block(
        "async function refreshWebAuth", "state.simpleMode = !!data.simpleMode"
    )
    form = _block("function buildProxyForm", "async function editLineAccount")
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
    assert "proxyProblem(proxy, false)" in _block(
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


def test_customers_can_be_renamed_from_the_customer_panel():
    panel = _block("function renderCrmPanel", "function crmOwnNames")
    assert "head.appendChild(crmSideTitle(panel, customer, chatName, data.canEdit));" in panel
    # Their own name stays in the panel, under the team's.
    assert 't("crm.own_name", {channel, name: own.name})' in panel
    title = _block("function crmSideTitle", "function crmRename")
    assert 'edit.setAttribute("aria-label", t("crm.rename"));' in title
    # Live redraws of the panel keep a rename in progress.
    assert "return panel.renaming.form;" in title
    rename = _block("function crmRename", "async function crmAssign")
    assert 'post("/api/crm/customers/update", {id: customer.id, displayName: value})' in rename
    assert 'crmButton("crm.rename_reset", () => submit(""), "crm-link")' in rename
    # Chat lists follow a rename and find a customer by either name.
    assert "applyCrmNames();" in _block("async function loadCrmMap", "function applyCrmNames")
    assert 'String(item.originalName || "").toLocaleLowerCase().includes(search)' in INDEX_HTML
    assert "[inboxChatName(chat), chat.originalName," in INDEX_HTML


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is not installed")
def test_chat_lists_follow_a_rename_without_reloading(tmp_path):
    source = _block("function applyCrmNames", "\n    }\n") + "\n    }\n"
    probe = (
        "const title = {textContent: 'Som'};\n"
        "const $ = () => title;\n"
        "let renders = 0;\n"
        "let loads = 0;\n"
        "const renderAllList = () => { renders += 1; };\n"
        "const renderContactsList = () => {};\n"
        "const loadMessages = () => { loads += 1; return Promise.resolve(); };\n"
        "const state = {target: 'u1', crmMap: {}, contacts: [\n"
        "  {mid: 'u1', name: 'Som', originalName: ''}, {mid: 'u2', name: 'Mali'}]};\n"
        + source
        + "state.crmMap = {u1: {customerName: 'Khun Somchai', nameCustom: true},\n"
        "  u2: {customerName: 'Mali M', nameCustom: false}};\n"
        "applyCrmNames();\n"
        "const renamed = [state.contacts[0].name, state.contacts[0].originalName, title.textContent];\n"
        "applyCrmNames();\n"
        "const draws = renders;\n"
        "state.crmMap.u1.nameCustom = false;\n"
        "applyCrmNames();\n"
        "console.log(JSON.stringify({renamed, draws, loads, other: state.contacts[1].name,\n"
        "  back: [state.contacts[0].name, state.contacts[0].originalName, title.textContent]}));\n"
    )
    path = tmp_path / "names.js"
    path.write_text(probe, encoding="utf-8")
    result = subprocess.run(["node", str(path)], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    seen = json.loads(result.stdout)
    assert seen["renamed"] == ["Khun Somchai", "Som", "Khun Somchai"]
    assert seen["draws"] == 1  # nothing changed the second time, so no redraw
    assert seen["other"] == "Mali"  # only names the team gave replace LINE's
    assert seen["back"] == ["Som", "", "Som"]
    assert seen["loads"] == 2  # the open chat's messages reload to name the sender anew


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
