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
      "errors.telegram_reauth": {th: "Telegram ไม่รับการเชื่อมต่อของบัญชีนี้แล้ว เชื่อมต่อใหม่ได้ที่ ตั้งค่า → จัดการบัญชี", en: "Telegram no longer accepts this account. Reconnect it in Settings → Account Management."},
      "errors.telegram_bot_mismatch": {th: "token นี้เป็นของบอทตัวอื่น", en: "This token belongs to a different bot."},
      "errors.telegram_unavailable": {th: "ติดต่อ Telegram ไม่ได้ชั่วคราว ลองใหม่อีกครั้ง", en: "Could not reach Telegram. Please try again."},
      "tg.accounts_title": {th: "บัญชี Telegram", en: "Telegram accounts"},
      "tg.accounts_subtitle": {th: "Bot ใช้ token จาก @BotFather ส่วนบัญชีส่วนตัวเข้าสู่ระบบด้วยเบอร์โทร", en: "A bot uses a token from @BotFather; a personal account signs in with its phone number."},
      "tg.accounts_none": {th: "ยังไม่มีบัญชี Telegram", en: "No Telegram accounts yet"},
      "tg.state_connected": {th: "เชื่อมต่อแล้ว", en: "Connected"},
      "tg.state_token_rejected": {th: "Telegram ไม่รับ token แล้ว", en: "Telegram rejected the token"},
      "tg.state_signed_out": {th: "ออกจากระบบแล้ว ต้องเข้าสู่ระบบใหม่", en: "Signed out; sign in again"},
      "tg.state_missing": {th: "ข้อมูลเชื่อมต่อหาย", en: "Connection details missing"},
      "tg.reconnect_button": {th: "ใส่ token ใหม่", en: "Paste a new token"},
      "tg.banner_title": {th: "บัญชี Telegram นี้หลุดการเชื่อมต่อ", en: "This Telegram account is disconnected"},
      "tg.banner_body": {th: "อ่านและส่งข้อความไม่ได้ จนกว่าจะใส่ token ใหม่ของบอท", en: "Messages can't be read or sent until you paste the bot's new token."},
      "tg.ask_owner": {th: "บัญชี Telegram นี้หลุดการเชื่อมต่อ แจ้งเจ้าของร้านให้เชื่อมต่อใหม่", en: "This Telegram account is disconnected. Ask the owner to reconnect it."},
      "tg.rename": {th: "เปลี่ยนชื่อ", en: "Rename"},
      "tg.delete_note": {th: "ระบบจะหยุดรับและส่งข้อความผ่านบัญชีนี้ และลบประวัติแชทที่เก็บในระบบนี้ (ถ้าเป็น Bot จะดูย้อนหลังที่อื่นไม่ได้) ตัวบอทหรือบัญชีใน Telegram ไม่ถูกลบ", en: "Messages stop flowing through this account and the chat history kept here is deleted (for a bot there is nowhere else to read it). The bot or account itself stays on Telegram."}
    });

    // Only the shop owner can add Telegram accounts (staff with account rights cannot).
    function tgCanAdd() {
      return hasPermission("manage_accounts") && !(state.currentUser && state.currentUser.teamOwnerId);
    }

    function tgAccount(id) {
      return (state.accounts || []).find((item) => item.id === id) || null;
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
        toast(t("tg.ask_owner"), true);
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

    ACCOUNT_ADD_ACTIONS.push({
      channel: "telegram",
      labelKey: "tg.add_bot_menu",
      visible: tgCanAdd,
      run: () => addTelegramBot().catch(toastError)
    });

    const baseReconnectKeysForTg = reconnectKeys;
    reconnectKeys = function (account) {
      if (!account || accountIsLine(account)) return baseReconnectKeysForTg(account);
      const personal = account.channel === "telegram_user";
      return {
        button: personal ? "tgu.reconnect_button" : "tg.reconnect_button",
        title: "tg.banner_title",
        body: personal ? "tgu.banner_body" : "tg.banner_body",
        ask: "tg.ask_owner"
      };
    };

    // Settings → accounts: Telegram accounts have their own section and actions.
    function telegramAccountState(account) {
      if (account.sessionState === "reauth_required") {
        return t(account.channel === "telegram_user" ? "tg.state_signed_out" : "tg.state_token_rejected");
      }
      return t(account.tokenFileExists ? "tg.state_connected" : "tg.state_missing");
    }

    function renderTelegramAccountList(accounts) {
      const list = $("telegramAccountList");
      if (!list) return;
      list.replaceChildren();
      const canManage = hasPermission("manage_accounts");
      for (const account of (accounts || []).filter((item) => !accountIsLine(item))) {
        const personal = account.channel === "telegram_user";
        const row = document.createElement("div");
        row.className = "management-row";
        const info = document.createElement("div");
        const title = document.createElement("strong");
        title.textContent = account.label || account.id;
        const kind = [accountKindLabel(account), account.userid].filter(Boolean).join(" · ");
        info.append(title, textSpan(kind, "mono"), textSpan(telegramAccountState(account), "muted"));

        const lost = account.sessionState === "reauth_required" || !account.tokenFileExists;
        const actions = document.createElement("div");
        actions.className = "management-row-actions";
        const use = document.createElement("button");
        use.textContent = t("accounts.use");
        use.disabled = lost;
        use.addEventListener("click", async () => {
          state.activeAccountId = account.id;
          renderAccounts();
          await selectAccount().catch(toastError);
        });
        actions.append(use);
        if (lost) {
          const fix = document.createElement("button");
          fix.className = "primary";
          fix.textContent = t(personal ? "tgu.reconnect_button" : "tg.reconnect_button");
          fix.disabled = !canManage;
          fix.addEventListener("click", () => openReconnect(account));
          actions.append(fix);
        }
        const rename = document.createElement("button");
        rename.textContent = t("tg.rename");
        rename.disabled = !canManage;
        rename.addEventListener("click", () => renameTelegramAccount(account).catch(toastError));
        const del = document.createElement("button");
        del.className = "danger";
        del.textContent = t("common.delete");
        del.disabled = !canManage;
        del.addEventListener("click", () => deleteTelegramAccount(account).catch(toastError));
        actions.append(rename, del);
        row.append(info, actions);
        list.appendChild(row);
      }
      if (!list.children.length) list.appendChild(textSpan(t("tg.accounts_none"), "muted"));
    }

    async function renameTelegramAccount(account) {
      const label = await openModal({
        title: t("accounts.edit_title"),
        build(body) {
          const {label: field, input} = labeledInput(t("accounts.name"), "text");
          input.maxLength = 60;
          input.value = account.label || account.id;
          body.appendChild(field);
          return () => input.value.trim();
        },
        confirmKey: "common.save"
      });
      if (!label) return;
      await post("/api/accounts/update", {accountId: account.id, label});
      toast(t("toast.account_updated"));
      await refreshStatus(false);
      await loadAccountManagement();
    }

    async function deleteTelegramAccount(account) {
      const confirmed = await openModal({
        title: t("accounts.delete_title", {name: account.label || account.id}),
        build(body) {
          body.appendChild(textSpan(t("tg.delete_note"), "muted"));
          return () => true;
        },
        confirmKey: "accounts.delete",
        danger: true
      });
      if (!confirmed) return;
      await post("/api/accounts/delete", {accountId: account.id});
      if (state.activeAccountId === account.id) state.activeAccountId = null;
      toast(t("toast.account_deleted"));
      await refreshStatus(false);
      await loadAccountManagement();
    }

    const baseRenderLineAccountListForTg = renderLineAccountList;
    renderLineAccountList = function (accounts) {
      baseRenderLineAccountListForTg(accounts);
      renderTelegramAccountList(accounts);
      $("telegramAccountActions").querySelectorAll("button").forEach((button) => {
        button.classList.toggle("hidden", !tgCanAdd());
      });
    };

    const LINE_ONLY_CONTROLS = [
      "convoMenuEncrypt", "convoMenuSchedule", "sendEncryptedButton", "loadGroupsButton",
      "tabBot", "tabIncomingApi", "tabTools", "assistantAutoReplyGroupsRow", "assistantAutoReplyGroupConfig"
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
      const actions = $("telegramAccountActions");
      if (actions) {
        const button = document.createElement("button");
        button.type = "button";
        button.id = "addTelegramAccountButton";
        button.className = "primary";
        button.dataset.i18n = "tg.add_bot_menu";
        button.dataset.requiresPermission = "manage_accounts";
        button.textContent = t("tg.add_bot_menu");
        button.addEventListener("click", () => addTelegramBot().catch(toastError));
        actions.appendChild(button);
      }
    })();
