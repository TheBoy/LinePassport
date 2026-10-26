    // ---- Telegram: bots as customer chat accounts ------------------------
    Object.assign(I18N, {
      "tg.add_bot_menu": {th: "+ เพิ่ม Telegram Bot", en: "+ Add a Telegram bot"},
      "tg.add_bot_title": {th: "เพิ่ม Telegram Bot สำหรับคุยกับลูกค้า", en: "Add a Telegram bot for customers"},
      "tg.add_bot_intro": {th: "ลูกค้าทักบอทใน Telegram แล้วข้อความจะเข้ากล่องแชทเดียวกับ LINE ใช้บอทคนละตัวกับบอทแจ้งเตือนพนักงาน", en: "Customers message the bot in Telegram and the chats land in the same inbox as LINE. Use a different bot from the staff alert bot."},
      "tg.add_bot_label": {th: "ชื่อที่จะแสดงในระบบ (ไม่บังคับ)", en: "Name shown here (optional)"},
      "tg.add_bot_confirm": {th: "เชื่อมบอท", en: "Connect bot"},
      "tg.add_bot_done": {th: "เพิ่มบัญชี Telegram \"{name}\" แล้ว แจกลิงก์ t.me ให้ลูกค้ากด Start ได้เลย", en: "Added \"{name}\". Share its t.me link so customers can press Start."},
      "tg.add_instead": {th: "หรือเพิ่ม Telegram Bot แทน", en: "Or add a Telegram bot instead"},
      "tg.reconnect_title": {th: "วาง token ใหม่ของ {name}", en: "Paste a new token for {name}"},
      "tg.reconnect_hint": {th: "ใน @BotFather พิมพ์ /token แล้วเลือกบอทนี้ เพื่อรับ token ใหม่", en: "In @BotFather send /token and pick this bot to get a new token."},
      "tg.reconnected": {th: "เชื่อมบอทใหม่แล้ว", en: "Bot reconnected"},
      "errors.line_only": {th: "ฟีเจอร์นี้ใช้ได้กับบัญชี LINE เท่านั้น", en: "This works with LINE accounts only."},
      "errors.telegram_bot_exists": {th: "บอทนี้เชื่อมไว้แล้ว", en: "This bot is already connected."},
      "errors.telegram_bot_in_use": {th: "บอทนี้ใช้ส่งแจ้งเตือนพนักงานอยู่ สร้างบอทอีกตัวสำหรับลูกค้า", en: "This bot sends staff alerts. Create a separate bot for customers."},
      "errors.telegram_blocked": {th: "ลูกค้าคนนี้บล็อกบอท หรือยังไม่ได้กด Start", en: "This customer blocked the bot or never pressed Start."},
      "errors.telegram_not_started": {th: "คนนี้ยังไม่เคยทักบอท บอทจึงส่งหาเขาก่อนไม่ได้", en: "This person has not messaged the bot yet, so the bot cannot write first."},
      "errors.telegram_reauth": {th: "Telegram ไม่รับ token ของบอทแล้ว วาง token ใหม่", en: "Telegram rejected the bot token. Paste a new one."},
      "errors.telegram_bot_mismatch": {th: "token นี้เป็นของบอทตัวอื่น", en: "This token belongs to a different bot."}
    });

    function tgAccount(id) {
      return (state.accounts || []).find((item) => item.id === id) || null;
    }

    function isTelegramAccount(id) {
      const account = tgAccount(id);
      return Boolean(account && account.channel && account.channel !== "line");
    }

    function tgTokenField(body, labelKey) {
      const label = document.createElement("label");
      label.append(textSpan(t(labelKey)));
      const input = document.createElement("input");
      input.type = "password";
      input.autocomplete = "off";
      input.spellcheck = false;
      input.placeholder = "123456789:AA…";
      label.appendChild(input);
      body.appendChild(label);
      return input;
    }

    async function addTelegramBot() {
      const values = await openModal({
        title: t("tg.add_bot_title"),
        build(body) {
          body.appendChild(textSpan(t("tg.add_bot_intro"), "ux-muted"));
          const steps = document.createElement("ol");
          steps.className = "tg-howto";
          for (const key of ["tg.bot_step1", "tg.bot_step2", "tg.bot_step3"]) {
            const item = document.createElement("li");
            item.textContent = t(key);
            steps.appendChild(item);
          }
          body.appendChild(steps);
          const {label, input: name} = labeledInput(t("tg.add_bot_label"), "text");
          name.maxLength = 60;
          body.appendChild(label);
          const token = tgTokenField(body, "tg.bot_token");
          return () => (token.value.trim() ? {token: token.value.trim(), label: name.value.trim()} : null);
        },
        confirmKey: "tg.add_bot_confirm"
      });
      if (!values) return;
      const data = await post("/api/telegram/accounts/create", values);
      state.accounts = (data.accounts && data.accounts.accounts) || state.accounts;
      renderAccounts();
      $("accountSelect").value = data.account.id;
      toast(t("tg.add_bot_done", {name: data.account.label}));
      await selectAccount();
    }

    async function reconnectTelegramBot(account) {
      if (!hasPermission("manage_accounts")) {
        toast(t("reconnect.ask_owner"), true);
        return;
      }
      const token = await openModal({
        title: t("tg.reconnect_title", {name: account.label || account.id}),
        build(body) {
          body.appendChild(textSpan(t("tg.reconnect_hint"), "ux-muted"));
          const input = tgTokenField(body, "tg.bot_token");
          return () => input.value.trim();
        },
        confirmKey: "common.save"
      });
      if (!token) return;
      const data = await post("/api/telegram/accounts/token", {accountId: account.id, token});
      renderProfile(data.status);
      toast(t("tg.reconnected"));
    }

    // Telegram accounts never go through the LINE QR.
    const baseOpenReconnectForTg = openReconnect;
    openReconnect = function (account) {
      const accountId = account ? account.id : (state.activeAccountId || selectedAccountId());
      const target = account || tgAccount(accountId);
      if (target && target.channel && target.channel !== "line") {
        reconnectTelegramBot(target).catch(toastError);
        return;
      }
      baseOpenReconnectForTg(account);
    };

    const baseRenderAccountPopoverForTg = renderAccountPopover;
    renderAccountPopover = function () {
      baseRenderAccountPopoverForTg();
      const pop = $("accountPopover");
      pop.querySelectorAll("[role=option]").forEach((button, index) => {
        const account = (state.accounts || [])[index];
        if (account && account.channel && account.channel !== "line") {
          button.appendChild(textSpan("Telegram", "badge telegram"));
        }
      });
      const owner = hasPermission("manage_accounts") && !(state.currentUser && state.currentUser.teamOwnerId);
      if (owner) {
        const add = document.createElement("button");
        add.type = "button";
        add.className = "add-account";
        add.textContent = t("tg.add_bot_menu");
        add.addEventListener("click", () => {
          closeAccountPopover();
          addTelegramBot().catch(toastError);
        });
        pop.appendChild(add);
      }
    };

    const baseRenderGateAccountsForTg = renderGateAccounts;
    renderGateAccounts = function () {
      baseRenderGateAccountsForTg();
      $("accountGateList").querySelectorAll("[data-account-id]").forEach((button) => {
        if (isTelegramAccount(button.dataset.accountId)) {
          button.insertBefore(textSpan("Telegram", "badge telegram"), button.querySelector(".status-dot"));
        }
      });
    };

    const baseRenderAccountCardForTg = renderAccountCard;
    renderAccountCard = function () {
      baseRenderAccountCardForTg();
      const accountId = state.activeAccountId || selectedAccountId();
      if (accountId && isTelegramAccount(accountId)) {
        $("accountCardStatus").textContent = "Telegram · " + $("accountCardStatus").textContent;
      }
    };

    const LINE_ONLY_CONTROLS = [
      "convoMenuEncrypt", "convoMenuSchedule", "sendEncryptedButton", "loadGroupsButton",
      "tabBot", "tabIncomingApi", "tabTools"
    ];
    const baseRenderProfileForTg = renderProfile;
    renderProfile = function (status) {
      baseRenderProfileForTg(status);
      const telegram = Boolean(status && status.channel && status.channel !== "line");
      document.body.classList.toggle("account-telegram", telegram);
      for (const id of LINE_ONLY_CONTROLS) {
        const el = $(id);
        if (el) el.classList.toggle("tg-hidden", telegram);
      }
      if (telegram && ["bot", "incoming-api", "tools"].includes(state.tab)) setTab("line");
      if (telegram && state.contactsSubtab === "groups") setContactsSubtab("all");
    };

    (function addTelegramEntryPoints() {
      const step = document.querySelector("[data-login-step=\"1\"] .wizard-center-inner");
      if (step) {
        const row = document.createElement("div");
        row.className = "ux-row tg-login-alt";
        const button = document.createElement("button");
        button.type = "button";
        button.className = "crm-link";
        button.dataset.i18n = "tg.add_instead";
        button.textContent = t("tg.add_instead");
        button.addEventListener("click", () => addTelegramBot().catch(toastError));
        row.appendChild(button);
        step.appendChild(row);
      }
      const addButton = $("addAccountButton");
      if (addButton) {
        const button = document.createElement("button");
        button.type = "button";
        button.id = "addTelegramAccountButton";
        button.dataset.i18n = "tg.add_bot_menu";
        button.dataset.requiresPermission = "manage_accounts";
        button.textContent = t("tg.add_bot_menu");
        button.addEventListener("click", () => addTelegramBot().catch(toastError));
        addButton.insertAdjacentElement("afterend", button);
      }
    })();
