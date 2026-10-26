    // ---- Telegram: staff alerts ----------------------------------------
    Object.assign(state, {tgNotify: null, tgLinkTimer: 0, tgLinkUntil: 0});
    Object.assign(I18N, {
      "settings.nav_telegram": {th: "แจ้งเตือนทาง Telegram", en: "Telegram alerts"},
      "settings.subtitle_telegram": {th: "รับแจ้งเตือนและตอบลูกค้าผ่าน Telegram", en: "Get alerts and answer customers from Telegram."},
      "tg.me_title": {th: "Telegram ของฉัน", en: "My Telegram"},
      "tg.me_linked": {th: "เชื่อมแล้วกับ {name} — แจ้งเตือนจะส่งไปที่ Telegram นี้", en: "Linked to {name}. Alerts go to this Telegram."},
      "tg.me_not_linked": {th: "ยังไม่ได้เชื่อม กดปุ่มด้านล่างเพื่อรับแจ้งเตือนใน Telegram", en: "Not linked yet. Press the button below to get alerts in Telegram."},
      "tg.me_no_bot": {th: "ร้านยังไม่ได้ตั้งบอทแจ้งเตือน ให้เจ้าของร้านตั้งค่าที่การ์ดด้านล่างก่อน", en: "The shop has no alert bot yet. The owner sets it up in the card below."},
      "tg.me_no_bot_staff": {th: "ร้านยังไม่ได้ตั้งบอทแจ้งเตือน ให้เจ้าของร้านตั้งค่าก่อน", en: "The shop has no alert bot yet. Ask the owner to set it up."},
      "tg.link": {th: "เชื่อม Telegram ของฉัน", en: "Link my Telegram"},
      "tg.link_hint": {th: "Telegram จะเปิดขึ้นและมีปุ่ม Start ให้กดเพื่อเชื่อมให้เสร็จ (ลิงก์ใช้ได้ 10 นาที)", en: "Telegram opens and shows a Start button. Press it to finish (the link works for 10 minutes)."},
      "tg.link_open": {th: "เปิด Telegram", en: "Open Telegram"},
      "tg.linked_toast": {th: "เชื่อม Telegram แล้ว", en: "Telegram linked"},
      "tg.test": {th: "ส่งข้อความทดสอบ", en: "Send a test"},
      "tg.test_sent": {th: "ส่งข้อความทดสอบไปที่ Telegram แล้ว", en: "Test sent to Telegram"},
      "tg.unlink": {th: "เลิกเชื่อม", en: "Unlink"},
      "tg.mode_label": {th: "แจ้งเตือนเมื่อ", en: "Alert me"},
      "tg.mode_away": {th: "เฉพาะตอนที่ไม่ได้เปิด LinePassport", en: "Only when LinePassport is not open"},
      "tg.mode_always": {th: "ทุกครั้ง", en: "Every time"},
      "tg.howto_reply": {th: "ตอบลูกค้า: กด Reply ที่ข้อความแจ้งเตือนแล้วพิมพ์ ข้อความจะส่งจากบัญชีร้านในนามของคุณ", en: "To answer a customer, press Reply on their alert and type. It is sent from your shop account, in your name."},
      "tg.howto_buttons": {th: "ปุ่ม \"ฉันรับเรื่อง\" ทำให้แชทเป็นของคุณ ปุ่ม \"ปิดแชท\" ปิดเรื่องนั้น", en: "\"I'll take it\" makes the chat yours; \"Close chat\" marks it done."},
      "tg.howto_stop": {th: "พิมพ์ /stop ในบอทเพื่อเลิกรับแจ้งเตือน", en: "Type /stop in the bot to stop alerts."},
      "tg.bot_title": {th: "บอทแจ้งเตือนของร้าน", en: "Shop alert bot"},
      "tg.bot_intro": {th: "แจ้งเตือนจะส่งจากบอท Telegram ของร้านเอง สร้างครั้งเดียว แล้วทุกคนในทีมเชื่อมกับบอทนี้", en: "Alerts come from a Telegram bot that belongs to your shop. Create one once; every team member links to it."},
      "tg.bot_step1": {th: "ใน Telegram เปิด @BotFather แล้วพิมพ์ /newbot", en: "In Telegram, open @BotFather and send /newbot."},
      "tg.bot_step2": {th: "ตั้งชื่อบอท และชื่อผู้ใช้ที่ลงท้ายด้วย bot", en: "Choose a name and a username ending in \"bot\"."},
      "tg.bot_step3": {th: "คัดลอก token ที่ BotFather ให้มา แล้ววางด้านล่าง", en: "Copy the token BotFather gives you and paste it below."},
      "tg.bot_token": {th: "Token ของบอท", en: "Bot token"},
      "tg.bot_save": {th: "บันทึกและทดสอบ", en: "Save and test"},
      "tg.bot_saved": {th: "เชื่อมบอทแล้ว", en: "Bot connected"},
      "tg.bot_remove": {th: "เลิกใช้บอทนี้", en: "Stop using this bot"},
      "tg.bot_remove_confirm": {th: "เลิกใช้บอทแจ้งเตือน? ทุกคนในทีมจะไม่ได้รับแจ้งเตือนทาง Telegram จนกว่าจะตั้งบอทใหม่", en: "Stop using the alert bot? Nobody gets Telegram alerts until a new bot is set up."},
      "tg.bot_connected": {th: "ใช้บอท @{name} อยู่", en: "Using @{name}"},
      "tg.bot_broken": {th: "บอทใช้งานไม่ได้ (token ถูกยกเลิก) วาง token ใหม่เพื่อใช้ต่อ", en: "The bot stopped working (token revoked). Paste a new token to continue."},
      "errors.telegram_token_invalid": {th: "token นี้ไม่ใช่ token ของบอทจาก @BotFather", en: "That is not a bot token from @BotFather."},
      "errors.telegram_token_rejected": {th: "Telegram ไม่รับ token นี้ ลองคัดลอกใหม่จาก @BotFather", en: "Telegram rejected this token. Copy it again from @BotFather."},
      "errors.telegram_unreachable": {th: "ติดต่อ Telegram ไม่ได้ ลองใหม่อีกครั้ง", en: "Could not reach Telegram. Try again."},
      "errors.telegram_not_configured": {th: "ร้านยังไม่ได้ตั้งบอทแจ้งเตือน", en: "The shop has not set up an alert bot."},
      "errors.telegram_not_linked": {th: "ยังไม่ได้เชื่อม Telegram", en: "Link your Telegram first."}
    });

    async function loadTelegramNotify() {
      if (!state.currentUser) return;
      const data = await api("/api/telegram/notify");
      state.tgNotify = data;
      renderTelegramNotify();
      return data;
    }

    function renderTelegramNotify() {
      const data = state.tgNotify;
      if (!data) return;
      const link = data.link || {};
      const configured = Boolean(data.configured);
      $("tgBotCard").classList.toggle("hidden", !data.canManage);
      const status = $("tgBotStatus");
      status.replaceChildren();
      if (data.broken) status.appendChild(textSpan(t("tg.bot_broken"), "badge bad"));
      else if (configured) status.appendChild(textSpan(t("tg.bot_connected", {name: data.botUsername}), "badge ok"));
      $("tgBotRemove").classList.toggle("hidden", !configured && !data.broken);
      let stateText;
      if (!configured) stateText = t(data.canManage ? "tg.me_no_bot" : "tg.me_no_bot_staff");
      else if (link.linked) stateText = t("tg.me_linked", {name: link.username ? "@" + link.username : (link.name || "Telegram")});
      else stateText = t("tg.me_not_linked");
      $("tgMeState").textContent = stateText;
      $("tgLinkButton").disabled = !configured;
      $("tgLinkButton").classList.toggle("hidden", Boolean(link.linked));
      $("tgTestButton").classList.toggle("hidden", !link.linked);
      $("tgUnlinkButton").classList.toggle("hidden", !link.linked);
      $("tgMode").value = link.mode || "away";
      $("tgMode").disabled = !link.linked;
      if (link.linked) {
        $("tgLinkBox").classList.add("hidden");
        clearTimeout(state.tgLinkTimer);
      }
    }

    async function startTelegramLink() {
      const data = await post("/api/telegram/notify/link", {});
      $("tgLinkUrl").href = data.url;
      $("tgLinkBox").classList.remove("hidden");
      window.open(data.url, "_blank", "noopener");
      state.tgLinkUntil = Number(data.expiresAt) || Date.now() + 600000;
      const wasLinked = Boolean(state.tgNotify && state.tgNotify.link && state.tgNotify.link.linked);
      const poll = async () => {
        clearTimeout(state.tgLinkTimer);
        if (Date.now() > state.tgLinkUntil || state.tab !== "settings") return;
        const fresh = await loadTelegramNotify().catch(() => null);
        if (fresh && fresh.link && fresh.link.linked && !wasLinked) {
          toast(t("tg.linked_toast"));
          return;
        }
        state.tgLinkTimer = setTimeout(poll, 3000);
      };
      state.tgLinkTimer = setTimeout(poll, 3000);
    }

    const baseSetSettingsPaneForTelegram = setSettingsPane;
    setSettingsPane = function (pane) {
      const telegram = pane === "telegram";
      baseSetSettingsPaneForTelegram(telegram ? "notify" : pane);
      $("telegramSettingsPane").classList.toggle("active", telegram);
      $("telegramSettingsTabButton").classList.toggle("active", telegram);
      if (telegram) {
        $("notifySettingsPane").classList.remove("active");
        $("notifySettingsTabButton").classList.remove("active");
        $("settingsSubtitle").textContent = t("settings.subtitle_telegram");
        loadTelegramNotify().catch(toastError);
      }
    };

    $("tgLinkButton").addEventListener("click", () => startTelegramLink().catch(toastError));
    $("tgTestButton").addEventListener("click", async () => {
      try {
        await post("/api/telegram/notify/test", {});
        toast(t("tg.test_sent"));
      } catch (err) { toastError(err); }
    });
    $("tgUnlinkButton").addEventListener("click", async () => {
      try {
        state.tgNotify = await post("/api/telegram/notify/unlink", {});
        renderTelegramNotify();
      } catch (err) { toastError(err); }
    });
    $("tgMode").addEventListener("change", async () => {
      try {
        state.tgNotify = await post("/api/telegram/notify/mode", {mode: $("tgMode").value});
        renderTelegramNotify();
      } catch (err) { toastError(err); }
    });
    $("tgBotSave").addEventListener("click", async () => {
      const token = $("tgBotToken").value.trim();
      if (!token) return;
      $("tgBotSave").disabled = true;
      try {
        state.tgNotify = await post("/api/telegram/notify/settings", {token});
        $("tgBotToken").value = "";
        renderTelegramNotify();
        toast(t("tg.bot_saved"));
      } catch (err) {
        toastError(err);
      } finally {
        $("tgBotSave").disabled = false;
      }
    });
    $("tgBotRemove").addEventListener("click", async () => {
      const ok = await openModal({
        title: t("tg.bot_remove"),
        build(el) { el.appendChild(textSpan(t("tg.bot_remove_confirm"))); return () => true; },
        confirmKey: "tg.bot_remove",
        danger: true
      });
      if (!ok) return;
      try {
        state.tgNotify = await post("/api/telegram/notify/remove", {});
        renderTelegramNotify();
      } catch (err) { toastError(err); }
    });
