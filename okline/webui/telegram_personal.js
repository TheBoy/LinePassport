    // ---- Telegram: a personal account (MTProto) -------------------------
    Object.assign(I18N, {
      "tgu.add_menu": {th: "+ เพิ่ม Telegram ส่วนตัว", en: "+ Add a personal Telegram"},
      "tgu.title": {th: "เชื่อมบัญชี Telegram ส่วนตัว", en: "Connect a personal Telegram account"},
      "tgu.reconnect_title": {th: "เข้าสู่ระบบ Telegram ของ {name} อีกครั้ง", en: "Sign {name} in to Telegram again"},
      "tgu.warning": {th: "ใช้บัญชีส่วนตัวทักลูกค้าได้ทุกคนที่มีแชทอยู่แล้ว แต่ถ้าส่งถี่หรือส่งหาคนแปลกหน้ามาก ๆ Telegram อาจระงับบัญชีได้", en: "A personal account can chat with anyone you already talk to, but sending too fast or to many strangers can get the account limited by Telegram."},
      "tgu.step1": {th: "เปิด my.telegram.org แล้วเข้าสู่ระบบด้วยเบอร์โทรของบัญชีนี้", en: "Open my.telegram.org and sign in with this account's phone number."},
      "tgu.step2": {th: "เลือก API development tools สร้างแอป แล้วคัดลอก api_id และ api_hash", en: "Choose API development tools, create an app, and copy api_id and api_hash."},
      "tgu.step3": {th: "กรอกด้านล่าง แล้ว Telegram จะส่งรหัสเข้าแอป Telegram ของคุณ", en: "Fill them in below; Telegram then sends a code to your Telegram app."},
      "tgu.label": {th: "ชื่อที่จะแสดงในระบบ (ไม่บังคับ)", en: "Name shown here (optional)"},
      "tgu.phone": {th: "เบอร์โทร (มีรหัสประเทศ เช่น +66812345678)", en: "Phone (with country code, e.g. +66812345678)"},
      "tgu.send_code": {th: "ขอรหัส", en: "Send code"},
      "tgu.code_title": {th: "กรอกรหัสจาก Telegram", en: "Enter the code from Telegram"},
      "tgu.code_hint": {th: "รหัสถูกส่งเข้าแอป Telegram ของบัญชีนี้ (ไม่ใช่ SMS)", en: "The code was sent to this account's Telegram app (not SMS)."},
      "tgu.code": {th: "รหัส", en: "Code"},
      "tgu.password_title": {th: "รหัสผ่านสองขั้นตอน", en: "Two-step password"},
      "tgu.password_hint": {th: "บัญชีนี้ตั้งรหัสผ่านสองขั้นตอนไว้ กรอกเพื่อเข้าสู่ระบบให้เสร็จ", en: "This account has a two-step password. Enter it to finish signing in."},
      "tgu.password": {th: "รหัสผ่าน", en: "Password"},
      "tgu.continue": {th: "ต่อไป", en: "Continue"},
      "tgu.done": {th: "เชื่อม Telegram \"{name}\" แล้ว", en: "Connected \"{name}\""},
      "tgu.missing_title": {th: "ต้องติดตั้งส่วนเสริมก่อน", en: "One more install needed"},
      "tgu.missing_body": {th: "บัญชี Telegram ส่วนตัวต้องใช้ Telethon ให้ผู้ดูแลเครื่องรันคำสั่งนี้แล้วเปิดโปรแกรมใหม่:", en: "Personal Telegram accounts need Telethon. Ask whoever runs this server to run this, then restart it:"},
      "errors.telegram_mtproto_missing": {th: "ยังไม่ได้ติดตั้ง Telethon สำหรับบัญชี Telegram ส่วนตัว", en: "Telethon is not installed for personal Telegram accounts."},
      "errors.telegram_api_id_invalid": {th: "api_id หรือ api_hash ไม่ถูกต้อง (ดูที่ my.telegram.org)", en: "api_id / api_hash are not valid (see my.telegram.org)."},
      "errors.telegram_phone_invalid": {th: "เบอร์โทรไม่ถูกต้อง ใส่รหัสประเทศด้วย เช่น +66…", en: "That phone number is not valid; include the country code, e.g. +66…"},
      "errors.telegram_code_invalid": {th: "รหัสไม่ถูกต้อง ดูรหัสล่าสุดในแอป Telegram", en: "The code is wrong. Check the latest code in Telegram."},
      "errors.telegram_code_expired": {th: "รหัสหมดอายุ เริ่มใหม่เพื่อขอรหัสใหม่", en: "The code expired. Start again for a new one."},
      "errors.telegram_password_invalid": {th: "รหัสผ่านสองขั้นตอนไม่ถูกต้อง", en: "The two-step password is wrong."},
      "errors.telegram_flood_wait": {th: "Telegram ให้รอสักครู่ก่อนลองใหม่", en: "Telegram asks you to wait before trying again."},
      "errors.telegram_login_expired": {th: "การเข้าสู่ระบบหมดเวลา เริ่มใหม่อีกครั้ง", en: "The sign-in expired. Start again."},
      "errors.telegram_account_exists": {th: "บัญชี Telegram นี้เชื่อมไว้แล้ว", en: "This Telegram account is already connected."},
      "errors.telegram_account_mismatch": {th: "เข้าสู่ระบบเป็นบัญชี Telegram อื่น", en: "You signed in to a different Telegram account."},
      "errors.telegram_connecting": {th: "บัญชี Telegram กำลังเชื่อมต่อ ลองใหม่อีกครั้ง", en: "The Telegram account is still connecting. Try again."}
    });

    function tguField(body, key, type) {
      const {label, input} = labeledInput(t(key), type || "text");
      input.autocomplete = "off";
      body.appendChild(label);
      return input;
    }

    async function addTelegramPersonal(reconnectAccount = null) {
      const availability = await api("/api/telegram/user/available");
      if (!availability.available) {
        await openModal({
          title: t("tgu.missing_title"),
          build(body) {
            body.appendChild(textSpan(t("tgu.missing_body"), "ux-muted"));
            const code = document.createElement("pre");
            code.className = "mono";
            code.textContent = availability.install;
            body.appendChild(code);
            return () => true;
          },
          confirmKey: "common.close"
        });
        return;
      }
      const start = await openModal({
        title: reconnectAccount
          ? t("tgu.reconnect_title", {name: reconnectAccount.label || reconnectAccount.id})
          : t("tgu.title"),
        build(body) {
          body.appendChild(textSpan(t("tgu.warning"), "ux-warning"));
          const steps = document.createElement("ol");
          steps.className = "tg-howto";
          for (const key of ["tgu.step1", "tgu.step2", "tgu.step3"]) {
            const item = document.createElement("li");
            item.textContent = t(key);
            steps.appendChild(item);
          }
          body.appendChild(steps);
          const label = reconnectAccount ? null : tguField(body, "tgu.label");
          const apiId = tguField(body, "api_id");
          apiId.inputMode = "numeric";
          const apiHash = tguField(body, "api_hash", "password");
          const phone = tguField(body, "tgu.phone", "tel");
          return () => {
            if (!apiId.value.trim() || !apiHash.value.trim() || !phone.value.trim()) return null;
            return {
              apiId: apiId.value.trim(),
              apiHash: apiHash.value.trim(),
              phone: phone.value.trim(),
              label: label ? label.value.trim() : "",
              reconnectAccountId: reconnectAccount ? reconnectAccount.id : ""
            };
          };
        },
        confirmKey: "tgu.send_code"
      });
      if (!start) return;
      await post("/api/telegram/user/start", start);
      try {
        const code = await openModal({
          title: t("tgu.code_title"),
          build(body) {
            body.appendChild(textSpan(t("tgu.code_hint"), "ux-muted"));
            const input = tguField(body, "tgu.code");
            input.inputMode = "numeric";
            return () => input.value.trim();
          },
          confirmKey: "tgu.continue"
        });
        if (!code) throw new Error("cancelled");
        let result = await post("/api/telegram/user/code", {code});
        if (result.state === "password") {
          const password = await openModal({
            title: t("tgu.password_title"),
            build(body) {
              body.appendChild(textSpan(t("tgu.password_hint"), "ux-muted"));
              const input = tguField(body, "tgu.password", "password");
              return () => input.value;
            },
            confirmKey: "tgu.continue"
          });
          if (!password) throw new Error("cancelled");
          result = await post("/api/telegram/user/password", {password});
        }
        state.accounts = (result.accounts && result.accounts.accounts) || state.accounts;
        renderAccounts();
        $("accountSelect").value = result.account.id;
        toast(t("tgu.done", {name: result.account.label}));
        await selectAccount();
      } catch (err) {
        post("/api/telegram/user/cancel", {}).catch(() => {});
        if (err && err.message !== "cancelled") throw err;
      }
    }

    // A signed-out personal account signs in again through the same steps.
    const baseOpenReconnectForTgu = openReconnect;
    openReconnect = function (account) {
      const accountId = account ? account.id : (state.activeAccountId || selectedAccountId());
      const target = account || tgAccount(accountId);
      if (target && target.channel === "telegram_user") {
        if (!hasPermission("manage_accounts")) return toast(t("reconnect.ask_owner"), true);
        addTelegramPersonal(target).catch(toastError);
        return;
      }
      baseOpenReconnectForTgu(account);
    };

    const baseRenderAccountPopoverForTgu = renderAccountPopover;
    renderAccountPopover = function () {
      baseRenderAccountPopoverForTgu();
      const owner = hasPermission("manage_accounts") && !(state.currentUser && state.currentUser.teamOwnerId);
      if (!owner) return;
      const add = document.createElement("button");
      add.type = "button";
      add.className = "add-account";
      add.textContent = t("tgu.add_menu");
      add.addEventListener("click", () => {
        closeAccountPopover();
        addTelegramPersonal().catch(toastError);
      });
      $("accountPopover").appendChild(add);
    };

    (function addPersonalEntryPoint() {
      const botButton = $("addTelegramAccountButton");
      if (!botButton) return;
      const button = document.createElement("button");
      button.type = "button";
      button.id = "addTelegramPersonalButton";
      button.dataset.i18n = "tgu.add_menu";
      button.dataset.requiresPermission = "manage_accounts";
      button.textContent = t("tgu.add_menu");
      button.addEventListener("click", () => addTelegramPersonal().catch(toastError));
      botButton.insertAdjacentElement("afterend", button);
    })();
