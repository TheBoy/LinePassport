    // ---- LINE OA: Official Accounts through OA Manager ------------------
    Object.assign(I18N, {
      "channel.line_oa": {th: "LINE OA", en: "LINE OA"},
      "oa.add_menu": {th: "+ เพิ่ม LINE OA", en: "+ Add a LINE OA"},
      "oa.accounts_title": {th: "บัญชี LINE OA", en: "LINE OA accounts"},
      "oa.accounts_subtitle": {th: "เชื่อมผ่าน LINE Official Account Manager โดยสแกน QR ด้วยบัญชี LINE ที่เป็นสมาชิกของ OA", en: "Connected through LINE Official Account Manager by scanning a QR code with a LINE account that is a member of the OA."},
      "oa.accounts_none": {th: "ยังไม่มีบัญชี LINE OA", en: "No LINE OA accounts yet"},
      "oa.add_title": {th: "เพิ่ม LINE OA", en: "Add a LINE OA"},
      "oa.reconnect_title": {th: "เชื่อม LINE OA \"{name}\" ใหม่", en: "Reconnect LINE OA \"{name}\""},
      "oa.step1": {th: "ใน OA Manager ไปที่ ตั้งค่า → จัดการสิทธิ์ → เพิ่มสมาชิก แล้วรับคำเชิญด้วยบัญชี LINE ที่จะใช้ (ข้ามได้ถ้าเป็นสมาชิกอยู่แล้ว)", en: "In OA Manager go to Settings → Manage permissions → Add member, and accept the invitation with the LINE account you'll use (skip this if it's a member already)."},
      "oa.step2": {th: "สแกน QR ด้านล่างด้วยแอป LINE ของบัญชีนั้น", en: "Scan the QR code below with LINE on that account's phone."},
      "oa.step3": {th: "ถ้ามีรหัส 4 หลักขึ้น ให้กรอกรหัสนั้นในแอป LINE บนมือถือ", en: "If a 4-digit code appears, enter it in LINE on the phone."},
      "oa.unofficial": {th: "ทำงานแบบเดียวกับหน้าเว็บ OA Manager ซึ่ง LINE ไม่ได้เปิดให้ใช้อย่างเป็นทางการ ถ้า LINE เปลี่ยนระบบอาจใช้ไม่ได้ชั่วคราว", en: "This works the way the OA Manager web page does, which LINE does not officially offer, so it may stop working for a while if LINE changes it."},
      "oa.start": {th: "แสดง QR", en: "Show the QR code"},
      "oa.preparing": {th: "กำลังเตรียม QR…", en: "Preparing the QR code…"},
      "oa.scan": {th: "สแกน QR นี้ด้วยแอป LINE", en: "Scan this QR code with LINE"},
      "oa.pin": {th: "กรอกรหัสนี้ในแอป LINE บนมือถือ", en: "Enter this code in LINE on your phone"},
      "oa.finishing": {th: "กำลังเข้า OA Manager…", en: "Opening OA Manager…"},
      "oa.pick": {th: "เลือก LINE OA ที่จะเพิ่ม", en: "Choose the LINE OAs to add"},
      "oa.pick_connected": {th: "เชื่อมแล้ว", en: "Already connected"},
      "oa.pick_chat_off": {th: "ปิดโหมดแชทอยู่", en: "Chat is off"},
      "oa.chat_off_hint": {th: "OA ที่ปิดโหมดแชทจะยังไม่มีแชทเข้ามา จนกว่าจะเปิดใน OA Manager → ตั้งค่า → การตอบกลับ → แชท", en: "An OA with chat turned off gets no chats here until you turn chat on in OA Manager → Settings → Response settings → Chat."},
      "oa.state_chat_off": {th: "ปิดโหมดแชทใน OA Manager อยู่ เปิดที่ ตั้งค่า → การตอบกลับ → แชท", en: "Chat is off in OA Manager; turn it on in Settings → Response settings → Chat"},
      "oa.add_selected": {th: "เพิ่มที่เลือก", en: "Add selected"},
      "oa.pick_none": {th: "เลือก LINE OA อย่างน้อย 1 บัญชี", en: "Choose at least one LINE OA."},
      "oa.added": {th: "เพิ่ม LINE OA \"{name}\" แล้ว", en: "Added LINE OA \"{name}\""},
      "oa.reconnected": {th: "เชื่อม LINE OA ใหม่แล้ว", en: "LINE OA reconnected"},
      "oa.state_connected": {th: "เชื่อมต่อแล้ว", en: "Connected"},
      "oa.state_signed_out": {th: "OA Manager ให้ออกจากระบบแล้ว ต้องสแกน QR ใหม่", en: "Signed out of OA Manager; scan the QR code again"},
      "oa.state_missing": {th: "ข้อมูลเชื่อมต่อหาย", en: "Connection details missing"},
      "oa.reconnect_button": {th: "สแกน QR ใหม่", en: "Scan the QR again"},
      "oa.banner_title": {th: "LINE OA นี้หลุดการเชื่อมต่อ", en: "This LINE OA is disconnected"},
      "oa.banner_body": {th: "อ่านและส่งข้อความไม่ได้ จนกว่าจะสแกน QR เข้า OA Manager ใหม่", en: "Messages can't be read or sent until you scan the QR code for OA Manager again."},
      "oa.ask_owner": {th: "LINE OA นี้หลุดการเชื่อมต่อ แจ้งเจ้าของร้านให้เชื่อมต่อใหม่", en: "This LINE OA is disconnected. Ask the owner to reconnect it."},
      "oa.delete_note": {th: "ระบบจะหยุดรับและส่งข้อความผ่าน OA นี้ และลบประวัติแชทที่เก็บในระบบนี้ ตัว OA และแชทใน OA Manager ไม่ถูกลบ", en: "Messages stop flowing through this OA and the chat history kept here is deleted. The OA and its chats in OA Manager stay."},
      "errors.line_oa_reauth": {th: "OA Manager ให้ LINE OA นี้ออกจากระบบแล้ว สแกน QR ใหม่ได้ที่ ตั้งค่า → จัดการบัญชี", en: "OA Manager signed this LINE OA out. Scan the QR code again in Settings → Account Management."},
      "errors.line_oa_unavailable": {th: "ติดต่อ LINE OA Manager ไม่ได้ชั่วคราว ลองใหม่อีกครั้ง", en: "Could not reach LINE OA Manager. Please try again."},
      "errors.line_oa_send_failed": {th: "LINE OA Manager ไม่รับข้อความนี้", en: "LINE OA Manager did not accept this message."},
      "errors.line_oa_chat_invalid": {th: "เลือกแชทของ LINE OA ก่อน", en: "Pick a LINE OA chat first."},
      "errors.line_oa_exists": {th: "LINE OA นี้เชื่อมไว้แล้ว", en: "This LINE OA is already connected."},
      "errors.line_oa_none": {th: "บัญชี LINE นี้ยังไม่ได้เป็นสมาชิกของ LINE OA ใดเลย", en: "This LINE account is not a member of any LINE OA."},
      "errors.line_oa_mismatch": {th: "บัญชี LINE ที่สแกนไม่ได้ดูแล LINE OA นี้", en: "The LINE account you scanned with does not manage this LINE OA."},
      "errors.line_oa_login_expired": {th: "หมดเวลาแล้ว สแกน QR ใหม่อีกครั้ง", en: "That took too long. Scan the QR code again."},
      "errors.line_oa_pick": {th: "เลือก LINE OA อย่างน้อย 1 บัญชี", en: "Choose at least one LINE OA."},
      "errors.line_oa_qr_expired": {th: "QR หมดเวลาแล้ว ลองใหม่อีกครั้ง", en: "The QR code expired. Please try again."},
      "errors.line_oa_pin_expired": {th: "ไม่ได้กรอกรหัสในมือถือทันเวลา ลองใหม่อีกครั้ง", en: "The code was not entered on the phone in time. Please try again."},
      "errors.line_oa_no_business_id": {th: "บัญชี LINE นี้ยังไม่มี LINE Business ID จึงยังไม่ได้เป็นสมาชิกของ OA ใด ใน OA Manager ไปที่ ตั้งค่า → จัดการสิทธิ์ → เพิ่มสมาชิก แล้วรับคำเชิญด้วยบัญชี LINE นี้ก่อน จากนั้นสแกนใหม่", en: "This LINE account has no LINE Business ID, so it is not a member of any OA yet. In OA Manager go to Settings → Manage permissions → Add member, accept the invitation with this LINE account, then scan again."},
      "errors.line_oa_consent_needed": {th: "LINE ขอให้บัญชีนี้ยินยอมใช้ LINE Business ID ก่อน เข้า OA Manager ด้วยบัญชี LINE นี้ในเบราว์เซอร์หนึ่งครั้ง แล้วลองใหม่", en: "LINE asks this account to agree to LINE Business ID first. Sign in to OA Manager with it once in a browser, then try again."},
      "errors.line_oa_network": {th: "ติดต่อ LINE ไม่ได้ ลองใหม่อีกครั้ง", en: "Could not reach LINE. Please try again."},
      "errors.line_oa_login_failed": {th: "เข้าสู่ระบบ OA Manager ไม่สำเร็จ ลองใหม่อีกครั้ง", en: "Could not sign in to OA Manager. Please try again."},
      "errors.line_oa_no_line_login": {th: "เข้าสู่ระบบ OA Manager ไม่สำเร็จ ลองใหม่อีกครั้ง", en: "Could not sign in to OA Manager. Please try again."},
      "errors.line_oa_no_qr": {th: "LINE ไม่ส่ง QR มา ลองใหม่อีกครั้ง", en: "LINE did not send a QR code. Please try again."},
      "errors.broadcast_line_oa": {th: "ส่งหากลุ่มลูกค้ายังใช้กับบัญชี LINE OA ไม่ได้", en: "Sending to a segment is not available for LINE OA accounts."}
    });

    // Only the shop owner adds LINE OAs (staff with account rights cannot).
    function oaCanAdd() {
      return hasPermission("manage_accounts") && !(state.currentUser && state.currentUser.teamOwnerId);
    }

    function oaErrorText(login) {
      const key = "errors." + String(login.errorCode || "");
      return I18N[key] ? t(key) : (login.error || t("errors.line_oa_login_failed"));
    }

    // Before a new sign-in: how to become a member, and an optional proxy.
    // The sign-in itself goes through that proxy, then every OA added keeps it.
    function lineOaLoginSettings() {
      return openModal({
        title: t("oa.add_title"),
        confirmKey: "oa.start",
        build(body) {
          const steps = document.createElement("ol");
          steps.className = "oa-howto";
          for (const key of ["oa.step1", "oa.step2", "oa.step3"]) {
            const item = document.createElement("li");
            item.textContent = t(key);
            steps.appendChild(item);
          }
          const proxy = buildProxyForm(false, proxyRequiredFor({channel: "line_oa"}));
          body.append(steps, proxy.row, proxy.fields);
          return () => proxy.values();
        }
      });
    }

    // One window for the whole sign-in: QR, then the PIN, then which OAs to add.
    async function openLineOaLogin(reconnectAccount) {
      let start = reconnectAccount ? {reconnectAccountId: reconnectAccount.id} : null;
      if (!start) {
        const proxy = await lineOaLoginSettings();
        if (!proxy) return;
        if (proxyValuesMissing(proxy, false)) {
          toast(t("proxy.host_port_required"), true);
          return;
        }
        start = proxy;
      }
      await post("/api/lineoa/login/start", start);
      let latest = {state: "starting"};
      let stopped = false;
      const checks = [];
      const result = await openModal({
        title: reconnectAccount
          ? t("oa.reconnect_title", {name: reconnectAccount.label || reconnectAccount.id})
          : t("oa.add_title"),
        confirmKey: "oa.add_selected",
        build(body) {
          const box = document.createElement("div");
          box.className = "oa-login";
          const steps = document.createElement("ol");
          steps.className = "oa-howto";
          for (const key of ["oa.step1", "oa.step2", "oa.step3"]) {
            const item = document.createElement("li");
            item.textContent = t(key);
            steps.appendChild(item);
          }
          const qr = document.createElement("img");
          qr.className = "oa-qr hidden";
          qr.alt = "QR";
          const pin = textSpan("", "oa-pin hidden");
          const status = textSpan(t("oa.preparing"), "oa-status");
          const picks = document.createElement("div");
          picks.className = "oa-picks hidden";
          box.append(steps, qr, pin, status, picks, textSpan(t("oa.unofficial"), "oa-unofficial"));
          body.appendChild(box);
          const confirm = $("modalConfirm");
          // openModal styles the button after build() returns; hide it once that is done.
          queueMicrotask(() => confirm.classList.toggle("hidden", latest.state !== "pick"));

          function showPicks(bots) {
            picks.replaceChildren(textSpan(t("oa.pick"), "muted"));
            // With several OAs nothing is ticked: each one added opens its own stream.
            const fresh = bots.filter((bot) => !bot.connected);
            for (const bot of bots) {
              const row = document.createElement("label");
              row.className = "checkbox-row";
              const input = document.createElement("input");
              input.type = "checkbox";
              input.value = bot.botId;
              input.disabled = Boolean(bot.connected);
              input.checked = fresh.length === 1 && !bot.connected && !bot.chatOff;
              checks.push(input);
              row.appendChild(input);
              if (bot.iconUrl) {
                const icon = document.createElement("img");
                icon.src = bot.iconUrl;
                icon.alt = "";
                row.appendChild(icon);
              }
              const name = [bot.name || bot.basicSearchId, bot.name ? bot.basicSearchId : "",
                bot.connected ? t("oa.pick_connected") : "",
                bot.chatOff ? t("oa.pick_chat_off") : ""].filter(Boolean).join(" · ");
              row.appendChild(textSpan(name));
              picks.appendChild(row);
            }
            if (bots.some((bot) => bot.chatOff && !bot.connected)) {
              picks.appendChild(textSpan(t("oa.chat_off_hint"), "oa-unofficial"));
            }
            picks.classList.remove("hidden");
            confirm.classList.remove("hidden");
          }

          async function tick() {
            if (stopped) return;
            try {
              latest = await api("/api/lineoa/login/state");
            } catch (_err) {
              setTimeout(tick, 2000);
              return;
            }
            const step = latest.state;
            confirm.classList.toggle("hidden", step !== "pick");
            qr.classList.toggle("hidden", step !== "qr" || !latest.qrImage);
            if (step === "qr" && latest.qrImage && qr.getAttribute("src") !== latest.qrImage) qr.src = latest.qrImage;
            pin.classList.toggle("hidden", step !== "pin");
            pin.textContent = step === "pin" ? String(latest.pin || "") : "";
            status.classList.toggle("error", step === "error");
            status.textContent = {starting: t("oa.preparing"), qr: t("oa.scan"), pin: t("oa.pin"), pick: "",
              error: oaErrorText(latest)}[step] ?? t("oa.finishing");
            if (step === "pick") {
              steps.classList.add("hidden");
              showPicks(latest.bots || []);
              return;
            }
            if (step === "done") {
              confirm.classList.remove("hidden");
              confirm.click();
              return;
            }
            if (step === "error" || step === "idle") return;
            setTimeout(tick, 1000);
          }
          tick();
          return () => {
            if (latest.state === "done") return {accountId: latest.accountId};
            if (latest.state === "pick") {
              return {botIds: checks.filter((input) => input.checked && !input.disabled).map((input) => input.value)};
            }
            return null;
          };
        }
      });
      stopped = true;
      if (!result || (result.botIds && !result.botIds.length)) {
        post("/api/lineoa/login/cancel", {}).catch(() => {});
        if (result) toast(t("oa.pick_none"), true);
        return;
      }
      let accountId = result.accountId;
      if (result.botIds) {
        const data = await post("/api/lineoa/login/pick", {botIds: result.botIds});
        accountId = data.account.id;
      }
      await refreshStatus(false);
      if (reconnectAccount) {
        toast(t("oa.reconnected"));
        if (state.activeAccountId === reconnectAccount.id) await selectAccount();
      } else if (accountId) {
        const account = (state.accounts || []).find((item) => item.id === accountId);
        toast(t("oa.added", {name: account ? (account.label || account.id) : accountId}));
        state.activeAccountId = accountId;
        renderAccounts();
        $("accountSelect").value = accountId;
        await selectAccount();
      }
      if (state.tab === "settings") await loadAccountManagement().catch(() => {});
    }

    // A LINE OA reconnects by scanning the OA Manager QR, not the LINE app QR.
    const baseOpenReconnectForOa = openReconnect;
    openReconnect = function (account) {
      const accountId = account ? account.id : (state.activeAccountId || selectedAccountId());
      const target = account || (state.accounts || []).find((item) => item.id === accountId);
      if (target && target.channel === "line_oa") {
        if (!oaCanAdd()) {
          toast(t("oa.ask_owner"), true);
          return;
        }
        openLineOaLogin(target).catch(toastError);
        return;
      }
      baseOpenReconnectForOa(account);
    };

    const baseReconnectKeysForOa = reconnectKeys;
    reconnectKeys = function (account) {
      if (!account || account.channel !== "line_oa") return baseReconnectKeysForOa(account);
      return {button: "oa.reconnect_button", title: "oa.banner_title", body: "oa.banner_body", ask: "oa.ask_owner"};
    };

    ACCOUNT_ADD_ACTIONS.push({
      channel: "line_oa",
      labelKey: "oa.add_menu",
      visible: oaCanAdd,
      run: () => openLineOaLogin().catch(toastError)
    });

    // Settings → accounts: LINE OAs have their own section and actions.
    function lineOaAccountState(account) {
      if (account.sessionState === "reauth_required") return t("oa.state_signed_out");
      if (!account.tokenFileExists) return t("oa.state_missing");
      return t(account.channelProblem === "chat_off" ? "oa.state_chat_off" : "oa.state_connected");
    }

    function renderLineOaAccountList(accounts) {
      const list = $("lineOaAccountList");
      if (!list) return;
      list.replaceChildren();
      const canManage = hasPermission("manage_accounts");
      for (const account of (accounts || []).filter((item) => item.channel === "line_oa")) {
        const row = document.createElement("div");
        row.className = "management-row";
        const info = document.createElement("div");
        const title = document.createElement("strong");
        title.textContent = account.label || account.id;
        const states = [lineOaAccountState(account)];
        if (account.proxyConfigured) states.push(t("proxy.configured"));
        else if (proxyRequiredFor(account)) states.push(t("proxy.missing_required"));
        info.append(title, textSpan(account.userid || "", "mono"), textSpan(states.join(" · "), "muted"));

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
          fix.textContent = t("oa.reconnect_button");
          fix.disabled = !canManage;
          fix.addEventListener("click", () => openReconnect(account));
          actions.append(fix);
        }
        // Name and proxy, in the same window as a LINE account's.
        const edit = document.createElement("button");
        edit.textContent = t("common.edit");
        edit.disabled = !canManage;
        edit.addEventListener("click", () => editLineAccount(account).catch(toastError));
        const del = document.createElement("button");
        del.className = "danger";
        del.textContent = t("common.delete");
        del.disabled = !canManage;
        del.addEventListener("click", () => deleteLineOaAccount(account).catch(toastError));
        actions.append(edit, del);
        row.append(info, actions);
        list.appendChild(row);
      }
      if (!list.children.length) list.appendChild(textSpan(t("oa.accounts_none"), "muted"));
    }

    async function deleteLineOaAccount(account) {
      const confirmed = await openModal({
        title: t("accounts.delete_title", {name: account.label || account.id}),
        build(body) {
          body.appendChild(textSpan(t("oa.delete_note"), "muted"));
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

    const baseRenderLineAccountListForOa = renderLineAccountList;
    renderLineAccountList = function (accounts) {
      baseRenderLineAccountListForOa(accounts);
      renderLineOaAccountList(accounts);
      $("lineOaAccountActions").querySelectorAll("button").forEach((button) => {
        button.classList.toggle("hidden", !oaCanAdd());
      });
    };

    (function addLineOaEntryPoints() {
      const actions = $("lineOaAccountActions");
      if (!actions) return;
      const button = document.createElement("button");
      button.type = "button";
      button.id = "addLineOaAccountButton";
      button.className = "primary";
      button.dataset.i18n = "oa.add_menu";
      button.dataset.requiresPermission = "manage_accounts";
      button.textContent = t("oa.add_menu");
      button.addEventListener("click", () => openLineOaLogin().catch(toastError));
      actions.appendChild(button);
    })();
