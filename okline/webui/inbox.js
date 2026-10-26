    // ---- All-channels inbox ----------------------------------------------
    TABS.push("inbox");
    const INBOX_SIDE_KEY = "okline.inboxSide";
    const INBOX_MAX_UPLOAD = 30 * 1024 * 1024;
    Object.assign(state, {
      inbox: null,
      inboxAccounts: new Map(),
      inboxChat: null,
      inboxFilter: {channel: "all", account: "", status: "", q: ""},
      inboxSideOpen: false,
      inboxSource: null,
      inboxStarted: false,
      inboxStreamedOnce: false,
      inboxSseRetry: 0,
      inboxReloadTimer: 0,
      inboxReloadAll: false,
      inboxReloadIds: new Set(),
      inboxSearchTimer: 0,
      inboxLoading: false,
      inboxReloadAgain: false,
      inboxMessageSeq: 0,
      inboxSending: false,
      inboxSignature: "",
      inboxReadAt: new Map()
    });
    try {
      state.inboxSideOpen = localStorage.getItem(INBOX_SIDE_KEY) !== "off"
        && window.matchMedia("(min-width: 1201px)").matches;
    } catch (_) { /* optional */ }

    Object.assign(I18N, {
      "tabs.inbox": {th: "แชทรวม", en: "All chats"},
      "tabs.inbox_sub": {th: "ทุกบัญชี ทุกช่องทาง", en: "Every account and channel"},
      "inbox.title": {th: "แชทรวม", en: "All chats"},
      "inbox.subtitle": {th: "ทุกบัญชี ทุกช่องทาง ในที่เดียว", en: "Every account and channel in one place"},
      "inbox.search_ph": {th: "ค้นหาชื่อ ข้อความ หรือบัญชี…", en: "Search name, message or account…"},
      "inbox.ch_all": {th: "ทั้งหมด", en: "All"},
      "inbox.ch_groups": {th: "กลุ่ม", en: "Groups"},
      "inbox.all_accounts": {th: "ทุกบัญชี", en: "All accounts"},
      "inbox.st_any": {th: "ทุกสถานะ", en: "Any status"},
      "inbox.st_unread": {th: "ยังไม่อ่าน", en: "Unread"},
      "inbox.pick": {th: "เลือกแชทจากรายการ", en: "Pick a chat"},
      "inbox.pick_hint": {th: "แชทจากทุกบัญชี LINE และ Telegram อยู่ในรายการด้านซ้าย ป้ายสีบอกว่ามาจากช่องทางและบัญชีไหน", en: "Chats from every LINE and Telegram account are on the left; the colored label shows which channel and account each one comes from."},
      "inbox.empty": {th: "ยังไม่มีแชท เมื่อลูกค้าทักเข้ามาที่บัญชีไหนก็ตาม แชทจะขึ้นที่นี่", en: "No chats yet. When a customer writes to any of your accounts, the chat shows up here."},
      "inbox.empty_filtered": {th: "ไม่มีแชทที่ตรงกับตัวกรอง", en: "No chats match these filters."},
      "inbox.group": {th: "กลุ่ม", en: "Group"},
      "inbox.group_unnamed": {th: "กลุ่ม LINE", en: "LINE group"},
      "inbox.you": {th: "คุณ: ", en: "You: "},
      "inbox.encrypted": {th: "ข้อความเข้ารหัส", en: "Encrypted message"},
      "inbox.open_in_chat": {th: "เปิดในหน้าแชทของบัญชีนี้", en: "Open in this account's chat page"},
      "inbox.disconnected": {th: "บัญชีนี้หลุดการเชื่อมต่อ ตอบแชทนี้ไม่ได้จนกว่าจะเชื่อมใหม่", en: "This account is disconnected. You can reply once it is connected again."},
      "inbox.no_send": {th: "คุณดูแชทได้อย่างเดียว ตอบไม่ได้", en: "You can read this chat but not reply."},
      "inbox.file_too_big": {th: "ไฟล์ใหญ่เกิน 30 MB", en: "The file is larger than 30 MB."}
    });

    // The inbox has its own customer panel (see the Chat page's one in the CRM script).
    // Group chats have no customer card, so the panel stays out of their way.
    function inboxSideShown() {
      return state.inboxSideOpen && Boolean(state.inboxChat) && state.inboxChat.kind !== "group";
    }

    const INBOX_PANEL = {
      bodyId: "inboxSideBody",
      key: "",
      data: null,
      context: () => ({
        open: inboxSideShown() && state.tab === "inbox",
        accountId: state.inboxChat ? state.inboxChat.accountId : "",
        chatId: state.inboxChat ? state.inboxChat.chatId : "",
        name: state.inboxChat ? inboxChatName(state.inboxChat) : ""
      })
    };
    state.crmPanels.push(INBOX_PANEL);

    function inboxAccount(id) {
      return state.inboxAccounts.get(id) || (state.accounts || []).find((item) => item.id === id) || null;
    }

    function inboxChatName(chat) {
      if (chat.name) return chat.name;
      if (chat.kind === "group") return t("inbox.group_unnamed");
      return chat.username ? "@" + chat.username : t("crm.no_name");
    }

    function inboxUnread(chat) {
      const readAt = state.inboxReadAt.get(chat.key) || 0;
      return chat.lastMessageAt && chat.lastMessageAt <= readAt ? 0 : Number(chat.unread) || 0;
    }

    function inboxPill(chat, extraClass) {
      const account = inboxAccount(chat.accountId);
      const channelName = chat.channel === "telegram" ? "Telegram" : "LINE";
      const label = account ? (account.label || account.id) : chat.accountId;
      const pill = textSpan(`${channelName} · ${label}`, "inbox-pill " + chat.channel + (extraClass ? " " + extraClass : ""));
      pill.title = `${channelName} · ${label}`;
      return pill;
    }

    function inboxPreview(chat) {
      let text;
      if (chat.lastEncrypted) text = "🔒 " + t("inbox.encrypted");
      else if (chat.lastContentType && !chat.lastPreview) text = contentLabel({contentType: chat.lastContentType});
      else text = chat.lastPreview || "";
      return (chat.lastFromMe && text ? t("inbox.you") : "") + text;
    }

    function inboxMatches(chat) {
      const f = state.inboxFilter;
      const me = (state.inbox && state.inbox.me) || state.crmMe;
      if (f.channel === "line" && chat.channel !== "line") return false;
      if (f.channel === "telegram" && chat.channel !== "telegram") return false;
      if (f.channel === "group" && chat.kind !== "group") return false;
      if (f.account && chat.accountId !== f.account) return false;
      if (f.status === "unread" && !inboxUnread(chat)) return false;
      if (f.status === "mine" && !(chat.assignedTo && chat.assignedTo === me && chat.status !== "closed")) return false;
      if (f.status === "unassigned" && !(chat.kind === "contact" && !chat.assignedTo && chat.status !== "closed")) return false;
      if ((f.status === "pending" || f.status === "closed") && chat.status !== f.status) return false;
      if (f.q) {
        const account = inboxAccount(chat.accountId);
        const haystack = [inboxChatName(chat), chat.username, chat.lastPreview, account && account.label]
          .filter(Boolean).join(" ").toLocaleLowerCase();
        if (!haystack.includes(f.q.toLocaleLowerCase())) return false;
      }
      return true;
    }

    async function loadInbox({silent = false} = {}) {
      if (!state.currentUser || !hasPermission("read")) return;
      if (state.inboxLoading) {
        state.inboxReloadAgain = true;
        return;
      }
      state.inboxLoading = true;
      try {
        if (!silent && !state.inbox) listLoading("inboxRows");
        // The CRM basics give the names behind the assignee initials.
        const [data] = await Promise.all([api("/api/inbox"), loadCrmBasics().catch(() => {})]);
        state.inbox = data;
        state.inboxAccounts = new Map((data.accounts || []).map((item) => [item.id, item]));
        if (state.inboxChat) {
          const fresh = (data.chats || []).find((chat) => chat.key === state.inboxChat.key);
          if (fresh) {
            state.inboxChat = fresh;
            renderInboxHeader();
          }
        }
        renderInboxAccounts();
        renderInbox();
      } catch (err) {
        if (!silent) listError("inboxRows", () => loadInbox());
      } finally {
        state.inboxLoading = false;
        if (state.inboxReloadAgain) {
          state.inboxReloadAgain = false;
          loadInbox({silent: true}).catch(() => {});
        }
      }
    }

    function renderInboxAccounts() {
      const select = $("inboxAccountFilter");
      const current = select.value;
      const options = [crmOption("", t("inbox.all_accounts"))];
      for (const account of (state.inbox && state.inbox.accounts) || []) {
        const kind = account.channel && account.channel !== "line" ? "Telegram" : "LINE";
        options.push(crmOption(account.id, `${account.label} · ${kind}`));
      }
      select.replaceChildren(...options);
      select.value = [...select.options].some((o) => o.value === current) ? current : "";
    }

    function renderInbox() {
      const root = $("inboxRows");
      const all = (state.inbox && state.inbox.chats) || [];
      const chats = all.filter(inboxMatches);
      root.replaceChildren(...chats.map(inboxRow));
      if (!chats.length) root.appendChild(textSpan(t(all.length ? "inbox.empty_filtered" : "inbox.empty"), "state-row"));
      const unreadBy = (pick) => all.filter((chat) => pick(chat) && inboxUnread(chat)).length;
      const show = (id, n) => { $(id).textContent = n ? String(n) : ""; };
      show("inboxCountLine", unreadBy((chat) => chat.channel === "line"));
      show("inboxCountTelegram", unreadBy((chat) => chat.channel === "telegram"));
      show("inboxCountGroup", unreadBy((chat) => chat.kind === "group"));
      document.querySelectorAll("[data-inbox-channel]").forEach((chip) => {
        chip.setAttribute("aria-pressed", String(chip.dataset.inboxChannel === state.inboxFilter.channel));
      });
      updateInboxBadge();
    }

    function updateInboxBadge() {
      const total = ((state.inbox && state.inbox.chats) || []).reduce((sum, chat) => sum + inboxUnread(chat), 0);
      const badge = $("navInboxBadge");
      badge.textContent = total > 99 ? "99+" : String(total);
      badge.classList.toggle("hidden", !total);
    }

    function inboxRow(chat) {
      const unread = inboxUnread(chat);
      const button = document.createElement("button");
      button.type = "button";
      button.className = "line-row inbox-row" + (unread ? " has-unread" : "")
        + (state.inboxChat && state.inboxChat.key === chat.key ? " selected" : "");
      button.dataset.key = chat.key;
      const name = inboxChatName(chat);

      const avatar = textSpan(crmInitial(name), "avatar inbox-avatar" + (chat.kind === "group" ? " group" : ""));
      avatar.setAttribute("aria-hidden", "true");
      avatar.appendChild(textSpan(chat.channel === "telegram" ? "T" : "L", "inbox-ch " + chat.channel));

      // Name and time, latest message and badges, then where the chat comes from.
      const source = document.createElement("span");
      source.className = "inbox-sub";
      source.append(inboxPill(chat));
      if (chat.kind === "group") source.append(textSpan(t("inbox.group"), "inbox-pill group"));
      for (const tag of (chat.tags || []).slice(0, 2)) source.append(crmTagChip(tag));

      const meta = document.createElement("span");
      meta.className = "crm-row-meta";
      if (chat.kind === "contact" && (chat.status === "new" || chat.status === "pending")) {
        const dot = document.createElement("span");
        dot.className = "crm-row-dot " + chat.status;
        dot.title = t("crm.status_" + chat.status);
        meta.appendChild(dot);
      }
      if (chat.assignedTo) {
        const who = crmMemberName(chat.assignedTo);
        const me = (state.inbox && state.inbox.me) || state.crmMe;
        const initial = textSpan(crmInitial(who), "crm-assignee" + (chat.assignedTo === me ? " me" : ""));
        initial.title = t("crm.handled_by", {name: who});
        meta.appendChild(initial);
      }
      if (unread) {
        const badge = textSpan(unread > 99 ? "99+" : String(unread), "line-unread-badge");
        badge.setAttribute("aria-label", t("contacts.unread", {n: unread}));
        meta.appendChild(badge);
      }
      button.append(
        avatar,
        textSpan(name, "line-row-name"),
        textSpan(fmtTime(chat.lastMessageAt), "line-row-time"),
        textSpan(inboxPreview(chat), "line-row-sub"),
        meta,
        source
      );
      button.addEventListener("click", () => openInboxChat(chat).catch(toastError));
      return button;
    }

    function renderInboxHeader() {
      const chat = state.inboxChat;
      const convo = $("inboxConversation");
      convo.classList.toggle("no-chat", !chat);
      if (!chat) {
        $("inboxTitle").textContent = t("inbox.pick");
        $("inboxSource").replaceChildren();
        $("inboxNotice").classList.add("hidden");
        $("inboxMessages").replaceChildren(textSpan(t("inbox.pick_hint"), "inbox-empty-thread"));
        return;
      }
      $("inboxTitle").textContent = inboxChatName(chat);
      const account = inboxAccount(chat.accountId);
      const offline = account && account.connected === false;
      const parts = [inboxPill(chat, offline ? "off" : "")];
      if (chat.kind === "group") parts.push(textSpan(t("inbox.group"), "inbox-pill group"));
      $("inboxSource").replaceChildren(...parts);
      const canSend = hasPermission("send") && !offline;
      for (const id of ["inboxText", "inboxSend", "inboxAttach", "inboxImage"]) $(id).disabled = !canSend;
      const notice = offline ? t("inbox.disconnected") : (!hasPermission("send") ? t("inbox.no_send") : "");
      $("inboxNotice").textContent = notice;
      $("inboxNotice").classList.toggle("hidden", !notice);
    }

    async function openInboxChat(chat) {
      state.inboxChat = chat;
      state.inboxSignature = "";
      document.querySelectorAll("#inboxRows .line-row").forEach((row) => {
        row.classList.toggle("selected", row.dataset.key === chat.key);
      });
      $("tabPanelInbox").classList.add("inbox-open");
      renderInboxHeader();
      if (window.matchMedia("(max-width: 980px)").matches) window.scrollTo(0, 0);
      else $("inboxText").focus();
      syncInboxSide();
      await loadInboxMessages({fresh: true});
    }

    async function loadInboxMessages({fresh = false, silent = false} = {}) {
      const chat = state.inboxChat;
      if (!chat) return;
      const key = chat.key;
      const seq = ++state.inboxMessageSeq;
      if (fresh) listLoading("inboxMessages");
      let data;
      try {
        data = await api(`/api/messages?${accountQuery(chat.accountId)}&chat_mid=${encodeURIComponent(chat.chatId)}&count=50`);
      } catch (err) {
        if (seq !== state.inboxMessageSeq || !state.inboxChat || state.inboxChat.key !== key) return;
        if (!silent) {
          listError("inboxMessages", () => loadInboxMessages({fresh: true}));
          // A disconnected account already has its notice above the thread.
          const account = inboxAccount(chat.accountId);
          if (!account || account.connected !== false) toastError(err);
        }
        return;
      }
      if (seq !== state.inboxMessageSeq || !state.inboxChat || state.inboxChat.key !== key) return;
      const messages = data.messages || [];
      renderInboxThread(messages, chat, {forceBottom: fresh});
      const last = messages.length ? messages[messages.length - 1] : null;
      if (last && last.id && inboxUnread(chat)) {
        state.inboxReadAt.set(chat.key, chat.lastMessageAt || Date.now());
        renderInbox();
        post("/api/messages/read", {accountId: chat.accountId, chatMid: chat.chatId, lastMessageId: last.id})
          .catch(() => { /* the read receipt is best effort */ });
      }
    }

    function renderInboxThread(messages, chat, {forceBottom = false} = {}) {
      const list = $("inboxMessages");
      const signature = chat.key + JSON.stringify(messages);
      if (!forceBottom && signature === state.inboxSignature) return;
      state.inboxSignature = signature;
      const previousTop = list.scrollTop;
      const bottomGap = Math.max(0, list.scrollHeight - list.clientHeight - list.scrollTop);
      const stayAtBottom = forceBottom || !list.children.length || bottomGap < 80;
      const account = inboxAccount(chat.accountId);
      const myMid = (account && account.mid)
        || (chat.accountId === selectedAccountId() && state.profile ? state.profile.mid : "");
      const line = (text, cls) => {
        const div = document.createElement("div");
        div.className = cls;
        div.textContent = text;
        return div;
      };
      const fragment = document.createDocumentFragment();
      for (const m of messages) {
        const bubble = document.createElement("div");
        const isMe = Boolean(myMid) && m.from === myMid;
        const type = Number(m.contentType);
        const visual = ([1, 2, 21].includes(type) && m.id && m.mediaReady) || (type === 7 && m.stickerId);
        bubble.className = "bubble" + (isMe ? " me" : "") + (visual ? " has-media" : "");
        const body = messageBody(m, chat.accountId, chat.chatId);
        if (isMe) {
          bubble.append(body);
        } else {
          bubble.append(line(m.fromName || inboxChatName(chat), "by"), body);
        }
        const at = fmtTime(m.createdTime);
        if (at) bubble.appendChild(line(at, "at"));
        fragment.appendChild(bubble);
      }
      if (!fragment.childNodes.length) fragment.appendChild(textSpan(t("chat.empty"), "state-row"));
      list.replaceChildren(fragment);
      list.scrollTop = stayAtBottom ? list.scrollHeight : previousTop;
    }

    async function sendInboxText() {
      const chat = state.inboxChat;
      const area = $("inboxText");
      const text = area.value;
      if (!chat || !text.trim() || state.inboxSending) return;
      state.inboxSending = true;
      $("inboxSend").disabled = true;
      try {
        await post("/api/send", {accountId: chat.accountId, to: chat.chatId, text});
        area.value = "";
        await loadInboxMessages({silent: true});
        scheduleInboxReload([]);
      } catch (err) {
        toastError(err);
      } finally {
        state.inboxSending = false;
        $("inboxSend").disabled = false;
        area.focus();
      }
    }

    async function sendInboxFile(file, kind) {
      const chat = state.inboxChat;
      if (!chat || !file || state.inboxSending) return;
      if (file.size > INBOX_MAX_UPLOAD) return toast(t("inbox.file_too_big"), true);
      state.inboxSending = true;
      toast(t("toast.sending_media"));
      try {
        const data = await fileToBase64(file);
        await post("/api/send-media", {accountId: chat.accountId, to: chat.chatId, filename: file.name, kind, data});
        toast(t("toast.sent"));
        await loadInboxMessages({silent: true});
        scheduleInboxReload([]);
      } catch (err) {
        toastError(err);
      } finally {
        state.inboxSending = false;
      }
    }

    function syncInboxSide() {
      $("tabPanelInbox").classList.toggle("inbox-side-open", inboxSideShown());
      $("inboxPanelToggle").setAttribute("aria-pressed", String(state.inboxSideOpen));
      $("inboxPanelToggle").classList.toggle("hidden", Boolean(state.inboxChat) && state.inboxChat.kind === "group");
      if (INBOX_PANEL.context().open) loadCrmPanel(INBOX_PANEL).catch(() => {});
    }

    function setInboxSideOpen(open) {
      state.inboxSideOpen = Boolean(open);
      try { localStorage.setItem(INBOX_SIDE_KEY, state.inboxSideOpen ? "on" : "off"); } catch (_) { /* optional */ }
      syncInboxSide();
    }

    // -- live updates for every account --------------------------------------
    // At most one reload per 1.5 s however busy the accounts are (the list has a
    // per-minute request limit); it covers every account that changed meanwhile.
    function scheduleInboxReload(accountIds) {
      if (!accountIds.length) state.inboxReloadAll = true;
      for (const id of accountIds) state.inboxReloadIds.add(id);
      if (state.inboxReloadTimer) return;
      state.inboxReloadTimer = setTimeout(() => {
        state.inboxReloadTimer = 0;
        const chat = state.inboxChat;
        const touched = state.inboxReloadAll || (chat && state.inboxReloadIds.has(chat.accountId));
        state.inboxReloadAll = false;
        state.inboxReloadIds = new Set();
        loadInbox({silent: true}).catch(() => {});
        if (touched && chat && state.tab === "inbox" && !state.inboxSending) {
          loadInboxMessages({silent: true}).catch(() => {});
        }
      }, 1500);
    }

    function startInboxStream() {
      if (state.inboxSource || typeof EventSource === "undefined" || !state.currentUser) return;
      const source = new EventSource("/api/events/inbox");
      state.inboxSource = source;
      source.addEventListener("hello", () => {
        state.inboxSseRetry = 0;
        // Catch up on whatever changed while the stream was down.
        if (state.inboxStreamedOnce) scheduleInboxReload([]);
        state.inboxStreamedOnce = true;
      });
      source.addEventListener("inbox_changed", (event) => {
        let ids = [];
        try { ids = JSON.parse(event.data).accountIds || []; } catch (_) { /* reload everything */ }
        scheduleInboxReload(ids);
      });
      source.onerror = () => {
        source.close();
        if (state.inboxSource !== source) return;
        state.inboxSource = null;
        if (!state.inboxStarted) return;
        const delay = SSE_BACKOFF_MS[Math.min(state.inboxSseRetry, SSE_BACKOFF_MS.length - 1)];
        state.inboxSseRetry += 1;
        setTimeout(startInboxStream, delay);
      };
    }

    function ensureInboxLive() {
      if (state.inboxStarted || !state.currentUser || !hasPermission("read")) return;
      state.inboxStarted = true;
      startInboxStream();
      loadInbox({silent: true}).catch(() => {});
    }

    function stopInbox() {
      state.inboxStarted = false;
      state.inboxStreamedOnce = false;
      clearTimeout(state.inboxReloadTimer);
      state.inboxReloadTimer = 0;
      state.inboxReloadAll = false;
      state.inboxReloadIds = new Set();
      if (state.inboxSource) state.inboxSource.close();
      state.inboxSource = null;
      state.inbox = null;
      state.inboxChat = null;
      state.inboxSignature = "";
      state.inboxReadAt = new Map();
      INBOX_PANEL.key = "";
      INBOX_PANEL.data = null;
      for (const id of ["inboxRows", "inboxMessages", "inboxSideBody"]) $(id).replaceChildren();
      $("tabPanelInbox").classList.remove("inbox-open", "inbox-side-open");
      renderInboxHeader();
      $("navInboxBadge").classList.add("hidden");
    }

    // -- hooks ------------------------------------------------------------------
    const baseSetTabForInbox = setTab;
    setTab = function (tab) {
      baseSetTabForInbox(tab);
      if (state.tab !== "inbox") return;
      ensureInboxLive();
      renderInboxHeader();
      syncInboxSide();
      loadInbox().catch(() => {});
      if (state.inboxChat) loadInboxMessages({silent: true}).catch(() => {});
    };
    const baseRenderProfileForInbox = renderProfile;
    renderProfile = function (status) {
      baseRenderProfileForInbox(status);
      ensureInboxLive();
    };
    const baseWebLogoutForInbox = webLogout;
    webLogout = async function () {
      stopInbox();
      return baseWebLogoutForInbox();
    };
    const baseApplyPermissionsForInbox = applyPermissions;
    applyPermissions = function () {
      baseApplyPermissionsForInbox();
      $("tabInbox").classList.toggle("hidden", !hasPermission("read"));
    };
    // With several accounts the account gate also offers All chats.
    const baseRenderGateAccountsForInbox = renderGateAccounts;
    renderGateAccounts = function () {
      baseRenderGateAccountsForInbox();
      if ((state.accounts || []).length < 2 || !hasPermission("read")) return;
      const button = document.createElement("button");
      button.type = "button";
      button.className = "gate-all-chats";
      const text = document.createElement("span");
      text.className = "gate-account-name";
      text.append(textSpan(t("tabs.inbox")), textSpan(t("inbox.subtitle"), "gate-all-chats-sub"));
      button.append($("tabInbox").querySelector("svg").cloneNode(true), text);
      button.addEventListener("click", openInboxFromGate);
      $("accountGateList").prepend(button);
    };

    // The workspace needs an active account: open the last used (or a connected)
    // one underneath, then show every account's chats.
    function openInboxFromGate() {
      const accounts = state.accounts || [];
      const connected = (account) => account.sessionState === "connected";
      const pick = accounts.find((item) => item.id === rememberedAccountId() && connected(item))
        || accounts.find(connected) || accounts[0];
      if (!pick) return;
      $("accountSelect").value = pick.id;
      const switching = selectAccount();
      setTab("inbox");
      switching.catch(toastError);
    }
    document.addEventListener("crm:changed", () => scheduleInboxReload([]));

    document.querySelectorAll("[data-inbox-channel]").forEach((chip) => {
      chip.addEventListener("click", () => {
        state.inboxFilter.channel = chip.dataset.inboxChannel;
        renderInbox();
      });
    });
    $("inboxAccountFilter").addEventListener("change", () => {
      state.inboxFilter.account = $("inboxAccountFilter").value;
      renderInbox();
    });
    $("inboxStatusFilter").addEventListener("change", () => {
      state.inboxFilter.status = $("inboxStatusFilter").value;
      renderInbox();
    });
    $("inboxSearch").addEventListener("input", () => {
      clearTimeout(state.inboxSearchTimer);
      state.inboxSearchTimer = setTimeout(() => {
        state.inboxFilter.q = $("inboxSearch").value.trim();
        renderInbox();
      }, 150);
    });
    $("inboxRefresh").addEventListener("click", () => loadInbox().catch(toastError));
    $("inboxReload").addEventListener("click", () => loadInboxMessages({fresh: true}).catch(toastError));
    $("inboxBack").addEventListener("click", () => $("tabPanelInbox").classList.remove("inbox-open"));
    $("inboxPanelToggle").addEventListener("click", () => setInboxSideOpen(!state.inboxSideOpen));
    $("inboxSideClose").addEventListener("click", () => setInboxSideOpen(false));
    $("inboxOpenInChat").addEventListener("click", () => {
      const chat = state.inboxChat;
      if (chat) openCrmChat(chat.accountId, chat.chatId, inboxChatName(chat)).catch(toastError);
    });
    $("inboxSend").addEventListener("click", () => sendInboxText().catch(toastError));
    $("inboxText").addEventListener("keydown", (event) => {
      if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
        event.preventDefault();
        sendInboxText().catch(toastError);
      }
    });
    $("inboxAttach").addEventListener("click", () => $("inboxFileInput").click());
    $("inboxImage").addEventListener("click", () => $("inboxImageInput").click());
    $("inboxFileInput").addEventListener("change", () => {
      const file = $("inboxFileInput").files[0];
      $("inboxFileInput").value = "";
      sendInboxFile(file, "file").catch(toastError);
    });
    $("inboxImageInput").addEventListener("change", () => {
      const file = $("inboxImageInput").files[0];
      $("inboxImageInput").value = "";
      sendInboxFile(file, "image").catch(toastError);
    });
    setInterval(() => {
      if (state.inboxStarted && !state.inboxSource) loadInbox({silent: true}).catch(() => {});
    }, 30000);
