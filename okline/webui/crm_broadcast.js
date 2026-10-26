    // ---- Customers: messages to a segment ------------------------------
    Object.assign(state, {crmBroadcastTimer: 0});
    Object.assign(I18N, {
      "crm.bc_send": {th: "ส่งข้อความหากลุ่ม", en: "Message this segment"},
      "crm.bc_title": {th: "ส่งข้อความหา \"{name}\"", en: "Message \"{name}\""},
      "crm.bc_account": {th: "ส่งจากบัญชี", en: "Send from"},
      "crm.bc_text": {th: "ข้อความ", en: "Message"},
      "crm.bc_text_ph": {th: "สวัสดีค่ะ {ชื่อลูกค้า} ร้านมีโปรใหม่…", en: "Hi {ชื่อลูกค้า}, we have a new offer…"},
      "crm.bc_vars": {th: "แทรก:", en: "Insert:"},
      "crm.bc_var_name": {th: "ชื่อลูกค้า", en: "Customer name"},
      "crm.bc_var_date": {th: "วันที่", en: "Date"},
      "crm.bc_var_time": {th: "เวลา", en: "Time"},
      "crm.bc_reach": {th: "ส่งถึงได้ {n} จาก {total} คนในกลุ่ม (คนที่เคยคุยกับบัญชีนี้)", en: "Reaches {n} of {total} people in the segment (those who chat with this account)."},
      "crm.bc_reach_too_many": {th: "กลุ่มนี้มีคนเกิน {limit} คน ให้แบ่งกลุ่มให้เล็กลงก่อน", en: "More than {limit} people; split the segment first."},
      "crm.bc_estimate_slow": {th: "ส่งทีละคน ห่างกัน 30–90 วินาที และไม่เกินโควตาต่อชั่วโมง/วันของบัญชี — ประมาณ {m} นาทีขึ้นไป", en: "One person at a time, 30–90 s apart, within the account's hourly/daily limits: about {m} minutes or more."},
      "crm.bc_estimate_fast": {th: "บอทส่งได้เร็ว ประมาณ {m} นาที", en: "Bots send quickly: about {m} minutes."},
      "crm.bc_preview": {th: "ตัวอย่างที่ลูกค้าจะเห็น", en: "What customers will see"},
      "crm.bc_test": {th: "ส่งทดสอบหาตัวเอง", en: "Send a test to myself"},
      "crm.bc_test_sent": {th: "ส่งทดสอบไปที่บัญชีนี้แล้ว", en: "Test sent to this account"},
      "crm.bc_start": {th: "เริ่มส่ง", en: "Start sending"},
      "crm.bc_started": {th: "เริ่มส่งแล้ว ดูความคืบหน้าได้ด้านล่าง", en: "Sending started; follow it below."},
      "crm.bc_none": {th: "ยังไม่เคยส่งข้อความหากลุ่ม", en: "No segment messages yet."},
      "crm.bc_progress": {th: "ส่งแล้ว {sent} · ไม่สำเร็จ {failed} · ข้าม {skipped} · ทั้งหมด {total}", en: "Sent {sent} · failed {failed} · skipped {skipped} · total {total}"},
      "crm.bc_status_running": {th: "กำลังส่ง", en: "Sending"},
      "crm.bc_status_paused": {th: "หยุดชั่วคราว", en: "Paused"},
      "crm.bc_status_done": {th: "ส่งครบแล้ว", en: "Done"},
      "crm.bc_status_cancelled": {th: "ยกเลิกแล้ว", en: "Cancelled"},
      "crm.bc_note_waiting_for_quota": {th: "รอโควตาการส่งของบัญชี จะส่งต่อเองเมื่อถึงเวลา", en: "Waiting for the account's send limit; it continues by itself."},
      "crm.bc_note_account_disconnected": {th: "บัญชีหลุดการเชื่อมต่อ เชื่อมใหม่แล้วกดส่งต่อ", en: "The account disconnected. Reconnect it, then resume."},
      "crm.bc_note_account_removed": {th: "บัญชีถูกลบไปแล้ว", en: "The account was removed."},
      "crm.bc_pause": {th: "หยุดชั่วคราว", en: "Pause"},
      "crm.bc_resume": {th: "ส่งต่อ", en: "Resume"},
      "crm.bc_cancel": {th: "ยกเลิก", en: "Cancel"},
      "crm.bc_cancel_confirm": {th: "ยกเลิกการส่งที่เหลือ? คนที่ได้รับไปแล้วจะไม่ถูกยกเลิก", en: "Cancel the rest? People already messaged keep their message."},
      "errors.broadcast_nobody": {th: "ยังไม่มีใครในกลุ่มนี้ที่คุยกับบัญชีนั้น", en: "Nobody in this segment chats with that account yet."},
      "errors.broadcast_too_many": {th: "คนในกลุ่มมากเกินไป แบ่งกลุ่มให้เล็กลงก่อน", en: "Too many people; split the segment first."},
      "errors.broadcast_test_unsupported": {th: "Telegram ส่งหาตัวเองไม่ได้ ดูตัวอย่างข้อความแทน", en: "Telegram cannot send to yourself; check the preview instead."}
    });

    function crmRenderBroadcastPreview(text, name) {
      const now = new Date();
      const pad = (n) => String(n).padStart(2, "0");
      return String(text || "")
        .replaceAll("{ชื่อลูกค้า}", name || "คุณลูกค้า")
        .replaceAll("{name}", name || "คุณลูกค้า")
        .replaceAll("{date}", `${pad(now.getDate())}/${pad(now.getMonth() + 1)}/${now.getFullYear()}`)
        .replaceAll("{time}", `${pad(now.getHours())}:${pad(now.getMinutes())}`);
    }

    async function crmBroadcastWizard(segment) {
      const accounts = (state.accounts || []).filter((a) => a.sessionState !== "reauth_required");
      if (!accounts.length) return toast(t("errors.no_account"), true);
      let preview = null;
      const values = await openModal({
        title: t("crm.bc_title", {name: segment.name}),
        build(body) {
          const accountLabel = document.createElement("label");
          accountLabel.append(textSpan(t("crm.bc_account")));
          const account = document.createElement("select");
          for (const item of accounts) {
            const kind = item.channel && item.channel !== "line" ? " · Telegram" : " · LINE";
            account.appendChild(crmOption(item.id, (item.label || item.id) + kind, item.id === selectedAccountId()));
          }
          accountLabel.appendChild(account);
          const textLabel = document.createElement("label");
          textLabel.append(textSpan(t("crm.bc_text")));
          const text = document.createElement("textarea");
          text.maxLength = 2000;
          text.placeholder = t("crm.bc_text_ph");
          textLabel.appendChild(text);
          const chips = document.createElement("div");
          chips.className = "ux-row";
          chips.appendChild(textSpan(t("crm.bc_vars"), "ux-muted"));
          for (const [token, key] of [["{ชื่อลูกค้า}", "crm.bc_var_name"], ["{date}", "crm.bc_var_date"], ["{time}", "crm.bc_var_time"]]) {
            const chip = document.createElement("button");
            chip.type = "button";
            chip.className = "crm-chip";
            chip.textContent = t(key);
            chip.addEventListener("click", () => {
              const at = text.selectionStart ?? text.value.length;
              text.value = text.value.slice(0, at) + token + text.value.slice(text.selectionEnd ?? at);
              text.focus();
              renderPreview();
            });
            chips.appendChild(chip);
          }
          const reach = document.createElement("div");
          reach.className = "ux-muted";
          const estimate = document.createElement("div");
          estimate.className = "ux-warning";
          const previewBox = document.createElement("div");
          previewBox.className = "crm-bc-preview";
          const previewTitle = textSpan(t("crm.bc_preview"), "ux-muted");
          const previewText = document.createElement("p");
          previewBox.append(previewTitle, previewText);
          const test = document.createElement("button");
          test.type = "button";
          test.textContent = t("crm.bc_test");
          test.addEventListener("click", async () => {
            try {
              await post("/api/crm/broadcasts/test", {
                accountId: account.value, text: text.value, sampleName: preview ? preview.sampleName : ""
              });
              toast(t("crm.bc_test_sent"));
            } catch (err) { toastError(err); }
          });
          function renderPreview() {
            previewText.textContent = crmRenderBroadcastPreview(text.value, preview ? preview.sampleName : "");
          }
          async function loadPreview() {
            reach.textContent = t("common.loading");
            try {
              preview = await post("/api/crm/broadcasts/preview", {segmentId: segment.id, accountId: account.value});
            } catch (err) {
              reach.textContent = err.message || "";
              return;
            }
            reach.textContent = preview.tooMany
              ? t("crm.bc_reach_too_many", {limit: preview.limit})
              : t("crm.bc_reach", {n: preview.reachable, total: preview.inSegment});
            const fast = preview.channel === "telegram_bot";
            const minutes = Math.max(1, Math.ceil(fast ? preview.reachable / 60 : preview.reachable));
            estimate.textContent = t(fast ? "crm.bc_estimate_fast" : "crm.bc_estimate_slow", {m: minutes});
            test.classList.toggle("hidden", preview.channel !== "line");
            renderPreview();
          }
          account.addEventListener("change", loadPreview);
          text.addEventListener("input", renderPreview);
          body.append(accountLabel, textLabel, chips, reach, estimate, previewBox, test);
          loadPreview();
          return () => (text.value.trim() ? {accountId: account.value, text: text.value.trim()} : null);
        },
        confirmKey: "crm.bc_start"
      });
      if (!values) return;
      await post("/api/crm/broadcasts/create", {segmentId: segment.id, ...values});
      toast(t("crm.bc_started"));
      await loadCrmBroadcasts();
    }

    async function loadCrmBroadcasts() {
      clearTimeout(state.crmBroadcastTimer);
      if (!hasPermission("read")) return;
      const data = await api("/api/crm/broadcasts");
      const root = $("crmBroadcastList");
      root.replaceChildren();
      const items = data.broadcasts || [];
      if (!items.length) root.appendChild(textSpan(t("crm.bc_none"), "ux-muted"));
      for (const item of items) {
        const row = document.createElement("div");
        row.className = "crm-bc-row";
        const head = document.createElement("div");
        head.className = "ux-row space";
        const title = document.createElement("strong");
        title.textContent = item.name || crmAccountLabel(item.accountId);
        const badgeClass = {running: "info", paused: "warn", done: "ok", cancelled: ""}[item.status] || "";
        head.append(title, textSpan(t("crm.bc_status_" + item.status), "badge " + badgeClass));
        const bar = document.createElement("div");
        bar.className = "crm-bc-bar";
        const fill = document.createElement("span");
        const doneCount = item.sent + item.failed + item.skipped;
        fill.style.width = (item.total ? Math.round((doneCount / item.total) * 100) : 0) + "%";
        bar.appendChild(fill);
        const meta = textSpan(`${crmAccountLabel(item.accountId)} · ${t("crm.bc_progress", item)} · ${fmtTime(item.createdAt)}`, "ux-muted");
        row.append(head, bar, meta);
        if (item.note && (item.status === "running" || item.status === "paused")) {
          row.appendChild(textSpan(t("crm.bc_note_" + item.note) || item.note, "ux-warning"));
        }
        const preview = document.createElement("p");
        preview.className = "crm-bc-text";
        preview.textContent = item.text;
        row.appendChild(preview);
        if (data.canManage && ["running", "paused"].includes(item.status)) {
          const actions = document.createElement("div");
          actions.className = "ux-row";
          const control = (action) => post("/api/crm/broadcasts/control", {id: item.id, action}).then(loadCrmBroadcasts);
          if (item.status === "running") actions.appendChild(crmButton("crm.bc_pause", () => control("pause")));
          if (item.status === "paused") actions.appendChild(crmButton("crm.bc_resume", () => control("resume"), "primary"));
          actions.appendChild(crmButton("crm.bc_cancel", async () => {
            const ok = await openModal({
              title: t("crm.bc_cancel"),
              build(el) { el.appendChild(textSpan(t("crm.bc_cancel_confirm"))); return () => true; },
              confirmKey: "crm.bc_cancel",
              danger: true
            });
            if (ok) await control("cancel");
          }, "danger"));
          row.appendChild(actions);
        }
        root.appendChild(row);
      }
      if (items.some((item) => item.status === "running") && state.tab === "customers" && state.crmView === "segments") {
        state.crmBroadcastTimer = setTimeout(() => loadCrmBroadcasts().catch(() => {}), 10000);
      }
    }

    // Segment cards get a "message this segment" button; the view loads the list.
    const baseLoadCrmSegmentsForBroadcast = loadCrmSegments;
    loadCrmSegments = async function () {
      await baseLoadCrmSegmentsForBroadcast();
      if (hasPermission("manage_crm")) {
        $("crmSegmentList").querySelectorAll(".crm-segment").forEach((card, index) => {
          const segment = state.crmSegments[index];
          const actions = card.querySelector(".ux-row");
          if (!segment || !actions) return;
          actions.appendChild(crmButton("crm.bc_send", () => crmBroadcastWizard(segment)));
        });
      }
      await loadCrmBroadcasts();
    };
