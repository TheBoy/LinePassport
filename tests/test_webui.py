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


def _run_node(tmp_path, name: str, source: str):
    path = tmp_path / name
    path.write_text(source, encoding="utf-8")
    result = subprocess.run(
        ["node", str(path)], capture_output=True, text=True, encoding="utf-8", timeout=60
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


# The auto-reply AI form against GET /api/assistant/settings as the server sends it:
# the provider list in server order, each with its own saved key preview and models.
BRAIN_PRELUDE = FAKE_DOM + r"""
const state = {assistantBrain: null};
const posted = [];
const answers = [];
function post(url, body) {
  posted.push([url, JSON.parse(JSON.stringify(body))]);
  return new Promise((resolve, reject) => answers.push({resolve, reject}));
}
const toasts = [];
function toastError(err) { toasts.push(err.code || err.message); }
const notes = [];
function toast(message, isError) { notes.push([message, Boolean(isError)]); }
let autoReplyLoads = 0;
function loadAssistantAutoReply() { autoReplyLoads += 1; return Promise.resolve(); }
let setupRefreshes = 0;
function refreshAssistantSetup() { setupRefreshes += 1; }
const entry = (id, label, group, extra) => ({
  id, label, group, available: true, unavailableReason: "", keyRequired: true, hasApiKey: false,
  apiKeyPreview: "", keyHint: "", keyPlaceholder: "sk-...", chatModel: "", embeddingModel: "",
  defaultChatModel: "", defaultEmbeddingModel: "", embeddings: false, needsBaseUrl: false, baseUrl: "",
  ...extra
});
const PROVIDERS = [
  entry("gemini", "Gemini (Google)", "main", {keyHint: "aistudio.google.com -> Get API key",
    keyPlaceholder: "AIza...", defaultChatModel: "gemini-3.8-flash",
    defaultEmbeddingModel: "gemini-embedding-001", embeddings: true}),
  entry("anthropic", "Claude (Anthropic)", "main", {keyHint: "console.anthropic.com -> API keys",
    keyPlaceholder: "sk-ant-...", hasApiKey: true, apiKeyPreview: "****abcd",
    chatModel: "claude-opus-5-5", defaultChatModel: "claude-opus-5-5"}),
  entry("openai", "ChatGPT (OpenAI)", "main", {keyHint: "platform.openai.com -> API keys",
    defaultChatModel: "gpt-6-luna", defaultEmbeddingModel: "text-embedding-3-small", embeddings: true}),
  entry("zai", "GLM (Z.ai)", "more", {defaultChatModel: "glm-4.7"}),
  entry("xai", "Grok (xAI)", "more", {available: false, unavailableReason: "ai_provider_unavailable"}),
  entry("custom", "Custom API (OpenAI-compatible)", "custom", {keyRequired: false, hasApiKey: true,
    apiKeyPreview: "****wxyz", chatModel: "my-model", needsBaseUrl: true,
    baseUrl: "https://llm.example.com/v1"})
];
const brain = (extra) => ({
  provider: "", source: "none", configured: false, enabled: false, canEdit: true, notice: "",
  imageKeyAvailable: false, strictKnowledge: false, chatModel: "", embeddingModel: "", baseUrl: "",
  hasApiKey: false, apiKeyPreview: "", providers: JSON.parse(JSON.stringify(PROVIDERS)), ...extra
});
const byId = (root, suffix) => root.find((el) => typeof el.id === "string" && el.id.endsWith(suffix));
const byText = (root, tag, text) => root.find((el) => el.tag === tag && el.textContent === text);
const labelOf = (root, text) => root.find((el) => el.tag === "label" && el.children[0] && el.children[0].textContent === text);
function view(slot) {
  const form = slot.children[0];
  const key = byId(form, "Key");
  const url = byId(form, "Url");
  const [keyLabel, keyRow, keySaved, keyHint] = key.parent.parent.children;
  const chat = labelOf(form, "brain.model");
  const search = labelOf(form, "brain.embedding_model");
  return {
    form, key, url, keyLabel, keySaved, keyHint, pick: byId(form, "Provider"),
    notice: form.children[0], pickNote: form.children[2], urlField: url.parent.parent,
    reveal: keyRow.children[1], chatInput: chat.children[2], chatList: chat.children[1],
    search, searchInput: search.children[2],
    refresh: byText(form, "button", "brain.refresh_models"), save: byText(form, "button", "brain.save_test")
  };
}
function fields(v) {
  return {
    provider: v.pick.value, keyLabel: v.keyLabel.children[0].textContent,
    placeholder: v.key.placeholder, key: v.key.value,
    saved: v.keySaved.hidden ? null : v.keySaved.textContent,
    hint: v.keyHint.hidden ? null : v.keyHint.textContent,
    url: v.urlField.hidden ? null : v.url.value, model: v.chatInput.value,
    search: v.search.hidden ? null : v.searchInput.value,
    note: v.pickNote.hidden ? null : v.pickNote.textContent
  };
}
""" + "\n"


def _brain_source() -> str:
    # The summary, the model box helper and the form, run as the page runs them.
    return _block("    function brainProviderInfo", "    async function loadKpiSettingsForm")


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is not installed")
def test_the_ai_form_follows_the_picked_provider_and_keeps_keys_apart(tmp_path):
    probe = BRAIN_PRELUDE + _brain_source() + r"""
const out = {};
(async () => {
  // A new workspace that lost the shared AI: the notice, and Gemini picked first.
  state.assistantBrain = brain({notice: "shared_ai_removed", imageKeyAvailable: true});
  const slot = new El("div");
  renderBrainForm(slot);
  const v = view(slot);
  out.groups = v.pick.children.map((group) => [group.tag, group.label, group.children.map((option) => option.value)]);
  const xai = v.pick.find((el) => el.value === "xai");
  out.unavailable = [xai.disabled, xai.textContent];
  out.notice = [Boolean(v.notice.hidden), v.notice.textContent];
  out.gemini = fields(v);
  v.key.value = "AIza-typed";
  v.reveal.fire("click");
  out.revealed = [v.key.type, v.reveal.textContent];
  v.pick.value = "anthropic"; v.pick.fire("change");
  out.anthropic = fields(v);
  out.hiddenAgain = [v.key.type, v.reveal.textContent];
  let run = v.refresh.listeners.click[0]();
  answers.shift().resolve({chatModels: ["claude-opus-5-5", "claude-sonnet-5-5"], embeddingModels: []});
  await run;
  out.refreshAnthropic = posted.pop();
  out.listed = [Boolean(v.chatList.hidden), v.chatList.children.map((option) => option.value)];
  v.pick.value = "custom"; v.pick.fire("change");
  out.custom = fields(v);
  out.listCleared = v.chatList.hidden;
  v.key.value = " sk-custom "; v.url.value = "https://llm2.example.com/v1";
  run = v.refresh.listeners.click[0]();
  answers.shift().resolve({chatModels: ["m1"], embeddingModels: ["e1"]});
  await run;
  out.refreshCustom = posted.pop();
  run = v.save.listeners.click[0]();
  out.saveCustom = posted.pop();
  const saved = brain({provider: "custom", source: "tenant", configured: true, chatModel: "my-model",
    baseUrl: "https://llm2.example.com/v1", test: {ok: true, error: ""}});
  Object.assign(saved.providers.find((item) => item.id === "custom"),
    {apiKeyPreview: "****tom1", baseUrl: "https://llm2.example.com/v1"});
  answers.shift().resolve(saved);
  await run;
  const result = v.save.parent.children[1];
  // The setup steps and the auto reply page's AI badge follow the saved AI.
  out.saved = [result.textContent, result.className, v.key.value, v.keySaved.textContent,
    Boolean(v.notice.hidden), setupRefreshes, state.assistantBrain === saved, autoReplyLoads,
    state.assistantBrainTest];
  // A key typed for Gemini is never sent with another provider.
  v.pick.value = "gemini"; v.pick.fire("change");
  v.key.value = "AIza-for-gemini";
  v.pick.value = "openai"; v.pick.fire("change");
  out.openai = fields(v);
  run = v.save.listeners.click[0]();
  out.saveOpenai = posted.pop();
  answers.shift().reject(Object.assign(new Error("Enter the API key first."), {code: "ai_key_required"}));
  await run;
  out.failed = [v.save.parent.children[1].textContent, toasts.slice(), v.save.disabled];
  console.log(JSON.stringify(out));
})().catch((err) => { console.error(err); process.exit(1); });
"""
    out = _run_node(tmp_path, "brain.js", probe)
    assert out["groups"] == [
        ["optgroup", "brain.group_main", ["gemini", "anthropic", "openai"]],
        ["optgroup", "brain.group_more", ["zai", "xai"]],
        ["optgroup", "brain.group_custom", ["custom"]],
    ]
    assert out["unavailable"] == [True, "Grok (xAI) (brain.unavailable)"]
    assert out["notice"] == [False, "brain.notice"]
    assert out["gemini"] == {
        "provider": "gemini", "keyLabel": "brain.api_key", "placeholder": "AIza...", "key": "",
        "saved": "brain.key_shared_hint",
        "hint": 'brain.key_where {"hint":"aistudio.google.com -> Get API key"}',
        "url": None, "model": "gemini-3.8-flash", "search": "gemini-embedding-001", "note": None,
    }
    assert out["revealed"] == ["text", "ai.hide"]
    # Switching clears the typed key and shows that provider's own saved key and models.
    assert out["anthropic"] == {
        "provider": "anthropic", "keyLabel": "brain.api_key", "placeholder": "sk-ant-...", "key": "",
        "saved": 'brain.key_saved {"preview":"****abcd"}',
        "hint": 'brain.key_where {"hint":"console.anthropic.com -> API keys"}',
        "url": None, "model": "claude-opus-5-5", "search": None, "note": None,
    }
    assert out["hiddenAgain"] == ["password", "ai.reveal"]
    assert out["refreshAnthropic"] == ["/api/ai/models", {"provider": "anthropic", "apiKey": ""}]
    assert out["listed"] == [False, ["claude-opus-5-5", "claude-sonnet-5-5", ""]]
    assert out["custom"] == {
        "provider": "custom", "keyLabel": "brain.api_key_optional", "placeholder": "sk-...", "key": "",
        "saved": 'brain.key_saved {"preview":"****wxyz"}', "hint": "brain.key_optional_hint",
        "url": "https://llm.example.com/v1", "model": "my-model", "search": "", "note": None,
    }
    assert out["listCleared"] is True  # a list belongs to the provider it came from
    assert out["refreshCustom"] == [
        "/api/ai/models",
        {"provider": "custom", "apiKey": "sk-custom", "baseUrl": "https://llm2.example.com/v1"},
    ]
    assert out["saveCustom"] == [
        "/api/assistant/settings",
        {"provider": "custom", "apiKey": "sk-custom", "baseUrl": "https://llm2.example.com/v1",
         "chatModel": "my-model", "embeddingModel": "", "strictKnowledge": False, "test": True},
    ]
    assert out["saved"] == [
        "brain.test_ok", "badge ok", "", 'brain.key_saved {"preview":"****tom1"}', True, 2, True, 1,
        {"ok": True, "error": ""},
    ]
    assert out["openai"] == {
        "provider": "openai", "keyLabel": "brain.api_key", "placeholder": "sk-...", "key": "",
        "saved": None, "hint": 'brain.key_where {"hint":"platform.openai.com -> API keys"}',
        "url": None, "model": "gpt-6-luna", "search": "text-embedding-3-small", "note": None,
    }
    assert out["saveOpenai"] == [
        "/api/assistant/settings",
        {"provider": "openai", "chatModel": "gpt-6-luna", "embeddingModel": "text-embedding-3-small",
         "strictKnowledge": False, "test": True},
    ]
    assert out["failed"] == ["", ["ai_key_required"], False]


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is not installed")
def test_the_ai_form_has_unique_ids_in_both_slots_and_says_what_it_uses(tmp_path):
    probe = BRAIN_PRELUDE + _brain_source() + r"""
const out = {};
const ids = (root) => {
  const found = [];
  const walk = (el) => { if (el.id) found.push(el.id); el.children.forEach(walk); };
  walk(root);
  return found;
};
const strays = (root, own) => {
  const bad = [];
  const walk = (el) => {
    if (el.tag === "label" && el.htmlFor && !own.includes(el.htmlFor)) bad.push(el.htmlFor);
    for (const ref of String(el.attrs["aria-describedby"] || "").split(" ").filter(Boolean)) {
      if (!own.includes(ref)) bad.push(ref);
    }
    el.children.forEach(walk);
  };
  walk(root);
  return bad;
};
// Settings -> AI and the setup steps on the auto reply page, at the same time.
state.assistantBrain = brain({notice: "shared_ai_removed"});
const settingsSlot = new El("div");
const setupSlot = new El("div");
renderBrainForm(settingsSlot);
renderBrainForm(setupSlot, {notice: false});
const a = ids(settingsSlot);
const b = ids(setupSlot);
out.ids = [a.length, b.length, new Set([...a, ...b]).size, [...strays(settingsSlot, a), ...strays(setupSlot, b)]];
// The page shows the warning once: the setup steps leave theirs out.
out.notices = [Boolean(view(settingsSlot).notice.hidden), Boolean(view(setupSlot).notice.hidden)];
// A saved provider this server cannot run is still shown, with the reason.
I18N["errors.ai_provider_unavailable"] = {th: "x", en: "x"};
const providers = JSON.parse(JSON.stringify(PROVIDERS));
providers[1].available = false;
providers[1].unavailableReason = "ai_provider_unavailable";
state.assistantBrain = brain({provider: "anthropic", source: "tenant", configured: true,
  chatModel: "claude-opus-5-5", notice: "ollama_removed", providers});
const savedSlot = new El("div");
renderBrainForm(savedSlot);
const v = view(savedSlot);
out.unavailableSaved = [v.pick.value, v.pick.find((el) => el.value === "anthropic").disabled,
  fields(v).note, Boolean(v.notice.hidden)];
// Staff see what the shop uses, and the warning, but no form.
state.assistantBrain = brain({canEdit: false, notice: "ollama_removed"});
const readOnly = new El("div");
renderBrainForm(readOnly);
out.readOnly = readOnly.children[0].children.map((el) => [el.textContent, Boolean(el.hidden)]);
out.summaries = [
  brainSummary(null),
  brainSummary(brain({provider: "openai", configured: true, chatModel: "gpt-6-luna"})),
  brainSummary(brain({provider: "custom", configured: true, chatModel: "my-model",
    baseUrl: "https://llm.example.com:8443/v1"})),
  brainSummary(brain({provider: "openai", configured: false})),
  brainSummary({provider: "zai", configured: true, chatModel: "", providers: PROVIDERS}),
  brainSummary({provider: "mystery", configured: true, chatModel: "m"}),
  // Staff are not sent the Custom API address.
  brainSummary({provider: "custom", configured: true, chatModel: "my-model", baseUrl: "",
    providers: PROVIDERS.map((item) => ({...item, baseUrl: ""}))})
];
console.log(JSON.stringify(out));
"""
    out = _run_node(tmp_path, "brain_slots.js", probe)
    settings_ids, setup_ids, unique, strays = out["ids"]
    assert settings_ids >= 8 and setup_ids == settings_ids
    assert unique == settings_ids + setup_ids
    assert strays == []  # every label and description points inside its own form
    assert out["notices"] == [False, True]
    assert out["unavailableSaved"] == ["anthropic", True, "errors.ai_provider_unavailable", True]
    # Staff read who has to act, not "add your own key".
    assert out["readOnly"] == [
        ["brain.notice_staff", False], ["brain.none", False], ["brain.owner_only", False],
    ]
    assert out["summaries"] == [
        "brain.none",
        "ChatGPT (OpenAI) (gpt-6-luna)",
        'brain.summary_custom {"host":"llm.example.com:8443","model":"my-model"}',
        "brain.none",
        "GLM (Z.ai) (glm-4.7)",  # a blank model means the provider's default
        "mystery (m)",
        'brain.summary_custom_model {"model":"my-model"}',
    ]


def test_the_ai_form_strings_have_thai_and_english_text():
    def has_both(key: str) -> bool:
        return bool(re.search(rf'"{re.escape(key)}": {{th: "[^"]+", en: "[^"]+"}}', INDEX_HTML))

    for key in (
        "brain.provider", "brain.group_main", "brain.group_more", "brain.group_custom",
        "brain.unavailable", "brain.api_key", "brain.api_key_optional", "brain.key_saved",
        "brain.key_where", "brain.key_optional_hint", "brain.base_url", "brain.base_url_hint",
        "brain.model_custom_ph", "brain.summary_custom", "brain.summary_custom_model",
        "brain.notice", "brain.notice_staff", "brain.key_new_address", "brain.key_note_zai",
        "assistant.auto_reply_ai_missing", "assistant.auto_reply_ai_removed",
        "assistant.source_shared", "botlog.action.ai.auto_reply.dropped",
    ):
        assert has_both(key), key
    # Every key the form and its summary name exists in both languages.
    asked = set(re.findall(r'"((?:brain|errors|ai)\.[a-z_]+)"', _brain_source()))
    assert {"brain.notice", "brain.notice_staff", "brain.key_note_zai"} <= asked
    for key in sorted(asked):
        assert has_both(key), key


def test_no_ollama_or_shared_ai_settings_are_left_in_the_pages():
    for html in (INDEX_HTML, GOD_HTML):
        assert "Ollama" not in html and "ollama" not in html
    for gone in ("brain.global", "globalAvailable", "brain.summary_global"):
        assert gone not in INDEX_HTML, gone
    assert "choice-card" not in _brain_source()  # one provider picker, no cards
    # The God page keeps the shared knowledge (keyword search) and the unanswered queue.
    for gone in ('data-god-page-target="ai-settings"', 'data-god-page="ai-settings"',
                 '"/api/god/ai/settings"', '"/api/god/ai/test"', "globalAi", "Embedding",
                 "Vector index"):
        assert gone not in GOD_HTML, gone
    assert 'data-god-page-target="knowledge"' in GOD_HTML
    assert 'id="reindexKnowledgeButton"' in GOD_HTML and '"/api/god/ai/reindex"' in GOD_HTML
    assert '"members", "knowledge", "unanswered", "system", "guide"' in GOD_HTML
    # The guide counts 1, 2, 3 without the removed steps.
    assert re.findall(r'<span class="god-guide-number">(\d+)</span>', GOD_HTML) == ["1", "2", "3"]
    # The unanswered badge loads its own count when the page opens.
    begin = GOD_HTML.index('api("/api/god/status").then(')
    assert "loadUnanswered().catch(() => {});" in GOD_HTML[begin:GOD_HTML.index("}).catch(", begin)]


def test_every_element_the_god_script_uses_is_on_the_page():
    script = re.search(r"<script>([\s\S]*?)</script>", GOD_HTML).group(1)
    markup = GOD_HTML[: GOD_HTML.index("<script>")]
    created = set(re.findall(r'\.id = "([^"]+)"', script))
    used = set(re.findall(r'\$\("([^"]+)"\)', script))
    assert len(used) > 40
    missing = sorted(name for name in used if name not in created and f'id="{name}"' not in markup)
    assert missing == []  # a listener on a missing element would stop the whole God page
    for name in used - created:
        assert markup.count(f'id="{name}"') == 1, name


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is not installed")
def test_god_script_is_valid_javascript(tmp_path):
    script = re.search(r"<script>([\s\S]*?)</script>", GOD_HTML).group(1)
    path = tmp_path / "god.js"
    path.write_text(script, encoding="utf-8")
    result = subprocess.run(
        ["node", "--check", str(path)], capture_output=True, text=True, timeout=60
    )
    assert result.returncode == 0, result.stderr


def test_auto_reply_warns_when_its_ai_is_missing_or_was_removed():
    label = _block("    function assistantAutoReplyRuntimeLabel", "    function syncAssistantAutoReplySwitch")
    assert 'ai_missing: "assistant.auto_reply_ai_missing"' in label
    assert 'return ["error", "login_required", "ai_missing"].includes(runtimeState);' in label
    for start, end in (
        ("    function renderAssistantAutoReply", "    async function loadAssistantAutoReply"),
        ("    function refreshAssistantSetup", "    async function loadAssistantOverview"),
    ):
        assert "autoReplyNeedsAttention(runtimeState)" in _block(start, end), start
    # The auto reply page tops itself with the warning; its setup form leaves its own out.
    assert INDEX_HTML.index('id="assistantAiNotice"') < INDEX_HTML.index('id="assistantSetup"')
    assert 'id="assistantAiNotice" style="margin-bottom:12px" data-i18n="brain.notice"' in INDEX_HTML
    setup = _block("    function refreshAssistantSetup", "    async function loadAssistantOverview")
    assert 'aiNotice.classList.toggle("hidden", !(brain && brain.notice && !brain.configured));' in setup
    assert 'brain && brain.canEdit === false ? "brain.notice_staff" : "brain.notice"' in setup
    assert "renderBrainForm(brainSlot, {notice: false});" in setup
    # A provider that failed to answer: the translated reason plus what it said.
    assert "aiAskError(err)" in _block("    async function askAssistant", "    function aiAskError")
    assert "aiAskError(err);" in _block("    async function askKbChat", "    async function loadKbUnanswered")


def test_the_new_error_codes_have_thai_and_english_text():
    for code in (
        "proxy_username_invalid", "proxy_zone_username_invalid", "proxy_zone_ip_invalid",
        "proxy_pool_invalid", "proxy_provider_unknown", "proxy_credentials_required",
        "proxy_auth_failed", "proxy_forbidden", "proxy_unreachable", "proxy_check_failed",
        "proxy_missing", "ai_key_required", "ai_key_invalid",
        "proxy_host_invalid", "proxy_port_invalid", "proxy_credentials_invalid",
        "proxy_scheme_invalid", "invalid_proxy_url",
        # The AI providers: every code the settings, models and ask routes answer with.
        "ai_settings_invalid", "ai_base_url_invalid", "ai_base_url_blocked",
        "ai_base_url_required", "ai_base_url_not_found",
        "ai_model_required", "ai_provider_unavailable", "ai_unavailable",
    ):
        match = re.search(rf'"errors\.{code}": {{th: "[^"]+", en: "[^"]+"}}', INDEX_HTML)
        assert match, code


_I18N_ENTRY = re.compile(
    r'"([A-Za-z0-9_.\-]+)":\s*\{\s*th:\s*"((?:[^"\\]|\\.)*)",\s*en:\s*"((?:[^"\\]|\\.)*)"\s*\}'
)


def _i18n() -> dict:
    """The page's I18N entries as the page reads them (a later entry wins)."""
    return {
        key: {"th": json.loads(f'"{th}"'), "en": json.loads(f'"{en}"')}
        for key, th, en in _I18N_ENTRY.findall(INDEX_HTML)
    }


def _with_page_text(prelude: str) -> str:
    """The prelude with the page's own I18N text and t() instead of the key stubs."""
    stubs = (
        "const I18N = {};\n"
        'const t = (key, vars) => vars ? key + " " + JSON.stringify(vars) : key;\n'
    )
    assert stubs in prelude
    page_t = _block("    function t(key, vars) {", "    function textSpan(text, cls) {")
    return prelude.replace(
        stubs, "const I18N = " + json.dumps(_i18n(), ensure_ascii=False) + ";\n" + page_t
    )


def _registry_providers() -> list:
    """The provider list as GET /api/assistant/settings sends it to a new workspace."""
    from okline.ai_providers import provider_available, provider_registry

    rows = []
    for provider in provider_registry():
        available, reason = provider_available(provider.id)
        rows.append({
            **provider.public_fields(), "available": available,
            "unavailableReason": "" if available else reason, "hasApiKey": False,
            "apiKeyPreview": "", "chatModel": "", "embeddingModel": "", "baseUrl": "",
        })
    return rows


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is not installed")
def test_the_key_hints_read_right_for_every_provider_in_both_languages(tmp_path):
    from okline.ai_providers import get_provider

    custom = get_provider("custom")
    assert custom is not None and custom.key_hint == ""  # the page adds the optional-key line
    providers = _registry_providers()
    probe = _with_page_text(BRAIN_PRELUDE) + _brain_source() + r"""
const REGISTRY = """ + json.dumps(providers) + r""";
const out = {};
for (const lang of ["th", "en"]) {
  state.lang = lang;
  // Even a sentence sent as the Custom API's hint never follows "Get a key at".
  const providers = JSON.parse(JSON.stringify(REGISTRY)).map((item) =>
    item.id === "custom" ? {...item, keyHint: "The key your server asks for."} : item);
  state.assistantBrain = brain({providers});
  const slot = new El("div");
  renderBrainForm(slot);
  const form = slot.children[0];
  const pick = byId(form, "Provider");
  const key = byId(form, "Key");
  const hint = byId(form, "KeyHint");
  out[lang] = {};
  for (const item of REGISTRY) {
    pick.value = item.id; pick.fire("change");
    out[lang][item.id] = [hint.hidden ? null : hint.textContent, key.placeholder];
  }
}
console.log(JSON.stringify(out));
"""
    out = _run_node(tmp_path, "hints.js", probe)
    text = _i18n()
    for lang in ("th", "en"):
        where = text["brain.key_where"][lang]
        hints = out[lang]
        assert hints["custom"] == [text["brain.key_optional_hint"][lang], ""]
        assert hints["zai"][0] == (
            where.replace("{hint}", "z.ai -> API Keys") + " " + text["brain.key_note_zai"][lang]
        )
        for provider in providers:
            if provider["id"] not in ("custom", "zai"):
                expected = where.replace("{hint}", provider["keyHint"])
                assert hints[provider["id"]][0] == expected, provider["id"]
    assert out["th"]["gemini"][0] == "สร้าง key ได้ที่ aistudio.google.com -> Get API key"
    assert out["th"]["zai"][0].endswith("ใช้ API key แบบ Standard เท่านั้น key ของ Coding Plan ใช้ที่นี่ไม่ได้")


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is not installed")
def test_a_failed_test_in_the_setup_steps_keeps_the_form_and_says_why(tmp_path):
    # The real setup steps around the real form: only the stub that counts them goes.
    prelude = BRAIN_PRELUDE.replace("function refreshAssistantSetup() { setupRefreshes += 1; }", "")
    probe = prelude + r"""
const els = {};
const $ = (id) => (els[id] = els[id] || Object.assign(new El("div"), {id}));
const localStorage = {getItem: () => null};
const selectedAccountId = () => "acc1";
const hasPermission = () => true;
""" + _block("    function assistantAutoReplyRuntimeLabel", "    function syncAssistantAutoReplySwitch") \
        + _brain_source() \
        + _block("    function refreshAssistantSetup", "    async function loadAssistantOverview") + r"""
const out = {};
const step1 = () => [$("setupStep1").classList.contains("done"), $("setupStep1").classList.contains("current")];
(async () => {
  state.assistantKnowledge = {sections: []};
  state.assistantAutoReply = {enabled: false, runtime: {state: "off"}};
  state.assistantBrain = brain();
  refreshAssistantSetup();
  const slot = $("setupBrainSlot");
  const form = slot.children[0];
  let v = view(slot);
  out.start = [Boolean(form.brainForm), ...step1()];
  // A new owner picks ChatGPT and pastes a wrong key: saved, but the test fails.
  v.pick.value = "openai"; v.pick.fire("change");
  v.key.value = "sk-wrong-key";
  let run = v.save.listeners.click[0]();
  const failed = brain({provider: "openai", source: "tenant", configured: true, enabled: true,
    chatModel: "gpt-6-luna", test: {ok: false, error: "OpenAI error 401: Incorrect API key provided."}});
  answers.shift().resolve(failed);
  await run;
  let result = v.save.parent.children[1];
  out.failed = [slot.children[0] === form, v.pick.value, result.textContent, result.className,
    ...step1(), $("assistantHeroSwitch").disabled, autoReplyLoads];
  // Something else refreshes the steps (shop info saved): the form is not redrawn.
  refreshAssistantSetup();
  out.kept = [slot.children[0] === form, result.textContent === out.failed[2]];
  // The settings are loaded again (a later visit): a new form still says why.
  state.assistantBrain = JSON.parse(JSON.stringify(failed));
  refreshAssistantSetup();
  v = view(slot);
  result = v.save.parent.children[1];
  out.redrawn = [slot.children[0] !== form, v.pick.value, result.textContent, result.className];
  // A working key closes step 1, and the slot shows what the shop uses.
  v.key.value = "sk-right-key";
  run = v.save.listeners.click[0]();
  answers.shift().resolve(brain({provider: "openai", source: "tenant", configured: true, enabled: true,
    chatModel: "gpt-6-luna", test: {ok: true, error: ""}}));
  await run;
  out.fixed = [...step1(), slot.children.map((el) => el.textContent), autoReplyLoads];
  console.log(JSON.stringify(out));
})().catch((err) => { console.error(err); process.exit(1); });
"""
    out = _run_node(tmp_path, "setup_steps.js", probe)
    failure = 'brain.test_failed {"error":"OpenAI error 401: Incorrect API key provided."}'
    assert out["start"] == [True, False, True]
    # The form and its reason stay, step 1 stays open and auto reply cannot be switched on;
    # the auto reply page's AI badge is loaded again.
    assert out["failed"] == [True, "openai", failure, "badge bad brain-status", False, True, True, 1]
    assert out["kept"] == [True, True]
    assert out["redrawn"] == [True, "openai", failure, "badge bad brain-status"]
    assert out["fixed"] == [True, False, ["ChatGPT (OpenAI) (gpt-6-luna)"], 2]


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is not installed")
def test_a_save_result_belongs_to_the_provider_it_was_for(tmp_path):
    probe = BRAIN_PRELUDE + _brain_source() + r"""
const out = {};
(async () => {
  state.assistantBrain = brain();
  const slot = new El("div");
  renderBrainForm(slot);
  const v = view(slot);
  const result = v.save.parent.children[1];
  v.key.value = "AIza-typed";
  let run = v.save.listeners.click[0]();
  answers.shift().resolve(brain({provider: "gemini", source: "tenant", configured: true,
    test: {ok: true, error: ""}}));
  await run;
  out.saved = [result.textContent, result.className];
  v.pick.value = "openai"; v.pick.fire("change");
  out.switched = [result.textContent, result.className];
  // Switched while the test runs: the answer is for the provider it was saved with.
  v.pick.value = "gemini"; v.pick.fire("change");
  run = v.save.listeners.click[0]();
  out.testing = result.textContent;
  v.pick.value = "anthropic"; v.pick.fire("change");
  answers.shift().resolve(brain({provider: "gemini", source: "tenant", configured: true,
    test: {ok: false, error: "Gemini error 400: API key not valid."}}));
  await run;
  out.late = [result.textContent, v.pick.value, notes.slice()];
  console.log(JSON.stringify(out));
})().catch((err) => { console.error(err); process.exit(1); });
"""
    out = _run_node(tmp_path, "save_result.js", probe)
    assert out["saved"] == ["brain.test_ok", "badge ok"]
    assert out["switched"] == ["", "ux-muted"]
    assert out["testing"] == "brain.testing"
    assert out["late"] == [
        "", "anthropic",
        [['Gemini (Google): brain.test_failed {"error":"Gemini error 400: API key not valid."}', True]],
    ]


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is not installed")
def test_the_saved_custom_api_key_line_follows_the_address_typed(tmp_path):
    probe = BRAIN_PRELUDE + _brain_source() + r"""
const out = {};
state.assistantBrain = brain({provider: "custom", source: "tenant", configured: true,
  chatModel: "my-model", baseUrl: "https://llm.example.com/v1"});
const slot = new El("div");
renderBrainForm(slot);
const v = view(slot);
const typed = (url) => { v.url.value = url; v.url.fire("input"); return fields(v).saved; };
out.lines = [
  fields(v).saved,
  typed("https://llm-b.example.com/v1"),
  typed("https://LLM.example.com:443/v2"),
  typed("https://llm.example.com:8443/v1"),
  typed("http://llm.example.com/v1"),
  typed("not an address yet"),
  typed("")
];
typed("https://llm-b.example.com/v1");
v.pick.value = "gemini"; v.pick.fire("change");
v.pick.value = "custom"; v.pick.fire("change");
out.back = [fields(v).url, fields(v).saved];
console.log(JSON.stringify(out));
"""
    out = _run_node(tmp_path, "custom_key.js", probe)
    saved = 'brain.key_saved {"preview":"****wxyz"}'
    moved = "brain.key_new_address"  # the server drops the key for another origin
    assert out["lines"] == [saved, moved, saved, moved, moved, saved, saved]
    assert out["back"] == ["https://llm.example.com/v1", saved]


def test_long_test_and_refresh_results_wrap_inside_the_card():
    css = INDEX_HTML[: INDEX_HTML.index("<script>")]
    rule = re.search(r"\n    \.brain-status \{([^}]*)\}", css)
    assert rule, "the .brain-status rule"
    for declaration in (
        "display: inline-block;", "max-width: 100%;", "white-space: normal;",
        "overflow-wrap: anywhere;",
    ):
        assert declaration in rule.group(1), declaration
    # After .badge, whose one-line inline-flex box it replaces for these results.
    assert css.index("\n    .badge {") < rule.start()
    brain = _brain_source()
    assert 'refreshNote.className = "badge bad brain-status";' in brain
    assert 'result.className = test.ok ? "badge ok" : "badge bad brain-status";' in brain


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is not installed")
def test_the_shared_info_is_cited_once_under_its_own_label(tmp_path):
    probe = FAKE_DOM + r"""
El.prototype.remove = function () { const all = this.parent.children; all.splice(all.indexOf(this), 1); };
const els = {};
const $ = (id) => (els[id] = els[id] || Object.assign(new El("div"), {id, scrollTop: 0, scrollHeight: 0}));
const localStorage = {setItem() {}};
let answer = null;
function post() { return Promise.resolve(answer); }
function refreshAssistantSetup() {}
function loadKbUnanswered() { return Promise.resolve(); }
function aiAskError(err) { throw err; }
function prefillUnanswered() {}
""" + _block("    function assistantSourceLabels", "    function renderAssistantUnanswered(items) {") \
        + _block("    function kbBubble(text, kind, source) {", "    async function loadKbUnanswered() {") + r"""
const SOURCES = [
  {name: "knowledge.md", score: 0.83, scope: "global"},
  {name: "ค่าส่ง", score: 0.09, scope: "shop"},
  {name: "Supplier price feed", score: 0.7, scope: "global"},
  {name: "", score: 0.2, scope: "shop"}
];
const out = {};
(async () => {
  renderAssistantHistory([
    {question: "ส่งฟรีไหม", answer: "ส่งฟรีเมื่อซื้อครบ 500 บาท", createdAt: "2026-11-22T10:00:00", sources: SOURCES},
    {question: "เปิดกี่โมง", answer: "9 โมง", createdAt: "2026-11-22T09:00:00",
      sources: [{name: "เวลาเปิด", score: 0.5}]}
  ]);
  out.history = $("assistantHistory").children.map((entry) => {
    const chips = entry.children.find((el) => el.className === "assistant-sources");
    return chips ? chips.children.map((el) => el.textContent) : null;
  });
  out.chat = [];
  for (const sources of [SOURCES, [SOURCES[0]], []]) {
    answer = {question: {answer: "ok", sources}};
    $("kbChatInput").value = "ส่งฟรีไหม";
    await askKbChat();
    const log = $("kbChatLog").children;
    out.chat.push(log[log.length - 1].children[0].textContent);
  }
  console.log(JSON.stringify(out));
})().catch((err) => { console.error(err); process.exit(1); });
"""
    out = _run_node(tmp_path, "sources.js", probe)
    # No God file or source names, the shared info once and without a score; the shop's
    # own sources keep their names and scores (a record from before scopes too).
    assert out["history"] == [
        ["เวลาเปิด (50%)"],
        ["ค่าส่ง (9%)", "kb.title (20%)", "assistant.source_shared"],
    ]
    assert out["chat"] == [
        'kb.answered_from {"names":"ค่าส่ง, assistant.source_shared"}',
        'kb.answered_from {"names":"assistant.source_shared"}',
        "kb.answered_general",
    ]


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is not installed")
def test_an_account_switched_off_with_the_removed_ai_says_so(tmp_path):
    probe = FAKE_DOM + r"""
const els = {};
const $ = (id) => (els[id] = els[id] || Object.assign(new El("div"), {id, checked: false}));
const state = {accounts: []};
const selectedAccountId = () => "acc1";
function accountLabel() { return "Shop"; }
function channelName() { return "LINE"; }
function setPill(id, text) { $(id).textContent = text; }
function assistantAutoReplyDate(value) { return value || "-"; }
function syncAssistantAutoReplyGroupConfig() {}
function setAssistantAutoReplyBusy() {}
function loadAssistantAutoReplyGroups() { return Promise.resolve(); }
""" + _block("    function assistantAutoReplyRuntimeLabel", "    function renderAssistantAutoReplyGroupSummary") \
        + _block("    function renderAssistantAutoReply(data) {", "    async function loadAssistantAutoReply() {") + r"""
const shown = (data) => {
  renderAssistantAutoReply(data);
  return !$("assistantAutoReplyAiRemoved").classList.contains("hidden");
};
console.log(JSON.stringify([
  shown({enabled: false, disabledReason: "ai_removed", runtime: {state: "off"}}),
  shown({enabled: false, disabledReason: "", runtime: {state: "off"}}),
  shown({enabled: true, disabledReason: "", aiConfigured: true, runtime: {state: "running"}}),
  shown(null)
]));
"""
    assert _run_node(tmp_path, "ai_removed.js", probe) == [True, False, False, False]
    warning = re.search(
        r'<div class="ux-warning hidden" id="assistantAutoReplyAiRemoved"[^>]*'
        r'data-i18n="([^"]+)">([^<]+)</div>',
        INDEX_HTML,
    )
    assert warning and warning.group(1) == "assistant.auto_reply_ai_removed"
    assert warning.group(2) == _i18n()["assistant.auto_reply_ai_removed"]["en"]


def test_the_removed_ai_notice_reads_true_for_every_workspace_with_one():
    text = _i18n()
    markup = re.search(
        r'<div class="ux-warning hidden" id="assistantAiNotice"[^>]*>([^<]+)</div>', INDEX_HTML
    )
    assert markup and markup.group(1) == text["brain.notice"]["en"]
    # Many workspaces with a notice never had auto reply on: the text does not say it was.
    assert "If auto reply was on" in text["brain.notice"]["en"]
    assert "ถ้าเคยเปิดตอบอัตโนมัติไว้" in text["brain.notice"]["th"]
    assert "so auto reply was switched off" not in text["brain.notice"]["en"]
    # Staff cannot add a key: their notice says who can.
    assert "account owner" in text["brain.notice_staff"]["en"]
    assert "เจ้าของบัญชี" in text["brain.notice_staff"]["th"]
    assert "your own API key" not in text["brain.notice_staff"]["en"]


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is not installed")
def test_the_god_unanswered_badge_says_when_there_are_more_than_it_loaded(tmp_path):
    script = re.search(r"<script>([\s\S]*?)</script>", GOD_HTML).group(1)
    limit = re.search(r"\n    const UNANSWERED_LIMIT = \d+;", script).group(0)
    begin = script.index("    function renderUnanswered() {")
    functions = script[begin:script.index("    function renderRoleOptions() {", begin)]
    probe = FAKE_DOM + r"""
const els = {};
const $ = (id) => (els[id] = els[id] || Object.assign(new El("div"), {id}));
function makeButton(text) { const button = new El("button"); button.textContent = text; return button; }
let asked = "";
let waiting = [];
async function api(path) {
  asked = path;
  const limit = Number(new URL(path, "https://god.example").searchParams.get("limit"));
  return {questions: waiting.slice(0, limit)};  // the route's own cap
}
function toast() {}
const state = {unanswered: [], unansweredMore: false};
""" + limit + "\n" + functions + r"""
const out = {};
(async () => {
  for (const n of [0, 3, 500, 501, 800]) {
    waiting = Array.from({length: n}, (_, i) => ({id: "q" + i, question: "q" + i, userName: "u", createdAt: ""}));
    await loadUnanswered();
    out[n] = [$("godUnansweredCount").textContent, $("godUnansweredList").children.length];
  }
  out.asked = asked;
  console.log(JSON.stringify(out));
})().catch((err) => { console.error(err); process.exit(1); });
"""
    out = _run_node(tmp_path, "god_unanswered.js", probe)
    # The page shows 500 at most; "+" only when the server has more than that.
    assert out == {
        "0": ["", 1], "3": ["(3)", 3], "500": ["(500)", 500], "501": ["(500+)", 500],
        "800": ["(500+)", 500], "asked": "/api/god/ai/unanswered?limit=501",
    }


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is not installed")
def test_god_rebuild_index_saves_typed_text_first_and_source_changes_keep_it(tmp_path):
    script = re.search(r"<script>([\s\S]*?)</script>", GOD_HTML).group(1)

    def part(start: str, end: str) -> str:
        begin = script.index(start)
        return script[begin:script.index(end, begin)]

    probe = r"""
const els = {};
const $ = (id) => (els[id] = els[id] || {id, value: "", textContent: "", className: "", disabled: false});
const state = {knowledge: null};
function renderKnowledgeSources() {}
const toasts = [];
function toast(message) { toasts.push(message); }
let saved = "# Shop\nOpening hours 9-18";
const calls = [];
async function api(path, options = {}) {
  const body = options.body ? JSON.parse(options.body) : {};
  calls.push([path, body]);
  if (path === "/api/god/ai/knowledge" && options.method === "POST") saved = body.knowledgeMarkdown;
  return {knowledgeMarkdown: saved, apiSources: [], chunkCount: 1, indexError: "", indexedAt: "2026-11-22T10:00:00"};
}
""" + part("    // Text typed into knowledge.md and not saved yet.", "    function clearKnowledgeSourceForm() {") \
        + part("    async function syncKnowledgeSource(id) {", "    async function deleteKnowledgeSource(id) {") + r"""
const out = {};
(async () => {
  await loadKnowledge();
  const editor = $("knowledgeMarkdown");
  editor.value = saved + "\nFree delivery over 500 baht";  // typed, not saved yet
  calls.length = 0;
  await reindexKnowledge();                                   // the button beside Save
  out.typed = [calls.slice(), editor.value, saved, toasts.slice(), $("reindexKnowledgeButton").disabled];
  calls.length = 0;
  toasts.length = 0;
  await reindexKnowledge();                                   // nothing typed: the index only
  out.clean = [calls.slice(), toasts.slice()];
  editor.value = saved + "\nA draft";
  calls.length = 0;
  await syncKnowledgeSource("s1");                           // an API source synced meanwhile
  out.synced = [calls.map(([path]) => path), editor.value];
  console.log(JSON.stringify(out));
})().catch((err) => { console.error(err); process.exit(1); });
"""
    out = _run_node(tmp_path, "god_reindex.js", probe)
    typed = "# Shop\nOpening hours 9-18\nFree delivery over 500 baht"
    assert out["typed"] == [
        [["/api/god/ai/knowledge", {"knowledgeMarkdown": typed}]], typed, typed,
        ["บันทึก knowledge.md และสร้างดัชนีแล้ว"], False,
    ]
    assert out["clean"] == [[["/api/god/ai/reindex", {}]], ["สร้างดัชนี Knowledge แล้ว"]]
    assert out["synced"] == [["/api/god/ai/sources/sync"], typed + "\nA draft"]


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
