    // ---- Chat ring: a soft beep while a customer waits ----------------------
    // All chats keeps every account live, so the alert sound starts here: the
    // soft double beep when a customer writes, then again every few seconds until
    // someone opens or answers the chat. Settings → Notifications can turn the
    // repeat off. Groups beep once when they start waiting and never repeat.
    const RING_EVERY_MS = 4000;
    // A chat the AI may answer beeps once, then waits this long before repeating.
    const RING_AI_WAIT_MS = 20000;
    const RING_LAST_BEEP_KEY = "okline.ringLastBeepAt";
    Object.assign(I18N, {
      "ring.waiting": {th: "ลูกค้ารอตอบ {n} แชท", en: "Customers waiting: {n}"},
      "ring.open": {th: "เปิดแชท", en: "Open chat"},
      "ring.stop": {th: "หยุดเสียง", en: "Stop sound"},
    });
    Object.assign(state, {
      ringChats: new Map(),  // chat key -> when it started ringing, oldest first
      ringTimer: 0,
    });

    function ringInboxChat(key) {
      return ((state.inbox && state.inbox.chats) || []).find((chat) => chat.key === key) || null;
    }

    function ringChatWaiting(chat) {
      return !!chat && !chat.lastFromMe && inboxUnread(chat) > 0;
    }

    // Open in All chats or the Chat tab, with the page in front.
    function ringChatOnScreen(chat) {
      if (!pageIsInFront()) return false;
      if (state.tab === "inbox") return !!state.inboxChat && state.inboxChat.key === chat.key;
      return state.tab === "line" && selectedAccountId() === chat.accountId && state.target === chat.chatId;
    }

    function ringAiMayAnswer(chat) {
      return chat.accountId === selectedAccountId() && autoReplyCovers(chat.chatId);
    }

    function ringRepeats() {
      try {
        return localStorage.getItem(NOTIFY_REPEAT_KEY) !== "off";
      } catch (_) {
        return true;
      }
    }

    // New customer messages since the last load start the ring. The first load
    // only sets the baseline: chats already waiting when the page opened stay quiet.
    function noteInboxForRing(before, after) {
      if (!before || !after) return;
      const previous = new Map((before.chats || []).map((chat) => [chat.key, chat]));
      const now = Date.now();
      let customer = false;
      let group = false;
      for (const chat of after.chats || []) {
        if (!ringChatWaiting(chat) || ringChatOnScreen(chat)) continue;
        const prev = previous.get(chat.key);
        const startedWaiting = !ringChatWaiting(prev);
        if (!startedWaiting && chat.lastMessageAt <= prev.lastMessageAt) continue;
        if (chat.kind === "group") {
          group = group || startedWaiting;
        } else {
          if (!state.ringChats.has(chat.key)) state.ringChats.set(chat.key, now);
          customer = true;
        }
      }
      if (customer) ringTick({fresh: true});
      else if (group && state.notificationsEnabled && insideWorkingTime()) ringBeep();
    }

    function ringTick({fresh = false} = {}) {
      clearTimeout(state.ringTimer);
      state.ringTimer = 0;
      const now = Date.now();
      let due = fresh;
      for (const [key, since] of [...state.ringChats]) {
        const chat = ringInboxChat(key);
        if (!ringChatWaiting(chat) || ringChatOnScreen(chat)) state.ringChats.delete(key);
        else if (!ringAiMayAnswer(chat) || now - since >= RING_AI_WAIT_MS) due = true;
      }
      if (!state.ringChats.size || !state.notificationsEnabled || !insideWorkingTime()) {
        quietRing();
        return;
      }
      if (due) ringBeep();
      if (!ringRepeats()) {
        quietRing();
        return;
      }
      renderRingBar();
      state.ringTimer = setTimeout(() => ringTick(), RING_EVERY_MS);
    }

    // No two beeps within two seconds: none right after another alert of this tab
    // (open chat, reminder), and none over a beep of another LinePassport tab.
    function ringBeep() {
      const now = Date.now();
      const button = $("notificationButton");
      let last = Number(button && button.dataset.soundPlayedAt) || 0;
      try {
        last = Math.max(last, Number(localStorage.getItem(RING_LAST_BEEP_KEY)) || 0);
        if (now - last >= RING_EVERY_MS / 2) localStorage.setItem(RING_LAST_BEEP_KEY, String(now));
      } catch (_) { /* no storage: this tab only */ }
      if (now - last < RING_EVERY_MS / 2) return;
      playNotificationSound().catch(() => {});
    }

    function quietRing() {
      clearTimeout(state.ringTimer);
      state.ringTimer = 0;
      state.ringChats.clear();
      renderRingBar();
    }

    const ringBar = document.createElement("div");
    ringBar.id = "chatRingBar";
    ringBar.className = "chat-ring-bar hidden";
    ringBar.setAttribute("role", "status");
    const ringDot = textSpan("", "chat-ring-dot");
    ringDot.setAttribute("aria-hidden", "true");
    const ringText = textSpan("", "chat-ring-text");
    const ringOpen = document.createElement("button");
    ringOpen.type = "button";
    ringOpen.className = "primary";
    const ringStop = document.createElement("button");
    ringStop.type = "button";
    ringBar.append(ringDot, ringText, ringOpen, ringStop);
    document.body.appendChild(ringBar);

    function renderRingBar() {
      const count = state.ringChats.size;
      ringBar.classList.toggle("hidden", !count);
      if (!count) return;
      // Only real changes, so screen readers don't repeat it on every beep.
      const texts = [[ringText, t("ring.waiting", {n: count})], [ringOpen, t("ring.open")], [ringStop, t("ring.stop")]];
      for (const [element, text] of texts) {
        if (element.textContent !== text) element.textContent = text;
      }
    }

    // Opens the chat that has waited longest; the others keep ringing.
    function openRingingChat() {
      const [key] = state.ringChats.keys();
      const chat = key ? ringInboxChat(key) : null;
      state.ringChats.delete(key);
      if (state.ringChats.size) renderRingBar();
      else quietRing();
      if (!chat) return;
      setTab("inbox");
      openInboxChat(chat).catch(toastError);
    }

    ringOpen.addEventListener("click", openRingingChat);
    ringStop.addEventListener("click", () => quietRing());

    const baseLoadInboxForRing = loadInbox;
    loadInbox = async function (options) {
      const before = state.inbox;
      await baseLoadInboxForRing(options);
      if (state.inbox !== before) noteInboxForRing(before, state.inbox);
    };
    const baseStopInboxForRing = stopInbox;
    stopInbox = function () {
      quietRing();
      baseStopInboxForRing();
    };
