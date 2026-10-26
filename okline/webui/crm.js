    // ---- Customers (CRM) ------------------------------------------------
    TABS.push("customers");
    const CRM_STATUSES = ["new", "open", "pending", "closed"];
    const CRM_PAGE_SIZE = 50;
    const CRM_SIDE_KEY = "okline.crmSide";
    const CRM_CHAT_FILTER_KEY = "okline.crmChatFilter";
    Object.assign(state, {
      crmView: "list",
      crmPage: 1,
      crmTotal: 0,
      crmCustomers: [],
      crmSelected: new Set(),
      crmTags: [],
      crmMembers: [],
      crmMe: "",
      crmBasicsAt: 0,
      crmSegment: null,
      crmSegments: [],
      crmTasks: [],
      crmMap: {},
      crmMapAccountId: "",
      crmChatFilter: "all",
      crmSide: null,
      crmSideKey: "",
      crmSideOpen: true,
      crmDrawer: null,
      crmDrawerId: "",
      crmSearchTimer: 0,
      crmRefreshTimer: 0
    });
    try {
      // Beside the chat on wide screens; on narrow ones it covers the chat, so it waits for a tap.
      state.crmSideOpen = localStorage.getItem(CRM_SIDE_KEY) !== "off"
        && window.matchMedia("(min-width: 1201px)").matches;
      state.crmChatFilter = localStorage.getItem(CRM_CHAT_FILTER_KEY) || "all";
    } catch (_) { /* optional */ }

    Object.assign(I18N, {
      "tabs.customers": {th: "ลูกค้า", en: "Customers"},
      "crm.title": {th: "ลูกค้า", en: "Customers"},
      "crm.subtitle": {th: "ทุกคนที่เคยคุยกับร้าน จากทุกบัญชี รวมไว้ในที่เดียว", en: "Everyone who has chatted with your shop, from every account, in one list."},
      "crm.guide_open": {th: "วิธีใช้", en: "Usage guide"},
      "crm.guide_back": {th: "กลับไปหน้ารายชื่อลูกค้า", en: "Back to customers"},
      "crm.view_list": {th: "รายชื่อลูกค้า", en: "Customer list"},
      "crm.view_tasks": {th: "งานติดตาม", en: "Follow-ups"},
      "crm.view_segments": {th: "กลุ่มลูกค้า", en: "Segments"},
      "crm.search_ph": {th: "ค้นหาชื่อ เบอร์โทร อีเมล…", en: "Search name, phone, email…"},
      "crm.filter_any_status": {th: "ทุกสถานะ", en: "Any status"},
      "crm.filter_any_assignee": {th: "ทุกคนในทีม", en: "Anyone"},
      "crm.filter_mine": {th: "ที่ฉันดูแล", en: "Looked after by me"},
      "crm.filter_unassigned": {th: "ยังไม่มีคนดูแล", en: "Nobody yet"},
      "crm.filter_any_tag": {th: "ทุกแท็ก", en: "Any tag"},
      "crm.filter_any_channel": {th: "ทุกช่องทาง", en: "All channels"},
      "crm.filter_any_time": {th: "ติดต่อเมื่อไหร่ก็ได้", en: "Any time"},
      "crm.filter_today": {th: "คุยวันนี้", en: "Talked today"},
      "crm.filter_week": {th: "คุยในสัปดาห์นี้", en: "Talked this week"},
      "crm.filter_quiet30": {th: "เงียบเกิน 30 วัน", en: "Quiet for 30+ days"},
      "crm.filter_quiet90": {th: "เงียบเกิน 90 วัน", en: "Quiet for 90+ days"},
      "crm.save_segment": {th: "บันทึกเป็นกลุ่ม", en: "Save as segment"},
      "crm.export": {th: "ดาวน์โหลด CSV", en: "Download CSV"},
      "crm.add_customer": {th: "+ เพิ่มลูกค้า", en: "+ Add customer"},
      "crm.bulk_selected": {th: "เลือก {n} คน", en: "{n} selected"},
      "crm.bulk_tag": {th: "ติดแท็ก", en: "Add tag"},
      "crm.bulk_untag": {th: "เอาแท็กออก", en: "Remove tag"},
      "crm.bulk_merge": {th: "รวมเป็นคนเดียวกัน", en: "Merge into one"},
      "crm.bulk_clear": {th: "ยกเลิกที่เลือก", en: "Clear selection"},
      "crm.col_customer": {th: "ลูกค้า", en: "Customer"},
      "crm.col_channel": {th: "ช่องทาง", en: "Channel"},
      "crm.col_tags": {th: "แท็ก", en: "Tags"},
      "crm.col_status": {th: "สถานะแชท", en: "Chat status"},
      "crm.col_assignee": {th: "ผู้ดูแล", en: "Handled by"},
      "crm.col_last": {th: "ติดต่อล่าสุด", en: "Last contact"},
      "crm.prev": {th: "ก่อนหน้า", en: "Previous"},
      "crm.next": {th: "ถัดไป", en: "Next"},
      "crm.pager": {th: "{from}–{to} จาก {total} คน", en: "{from}–{to} of {total}"},
      "crm.empty_title": {th: "ยังไม่มีลูกค้า", en: "No customers yet"},
      "crm.empty_body": {th: "เมื่อมีคนทักแชทส่วนตัวเข้ามา การ์ดลูกค้าจะขึ้นที่นี่เอง", en: "When someone messages you one-to-one, their card appears here."},
      "crm.empty_filtered": {th: "ไม่พบลูกค้าที่ตรงกับตัวกรอง", en: "No customers match these filters."},
      "crm.status_new": {th: "ใหม่", en: "New"},
      "crm.status_open": {th: "กำลังดูแล", en: "In progress"},
      "crm.status_pending": {th: "รอลูกค้า", en: "Waiting on customer"},
      "crm.status_closed": {th: "ปิดแล้ว", en: "Closed"},
      "crm.no_name": {th: "ยังไม่มีชื่อ", en: "No name yet"},
      "crm.me": {th: "ฉัน", en: "Me"},
      "crm.unknown_member": {th: "คนที่ออกจากทีมแล้ว", en: "Former team member"},
      "crm.nobody": {th: "— ยังไม่มีคนดูแล —", en: "— Nobody yet —"},
      "crm.handled_by": {th: "ดูแลโดย {name}", en: "Handled by {name}"},
      "crm.tasks_mine": {th: "งานของฉัน", en: "My follow-ups"},
      "crm.tasks_all": {th: "งานทั้งทีม", en: "Whole team"},
      "crm.tasks_open": {th: "ยังไม่เสร็จ", en: "To do"},
      "crm.tasks_done": {th: "เสร็จแล้ว", en: "Done"},
      "crm.add_task": {th: "+ เพิ่มงานติดตาม", en: "+ Add follow-up"},
      "crm.task_title": {th: "ต้องทำอะไร", en: "What to do"},
      "crm.task_title_ph": {th: "เช่น โทรถามว่าได้ของหรือยัง", en: "e.g. Call to check the order arrived"},
      "crm.task_due": {th: "วันเวลาที่ต้องทำ", en: "When"},
      "crm.task_assignee": {th: "ใครทำ", en: "Who"},
      "crm.task_group_overdue": {th: "เลยกำหนด", en: "Overdue"},
      "crm.task_group_today": {th: "วันนี้", en: "Today"},
      "crm.task_group_later": {th: "ถัดไป", en: "Coming up"},
      "crm.task_group_nodate": {th: "ไม่มีกำหนด", en: "No date"},
      "crm.task_group_done": {th: "ทำเสร็จแล้ว", en: "Completed"},
      "crm.task_none": {th: "ไม่มีงานติดตามค้าง เยี่ยมมาก", en: "No follow-ups waiting. Nice work."},
      "crm.task_delete_confirm": {th: "ลบงาน \"{title}\"?", en: "Delete \"{title}\"?"},
      "crm.task_for": {th: "ลูกค้า: {name}", en: "Customer: {name}"},
      "crm.segments_intro": {th: "กลุ่มลูกค้าคือการบันทึกตัวกรองไว้ เช่น \"VIP ที่เงียบเกิน 30 วัน\" รายชื่อในกลุ่มอัปเดตเองเมื่อข้อมูลลูกค้าเปลี่ยน", en: "A segment is a saved filter, such as \"VIP and quiet for 30 days\". Its list updates by itself as customers change."},
      "crm.segment_none": {th: "ยังไม่มีกลุ่ม ลองกรองรายชื่อลูกค้าแล้วกด \"บันทึกเป็นกลุ่ม\"", en: "No segments yet. Filter the customer list, then press \"Save as segment\"."},
      "crm.segment_name": {th: "ชื่อกลุ่ม", en: "Segment name"},
      "crm.segment_people": {th: "คน", en: "people"},
      "crm.segment_view": {th: "ดูรายชื่อ", en: "View list"},
      "crm.segment_rename": {th: "เปลี่ยนชื่อ", en: "Rename"},
      "crm.segment_delete_confirm": {th: "ลบกลุ่ม \"{name}\"? (ข้อมูลลูกค้าไม่หาย)", en: "Delete segment \"{name}\"? Customers are not affected."},
      "crm.segment_viewing": {th: "กำลังดูกลุ่ม: {name}", en: "Viewing segment: {name}"},
      "crm.segment_clear": {th: "ดูทุกคน", en: "Show everyone"},
      "crm.segment_everyone": {th: "ลูกค้าทุกคน", en: "Every customer"},
      "crm.summary_within": {th: "คุยภายใน {n} วัน", en: "Talked within {n} days"},
      "crm.summary_older": {th: "เงียบเกิน {n} วัน", en: "Quiet for {n}+ days"},
      "crm.settings_title": {th: "การดูแลแชท", en: "Chat handling"},
      "crm.settings_autoclose": {th: "ปิดแชทอัตโนมัติเมื่อเงียบ", en: "Close chats automatically after"},
      "crm.settings_never": {th: "ไม่ปิดเอง", en: "Never"},
      "crm.settings_days_quiet": {th: "วัน", en: "quiet days"},
      "crm.settings_autoassign": {th: "ให้แชทใหม่เป็นของพนักงานคนแรกที่ตอบ", en: "Give a new chat to the first staff member who replies"},
      "crm.settings_saved": {th: "บันทึกการตั้งค่าแล้ว", en: "Settings saved"},
      "crm.chip_all": {th: "ทั้งหมด", en: "All"},
      "crm.chip_mine": {th: "ของฉัน", en: "Mine"},
      "crm.chip_unassigned": {th: "ยังไม่มีคนรับ", en: "Nobody yet"},
      "crm.chip_pending": {th: "รอลูกค้า", en: "Waiting on customer"},
      "crm.chip_closed": {th: "ปิดแล้ว", en: "Closed"},
      "crm.panel_toggle": {th: "ข้อมูลลูกค้า", en: "Customer info"},
      "crm.side_title": {th: "ข้อมูลลูกค้า", en: "Customer"},
      "crm.side_pick_chat": {th: "เลือกแชทเพื่อดูข้อมูลลูกค้า", en: "Pick a chat to see the customer."},
      "crm.side_group": {th: "แชทกลุ่มไม่มีการ์ดลูกค้า", en: "Group chats have no customer card."},
      "crm.side_start": {th: "เริ่มติดตามลูกค้าคนนี้", en: "Start a customer card"},
      "crm.side_status": {th: "สถานะแชท", en: "Chat status"},
      "crm.side_owner": {th: "ผู้ดูแลแชทนี้", en: "Looked after by"},
      "crm.claim": {th: "ฉันรับเรื่องนี้", en: "I'll take it"},
      "crm.claimed": {th: "รับเรื่องแล้ว", en: "You're on it"},
      "crm.open_profile": {th: "เปิดการ์ดลูกค้า", en: "Open customer card"},
      "crm.quick_note_ph": {th: "จดโน้ตถึงทีม…", en: "Note for the team…"},
      "crm.save_note": {th: "บันทึกโน้ต", en: "Save note"},
      "crm.note_saved": {th: "บันทึกโน้ตแล้ว", en: "Note saved"},
      "crm.pin": {th: "ปักหมุด", en: "Pin"},
      "crm.unpin": {th: "เลิกปักหมุด", en: "Unpin"},
      "crm.tag_add_ph": {th: "เพิ่มแท็ก…", en: "Add a tag…"},
      "crm.tag_name": {th: "ชื่อแท็ก", en: "Tag name"},
      "crm.tag_pick": {th: "เลือกแท็ก", en: "Choose a tag"},
      "crm.sec_contact": {th: "ข้อมูลติดต่อ", en: "Contact details"},
      "crm.sec_tags": {th: "แท็ก", en: "Tags"},
      "crm.sec_chats": {th: "แชท", en: "Chats"},
      "crm.sec_tasks": {th: "งานติดตาม", en: "Follow-ups"},
      "crm.sec_notes": {th: "โน้ตของทีม", en: "Team notes"},
      "crm.sec_history": {th: "ประวัติ", en: "History"},
      "crm.field_name": {th: "ชื่อ", en: "Name"},
      "crm.field_phone": {th: "เบอร์โทร", en: "Phone"},
      "crm.field_email": {th: "อีเมล", en: "Email"},
      "crm.field_company": {th: "บริษัท/ร้าน", en: "Company"},
      "crm.edit_details": {th: "แก้ไขข้อมูล", en: "Edit details"},
      "crm.open_chat": {th: "เปิดแชท", en: "Open chat"},
      "crm.load_more": {th: "ดูเพิ่ม", en: "Show more"},
      "crm.history_none": {th: "ยังไม่มีประวัติ", en: "Nothing here yet"},
      "crm.notes_none": {th: "ยังไม่มีโน้ต", en: "No notes yet"},
      "crm.delete_customer": {th: "ลบการ์ดลูกค้า", en: "Delete customer card"},
      "crm.delete_confirm": {th: "ลบการ์ดของ {name}? แท็ก โน้ต งาน และประวัติในระบบนี้จะหายทั้งหมด (แชทใน LINE ไม่หาย)", en: "Delete {name}'s card? Tags, notes, follow-ups and history here are removed. Chats in the app itself stay."},
      "crm.merge_title": {th: "รวมลูกค้าเป็นคนเดียวกัน", en: "Merge customers"},
      "crm.merge_pick": {th: "เลือกการ์ดหลักที่จะเก็บไว้", en: "Choose the card to keep"},
      "crm.merge_need_two": {th: "เลือกอย่างน้อย 2 คนก่อน", en: "Select at least two customers first."},
      "crm.merged": {th: "รวมการ์ดลูกค้าแล้ว", en: "Customers merged"},
      "crm.saved": {th: "บันทึกแล้ว", en: "Saved"},
      "crm.sender_ai": {th: "AI ตอบอัตโนมัติ", en: "Auto reply"},
      "crm.sender_schedule": {th: "ส่งตามเวลา", en: "Scheduled send"},
      "crm.sender_broadcast": {th: "ส่งหากลุ่ม", en: "Segment message"},
      "crm.sender_app": {th: "ส่งจากแอป", en: "Sent from the app"},
      "crm.ev_system": {th: "ระบบ", en: "System"},
      "crm.ev_created": {th: "สร้างการ์ดลูกค้า", en: "Customer card created"},
      "crm.ev_assigned": {th: "{who} ให้ {to} ดูแล", en: "{who} gave the chat to {to}"},
      "crm.ev_unassigned": {th: "{who} ยกเลิกผู้ดูแล", en: "{who} removed the owner"},
      "crm.ev_claimed": {th: "{who} รับเรื่อง", en: "{who} took the chat"},
      "crm.ev_status": {th: "{who} เปลี่ยนสถานะเป็น {to}", en: "{who} set the status to {to}"},
      "crm.ev_tag_added": {th: "{who} ติดแท็ก {tags}", en: "{who} added {tags}"},
      "crm.ev_tag_removed": {th: "{who} เอาแท็ก {tags} ออก", en: "{who} removed {tags}"},
      "crm.ev_note": {th: "{who} เขียนโน้ต", en: "{who} wrote a note"},
      "crm.ev_task": {th: "{who} เพิ่มงาน: {title}", en: "{who} added a follow-up: {title}"},
      "crm.ev_task_done": {th: "{who} ทำงานเสร็จ: {title}", en: "{who} finished: {title}"},
      "crm.ev_merged": {th: "{who} รวมการ์ด {n} ใบเข้ามา", en: "{who} merged {n} cards in"},
      "crm.ev_updated": {th: "{who} แก้ไขข้อมูลติดต่อ", en: "{who} edited the details"},
      "help.customers.title": {th: "ลูกค้า", en: "Customers"},
      "help.customers.intro": {th: "รู้ว่าลูกค้าแต่ละคนเป็นใคร ใครในทีมดูแลอยู่ และต้องทำอะไรต่อ", en: "Know who each customer is, who is looking after them and what to do next."},
      "help.customers.s1_title": {th: "การ์ดลูกค้าขึ้นเอง", en: "Customers appear by themselves"},
      "help.customers.s1_body": {th: "ใครที่ทักแชทส่วนตัวกับบัญชีใดก็ได้ของร้าน จะได้การ์ดลูกค้าเอง คนเดียวกันที่ทักหลายบัญชีจะรวมเป็นการ์ดเดียว", en: "Anyone who chats one-to-one with any of your accounts gets a customer card. The same person on two accounts is one card."},
      "help.customers.s2_title": {th: "รับเรื่องแชท", en: "Take on a chat"},
      "help.customers.s2_body": {th: "ในหน้าแชท เปิดแผงข้อมูลลูกค้าแล้วกด \"ฉันรับเรื่องนี้\" หรือแค่ตอบลูกค้า คนแรกที่ตอบจะเป็นผู้ดูแลแชทนั้น", en: "In Chat, open the customer panel and press \"I'll take it\", or just reply: the first person to reply looks after the chat."},
      "help.customers.s3_title": {th: "ติดแท็ก จดโน้ต ตั้งงานติดตาม", en: "Tag, note and follow up"},
      "help.customers.s3_body": {th: "ติดแท็กเช่น VIP เขียนโน้ตให้ทั้งทีมอ่าน และตั้งงานติดตามพร้อมวันเวลา ถึงเวลาแล้วระบบจะเตือน", en: "Add tags like VIP, write notes the whole team can read, and set a follow-up with a date. You get a reminder when it is due."},
      "help.customers.s4_title": {th: "บันทึกกลุ่มลูกค้า", en: "Save segments"},
      "help.customers.s4_body": {th: "กรองรายชื่อแล้วกด \"บันทึกเป็นกลุ่ม\" เพื่อเรียกดูกลุ่มเดิมได้ทุกเมื่อ", en: "Filter the list, then save it as a segment to find the same group again."},
      "help.customers.tip1": {th: "แชทที่มีผู้ดูแลแล้ว จะแจ้งเตือนเฉพาะคนที่ดูแล", en: "A chat assigned to someone only notifies that person."},
      "help.customers.tip2": {th: "ถ้าลูกค้าทักแชทที่ปิดไปแล้ว แชทจะเปิดใหม่เอง", en: "When a customer writes to a closed chat, it opens again by itself."},
      "help.customers.q1": {th: "ทำไมไม่มีแชทกลุ่ม", en: "Why are groups missing?"},
      "help.customers.a1": {th: "การ์ดลูกค้าใช้กับแชทส่วนตัวเท่านั้น แชทกลุ่มยังใช้งานได้ตามปกติในหน้าแชท", en: "Customer cards are for one-to-one chats. Group chats still work as usual in Chat."},
      "help.customers.q2": {th: "ลูกค้าคนเดียวกันขึ้นสองการ์ด", en: "The same person shows up twice"},
      "help.customers.a2": {th: "ติ๊กเลือกทั้งสองแถว แล้วกด \"รวมเป็นคนเดียวกัน\" แชท แท็ก โน้ต และงานจะย้ายมาอยู่การ์ดเดียว", en: "Tick both rows and choose \"Merge into one\". Chats, tags, notes and follow-ups move to one card."},
      "errors.assign_forbidden": {th: "เฉพาะหัวหน้าทีมที่ส่งต่อแชทให้คนอื่นได้", en: "Only a manager can hand chats to someone else."},
      "errors.assignee_invalid": {th: "คนนี้ตอบจากบัญชีนี้ไม่ได้", en: "That person cannot reply from this account."},
      "errors.chat_not_supported": {th: "การ์ดลูกค้าใช้กับแชทส่วนตัวเท่านั้น", en: "Customer cards are for one-to-one chats only."},
      "errors.note_not_yours": {th: "แก้ได้เฉพาะโน้ตที่คุณเขียนเอง", en: "You can only change your own notes."}
    });

    function crmMemberName(id) {
      if (!id) return "";
      if (id === state.crmMe) return t("crm.me");
      const member = state.crmMembers.find((item) => item.id === id);
      return member ? (member.name || t("crm.unknown_member")) : t("crm.unknown_member");
    }

    function crmInitial(name) {
      return (String(name || "?").trim().charAt(0) || "?").toUpperCase();
    }

    function crmAvatar(name) {
      const avatar = document.createElement("span");
      avatar.className = "avatar";
      avatar.setAttribute("aria-hidden", "true");
      avatar.textContent = crmInitial(name);
      return avatar;
    }

    function crmStatusBadge(status) {
      return textSpan(t("crm.status_" + status), "badge crm-status " + status);
    }

    function crmPreview(text) {
      const tokens = {
        "[image]": "content.image", "[video]": "content.video", "[audio]": "content.audio",
        "[sticker]": "content.sticker", "[contact]": "content.contact", "[file]": "content.file",
        "[location]": "content.location", "[media]": "content.unsupported"
      };
      return tokens[text] ? t(tokens[text]) : (text || "");
    }

    function crmChannelBadges(items) {
      const wrap = document.createElement("span");
      wrap.className = "crm-channel";
      for (const channel of [...new Set((items || []).map((item) => item.channel))]) {
        wrap.appendChild(textSpan(channel === "telegram" ? "Telegram" : "LINE", "badge " + channel));
      }
      return wrap;
    }

    function crmTagChip(tag, onRemove) {
      const chip = document.createElement("span");
      chip.className = "crm-tag";
      const dot = document.createElement("span");
      dot.className = "crm-tag-dot";
      if (tag.color) dot.style.background = tag.color;
      chip.append(dot, textSpan(tag.name));
      if (onRemove) {
        const remove = document.createElement("button");
        remove.type = "button";
        remove.textContent = "×";
        remove.setAttribute("aria-label", t("common.delete") + " " + tag.name);
        remove.addEventListener("click", (event) => { event.stopPropagation(); onRemove(tag); });
        chip.appendChild(remove);
      }
      return chip;
    }

    function crmAccountLabel(accountId) {
      const account = (state.accounts || []).find((item) => item.id === accountId);
      return account ? (account.label || account.id) : accountId;
    }

    function crmOption(value, label, selected) {
      const option = document.createElement("option");
      option.value = value;
      option.textContent = label;
      if (selected) option.selected = true;
      return option;
    }

    function crmEndOfToday() {
      const end = new Date();
      end.setHours(23, 59, 59, 999);
      return end.getTime();
    }

    function crmLocalInputValue(ms) {
      if (!ms) return "";
      const d = new Date(ms);
      const pad = (n) => String(n).padStart(2, "0");
      return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
    }

    function crmSection(titleKey, ...children) {
      const section = document.createElement("section");
      section.className = "ux-card";
      const head = document.createElement("h3");
      head.textContent = t(titleKey);
      section.append(head, ...children.filter(Boolean));
      return section;
    }

    function crmButton(labelKey, onClick, cls) {
      const button = document.createElement("button");
      button.type = "button";
      if (cls) button.className = cls;
      button.textContent = t(labelKey);
      button.addEventListener("click", (event) => {
        event.stopPropagation();
        Promise.resolve(onClick(event)).catch(toastError);
      });
      return button;
    }

    // -- shared data: tags, team, badges ------------------------------------
    async function loadCrmBasics(force = false) {
      if (!hasPermission("read")) return;
      if (!force && state.crmBasicsAt && Date.now() - state.crmBasicsAt < 30000) return;
      const [tags, members] = await Promise.all([api("/api/crm/tags"), api("/api/crm/members")]);
      state.crmTags = tags.tags || [];
      state.crmMembers = members.members || [];
      state.crmMe = members.me || "";
      state.crmBasicsAt = Date.now();
      renderCrmFilterOptions();
    }

    function renderCrmFilterOptions() {
      const who = $("crmFilterAssignee");
      const whoValue = who.value;
      who.replaceChildren(
        crmOption("", t("crm.filter_any_assignee")),
        crmOption("me", t("crm.filter_mine")),
        crmOption("none", t("crm.filter_unassigned")),
        ...state.crmMembers.filter((m) => m.id !== state.crmMe).map((m) => crmOption(m.id, m.name))
      );
      who.value = [...who.options].some((o) => o.value === whoValue) ? whoValue : "";
      const tag = $("crmFilterTag");
      const tagValue = tag.value;
      tag.replaceChildren(
        crmOption("", t("crm.filter_any_tag")),
        ...state.crmTags.map((item) => crmOption(item.id, `${item.name} (${item.customers})`))
      );
      tag.value = [...tag.options].some((o) => o.value === tagValue) ? tagValue : "";
    }

    async function loadCrmSummary() {
      if (!hasPermission("read") || !state.currentUser) return;
      try {
        const data = await api(`/api/crm/summary?dueBefore=${crmEndOfToday()}`);
        const due = Number(data.tasksDue) || 0;
        $("navCustomersBadge").textContent = due > 99 ? "99+" : String(due);
        $("navCustomersBadge").classList.toggle("hidden", !due);
        $("crmTasksBadge").textContent = String(due);
        $("crmTasksBadge").classList.toggle("hidden", !due);
      } catch (_) { /* badges are best effort */ }
    }

    // -- customer list ------------------------------------------------------
    function setCrmView(view) {
      if (!["list", "tasks", "segments", "guide"].includes(view)) view = "list";
      state.crmView = view;
      document.querySelectorAll("[data-crm-pane]").forEach((pane) => {
        pane.classList.toggle("hidden", pane.dataset.crmPane !== view);
      });
      document.querySelectorAll("[data-crm-view]").forEach((button) => {
        button.setAttribute("aria-selected", String(button.dataset.crmView === view));
      });
      if (view === "list") loadCustomers().catch(toastError);
      if (view === "tasks") loadCrmTasks().catch(toastError);
      if (view === "segments") loadCrmSegments().catch(toastError);
    }

    function crmCurrentFilter() {
      const filter = {};
      const q = $("crmSearch").value.trim();
      if (q) filter.q = q;
      const status = $("crmFilterStatus").value;
      if (status) filter.statuses = [status];
      const who = $("crmFilterAssignee").value;
      if (who) filter.assignedTo = [who === "me" ? state.crmMe : who];
      const tag = $("crmFilterTag").value;
      if (tag) filter.tagsAny = [tag];
      const channel = $("crmFilterChannel").value;
      if (channel) filter.channels = [channel];
      const last = $("crmFilterLast").value;
      if (last) {
        const [kind, days] = last.split(":");
        filter.lastContact = kind === "within" ? {within: Number(days)} : {olderThan: Number(days)};
      }
      return filter;
    }

    async function loadCustomers() {
      if (!hasPermission("read")) return;
      await loadCrmBasics().catch(() => {});
      const segment = state.crmSegment;
      const body = {page: state.crmPage, pageSize: CRM_PAGE_SIZE, filter: crmCurrentFilter()};
      if (segment) {
        body.segmentId = segment.id;
        body.filter = {q: $("crmSearch").value.trim()};
      }
      $("crmSegmentBanner").classList.toggle("hidden", !segment);
      if (segment) $("crmSegmentBannerText").textContent = t("crm.segment_viewing", {name: segment.name});
      const data = await post("/api/crm/customers/search", body);
      state.crmCustomers = data.customers || [];
      state.crmTotal = Number(data.total) || 0;
      renderCustomers();
    }

    function renderCustomers() {
      const tbody = $("crmTableBody");
      tbody.replaceChildren();
      if (!state.crmCustomers.length) {
        const row = document.createElement("tr");
        const cell = document.createElement("td");
        cell.colSpan = 7;
        cell.className = "crm-empty";
        const filtered = Object.keys(crmCurrentFilter()).length || state.crmSegment;
        const title = document.createElement("strong");
        title.textContent = t("crm.empty_title");
        cell.append(title, textSpan(t(filtered ? "crm.empty_filtered" : "crm.empty_body")));
        row.appendChild(cell);
        tbody.appendChild(row);
      }
      for (const customer of state.crmCustomers) {
        const row = document.createElement("tr");
        row.dataset.id = customer.id;
        const check = document.createElement("td");
        check.className = "crm-check";
        const box = document.createElement("input");
        box.type = "checkbox";
        box.checked = state.crmSelected.has(customer.id);
        box.setAttribute("aria-label", customer.displayName || t("crm.no_name"));
        box.addEventListener("click", (event) => event.stopPropagation());
        box.addEventListener("change", () => {
          if (box.checked) state.crmSelected.add(customer.id);
          else state.crmSelected.delete(customer.id);
          renderCrmBulk();
        });
        check.appendChild(box);

        const person = document.createElement("td");
        const personWrap = document.createElement("span");
        personWrap.className = "crm-person";
        const text = document.createElement("span");
        const name = document.createElement("strong");
        name.textContent = customer.displayName || t("crm.no_name");
        const sub = document.createElement("small");
        sub.textContent = customer.phone || crmPreview(customer.lastPreview);
        text.append(name, sub);
        personWrap.append(crmAvatar(customer.displayName), text);
        person.appendChild(personWrap);

        const channel = document.createElement("td");
        channel.className = "crm-hide-sm";
        channel.appendChild(crmChannelBadges(customer.identities));
        const tags = document.createElement("td");
        tags.className = "crm-hide-sm";
        const tagWrap = document.createElement("span");
        tagWrap.className = "crm-tags";
        for (const tag of customer.tags || []) tagWrap.appendChild(crmTagChip(tag));
        tags.appendChild(tagWrap);
        const conv = (customer.conversations || [])[0];
        const status = document.createElement("td");
        status.appendChild(conv ? crmStatusBadge(conv.status) : textSpan("–", "ux-muted"));
        const owner = document.createElement("td");
        owner.className = "crm-hide-sm";
        owner.textContent = conv && conv.assignedTo ? crmMemberName(conv.assignedTo) : "–";
        const last = document.createElement("td");
        last.className = "crm-hide-sm";
        last.textContent = fmtTime(customer.lastMessageAt) || "–";
        row.append(check, person, channel, tags, status, owner, last);
        row.addEventListener("click", () => openCustomer(customer.id).catch(toastError));
        tbody.appendChild(row);
      }
      const from = state.crmTotal ? (state.crmPage - 1) * CRM_PAGE_SIZE + 1 : 0;
      const to = Math.min(state.crmTotal, state.crmPage * CRM_PAGE_SIZE);
      $("crmPagerText").textContent = t("crm.pager", {from, to, total: state.crmTotal});
      $("crmPrevPage").disabled = state.crmPage <= 1;
      $("crmNextPage").disabled = to >= state.crmTotal;
      $("crmSelectAll").checked = state.crmCustomers.length > 0
        && state.crmCustomers.every((item) => state.crmSelected.has(item.id));
      renderCrmBulk();
    }

    function renderCrmBulk() {
      const count = state.crmSelected.size;
      $("crmBulkBar").classList.toggle("hidden", !count);
      $("crmBulkCount").textContent = t("crm.bulk_selected", {n: count});
      $("crmBulkMerge").disabled = count < 2 || !hasPermission("manage_crm");
    }

    function crmTagInput(placeholderKey) {
      const input = document.createElement("input");
      input.setAttribute("list", "crmTagOptions");
      input.placeholder = t(placeholderKey);
      input.maxLength = 40;
      let list = $("crmTagOptions");
      if (!list) {
        list = document.createElement("datalist");
        list.id = "crmTagOptions";
        document.body.appendChild(list);
      }
      list.replaceChildren(...state.crmTags.map((tag) => crmOption(tag.name, tag.name)));
      return input;
    }

    async function crmBulkTag() {
      const ids = [...state.crmSelected];
      if (!ids.length) return;
      const name = await openModal({
        title: t("crm.bulk_tag"),
        build(body) {
          const label = document.createElement("label");
          label.append(textSpan(t("crm.tag_name")));
          const input = crmTagInput("crm.tag_add_ph");
          label.appendChild(input);
          body.appendChild(label);
          return () => input.value.trim();
        },
        confirmKey: "common.save"
      });
      if (!name) return;
      await post("/api/crm/customers/tags", {customerIds: ids, add: [{name}]});
      state.crmBasicsAt = 0;
      toast(t("crm.saved"));
      await loadCustomers();
    }

    async function crmBulkUntag() {
      const ids = [...state.crmSelected];
      if (!ids.length || !state.crmTags.length) return;
      const tagId = await openModal({
        title: t("crm.bulk_untag"),
        build(body) {
          const label = document.createElement("label");
          label.append(textSpan(t("crm.tag_pick")));
          const select = document.createElement("select");
          select.append(...state.crmTags.map((tag) => crmOption(tag.id, tag.name)));
          label.appendChild(select);
          body.appendChild(label);
          return () => select.value;
        },
        confirmKey: "common.save"
      });
      if (!tagId) return;
      await post("/api/crm/customers/tags", {customerIds: ids, remove: [tagId]});
      state.crmBasicsAt = 0;
      toast(t("crm.saved"));
      await loadCustomers();
    }

    async function crmBulkMerge() {
      const chosen = state.crmCustomers.filter((item) => state.crmSelected.has(item.id));
      if (chosen.length < 2) return toast(t("crm.merge_need_two"), true);
      const targetId = await openModal({
        title: t("crm.merge_title"),
        build(body) {
          body.appendChild(textSpan(t("crm.merge_pick"), "ux-muted"));
          const list = document.createElement("div");
          list.className = "ux-grid";
          chosen.forEach((customer, index) => {
            const label = document.createElement("label");
            label.className = "ux-row";
            const radio = document.createElement("input");
            radio.type = "radio";
            radio.name = "crmMergeTarget";
            radio.value = customer.id;
            radio.checked = index === 0;
            radio.style.width = "auto";
            label.append(radio, textSpan(`${customer.displayName || t("crm.no_name")} · ${customer.phone || crmPreview(customer.lastPreview)}`));
            list.appendChild(label);
          });
          body.appendChild(list);
          return () => (body.querySelector("input[name=crmMergeTarget]:checked") || {}).value || "";
        },
        confirmKey: "crm.bulk_merge"
      });
      if (!targetId) return;
      const sourceIds = chosen.map((item) => item.id).filter((id) => id !== targetId);
      await post("/api/crm/customers/merge", {targetId, sourceIds});
      state.crmSelected.clear();
      toast(t("crm.merged"));
      await loadCustomers();
      openCustomer(targetId).catch(toastError);
    }

    async function crmAddCustomer() {
      const values = await openModal({
        title: t("crm.add_customer").replace(/^\+\s*/, ""),
        build(body) {
          const fields = {};
          for (const [key, label, type] of [
            ["displayName", "crm.field_name", "text"], ["phone", "crm.field_phone", "tel"],
            ["email", "crm.field_email", "email"], ["company", "crm.field_company", "text"]
          ]) {
            const {label: el, input} = labeledInput(t(label), type);
            fields[key] = input;
            body.appendChild(el);
          }
          return () => Object.fromEntries(Object.entries(fields).map(([k, input]) => [k, input.value.trim()]));
        },
        confirmKey: "common.save"
      });
      if (!values) return;
      const data = await post("/api/crm/customers/create", values);
      await loadCustomers();
      renderCustomerDrawer(data);
    }

    async function crmSaveSegment() {
      const name = await openModal({
        title: t("crm.save_segment"),
        build(body) {
          const {label, input} = labeledInput(t("crm.segment_name"), "text");
          input.maxLength = 80;
          body.appendChild(label);
          body.appendChild(textSpan(crmFilterSummary(crmCurrentFilter()), "ux-muted"));
          return () => input.value.trim();
        },
        confirmKey: "common.save"
      });
      if (!name) return;
      await post("/api/crm/segments/save", {name, filter: crmCurrentFilter()});
      toast(t("crm.saved"));
      setCrmView("segments");
    }

    function crmExport() {
      const params = new URLSearchParams();
      if (state.crmSegment) {
        params.set("segmentId", state.crmSegment.id);
        params.set("filter", JSON.stringify({q: $("crmSearch").value.trim()}));
      } else {
        params.set("filter", JSON.stringify(crmCurrentFilter()));
      }
      const link = document.createElement("a");
      link.href = "/api/crm/customers/export?" + params.toString();
      link.download = "";
      document.body.appendChild(link);
      link.click();
      link.remove();
    }

    // -- customer card (drawer) ---------------------------------------------
    async function openCustomer(id) {
      if (!id) return;
      state.crmDrawerId = id;
      $("crmScrim").classList.remove("hidden");
      const drawer = $("crmDrawer");
      drawer.classList.remove("hidden");
      drawer.replaceChildren();
      const loading = document.createElement("div");
      loading.className = "state-row";
      const spin = document.createElement("span");
      spin.className = "spinner";
      loading.append(spin, textSpan(t("common.loading")));
      drawer.appendChild(loading);
      await loadCrmBasics().catch(() => {});
      const data = await api(`/api/crm/customer?id=${encodeURIComponent(id)}`);
      if (state.crmDrawerId !== id) return;
      renderCustomerDrawer(data);
    }

    function closeCustomerDrawer() {
      state.crmDrawerId = "";
      state.crmDrawer = null;
      $("crmDrawer").classList.add("hidden");
      $("crmScrim").classList.add("hidden");
    }

    async function refreshCustomerDrawer() {
      if (!state.crmDrawerId) return;
      const id = state.crmDrawerId;
      const data = await api(`/api/crm/customer?id=${encodeURIComponent(id)}`);
      if (state.crmDrawerId === id) renderCustomerDrawer(data);
    }

    function renderCustomerDrawer(data) {
      state.crmDrawer = data;
      state.crmDrawerId = data.customer.id;
      if (data.members) state.crmMembers = data.members;
      $("crmScrim").classList.remove("hidden");
      const drawer = $("crmDrawer");
      drawer.classList.remove("hidden");
      drawer.replaceChildren();
      const customer = data.customer;
      const canEdit = Boolean(data.canEdit);
      const canManage = Boolean(data.canManage);

      const head = document.createElement("header");
      head.className = "crm-drawer-head";
      const avatar = crmAvatar(customer.displayName);
      avatar.classList.add("avatar-lg");
      const headText = document.createElement("div");
      headText.className = "crm-head-text";
      const title = document.createElement("h3");
      title.id = "crmDrawerName";
      title.textContent = customer.displayName || t("crm.no_name");
      const tags = document.createElement("span");
      tags.className = "crm-tags";
      tags.append(crmChannelBadges(customer.identities));
      headText.append(title, tags);
      const close = document.createElement("button");
      close.className = "icon-ghost";
      close.type = "button";
      close.setAttribute("aria-label", t("common.close"));
      close.textContent = "×";
      close.style.fontSize = "22px";
      close.addEventListener("click", closeCustomerDrawer);
      head.append(avatar, headText, close);

      const body = document.createElement("div");
      body.className = "crm-drawer-body";

      // Contact details
      const kv = document.createElement("div");
      kv.className = "crm-kv";
      const rows = [
        ["crm.field_phone", customer.phone], ["crm.field_email", customer.email],
        ["crm.field_company", customer.company]
      ];
      for (const identity of customer.identities || []) {
        rows.push([identity.channel === "telegram" ? "Telegram" : "LINE",
          [identity.name, identity.username ? "@" + identity.username : ""].filter(Boolean).join(" ") || identity.externalId]);
      }
      for (const [key, value] of Object.entries(customer.fields || {})) rows.push([key, value]);
      for (const [label, value] of rows) {
        kv.append(textSpan(label.startsWith("crm.") ? t(label) : label), textSpan(value || "–"));
      }
      body.appendChild(crmSection("crm.sec_contact", kv,
        canEdit ? crmButton("crm.edit_details", () => crmEditCustomer(customer)) : null));

      // Tags
      const tagRow = document.createElement("div");
      tagRow.className = "crm-tags";
      for (const tag of customer.tags || []) {
        tagRow.appendChild(crmTagChip(tag, canEdit ? (item) => crmTagCustomer(customer.id, {remove: [item.id]}) : null));
      }
      let tagAdd = null;
      if (canEdit) {
        tagAdd = document.createElement("div");
        tagAdd.className = "crm-add-row";
        const input = crmTagInput("crm.tag_add_ph");
        const add = crmButton("common.save", () => {
          const name = input.value.trim();
          if (name) return crmTagCustomer(customer.id, {add: [{name}]});
          return null;
        });
        input.addEventListener("keydown", (event) => { if (event.key === "Enter") add.click(); });
        tagAdd.append(input, add);
      }
      body.appendChild(crmSection("crm.sec_tags", tagRow, tagAdd));

      // Chats
      const chats = document.createElement("div");
      for (const conv of customer.conversations || []) chats.appendChild(crmChatRow(conv, customer, data));
      if (!chats.children.length) chats.appendChild(textSpan("–", "ux-muted"));
      body.appendChild(crmSection("crm.sec_chats", chats));

      // Follow-ups
      const tasks = document.createElement("div");
      for (const task of (data.tasks || []).filter((item) => item.status === "open")) {
        tasks.appendChild(crmTaskRow(task, {afterChange: refreshCustomerDrawer}));
      }
      body.appendChild(crmSection("crm.sec_tasks", tasks,
        canEdit ? crmButton("crm.add_task", () => crmAddTask({customerId: customer.id}, refreshCustomerDrawer)) : null));

      // Notes
      const notes = document.createElement("div");
      let noteAdd = null;
      if (canEdit) {
        noteAdd = document.createElement("div");
        noteAdd.className = "crm-add-row";
        const area = document.createElement("textarea");
        area.placeholder = t("crm.quick_note_ph");
        area.maxLength = 5000;
        noteAdd.append(area, crmButton("crm.save_note", async () => {
          if (!area.value.trim()) return;
          await post("/api/crm/notes/create", {customerId: customer.id, body: area.value.trim()});
          toast(t("crm.note_saved"));
          await refreshCustomerDrawer();
        }, "primary"));
      }
      for (const note of data.notes || []) notes.appendChild(crmNoteItem(note, canManage, refreshCustomerDrawer));
      if (!notes.children.length) notes.appendChild(textSpan(t("crm.notes_none"), "ux-muted"));
      body.appendChild(crmSection("crm.sec_notes", noteAdd, notes));

      // History
      const timeline = document.createElement("div");
      timeline.className = "crm-timeline";
      crmAppendTimeline(timeline, data.timeline || [], customer);
      const more = crmButton("crm.load_more", async () => {
        const items = [...timeline.querySelectorAll("[data-at]")];
        const before = items.length ? Number(items[items.length - 1].dataset.at) : 0;
        const page = await api(`/api/crm/timeline?customerId=${encodeURIComponent(customer.id)}&before=${before}&limit=60`);
        crmAppendTimeline(timeline, page.timeline || [], customer);
        if (!(page.timeline || []).length) more.remove();
      }, "crm-link");
      body.appendChild(crmSection("crm.sec_history", timeline, (data.timeline || []).length >= 60 ? more : null));

      if (canManage) {
        body.appendChild(crmButton("crm.delete_customer", async () => {
          const ok = await openModal({
            title: t("crm.delete_customer"),
            build(el) { el.appendChild(textSpan(t("crm.delete_confirm", {name: customer.displayName || t("crm.no_name")}))); return () => true; },
            confirmKey: "common.delete",
            danger: true
          });
          if (!ok) return;
          await post("/api/crm/customers/delete", {id: customer.id});
          closeCustomerDrawer();
          await loadCustomers();
        }, "danger"));
      }
      drawer.append(head, body);
    }

    function crmChatRow(conv, customer, data) {
      const row = document.createElement("div");
      row.className = "crm-chat-row";
      const label = document.createElement("span");
      label.className = "crm-chat-label";
      const strong = document.createElement("strong");
      strong.textContent = crmAccountLabel(conv.accountId);
      label.append(strong, textSpan(fmtTime(conv.lastMessageAt) || "–", "ux-muted"));
      row.appendChild(label);
      if (data.canEdit) {
        const status = document.createElement("select");
        status.setAttribute("aria-label", t("crm.side_status"));
        status.append(...CRM_STATUSES.map((s) => crmOption(s, t("crm.status_" + s), s === conv.status)));
        status.addEventListener("change", () => crmSetStatus({conversationId: conv.id}, status.value)
          .then(refreshCustomerDrawer).catch(toastError));
        row.appendChild(status);
        row.appendChild(crmAssigneeSelect(conv, data, (assignee) => crmAssign({conversationId: conv.id}, assignee)
          .then(refreshCustomerDrawer)));
      } else {
        row.append(crmStatusBadge(conv.status), textSpan(conv.assignedTo ? crmMemberName(conv.assignedTo) : t("crm.nobody"), "ux-muted"));
      }
      row.appendChild(crmButton("crm.open_chat", () => openCrmChat(conv.accountId, conv.chatId, customer.displayName)));
      return row;
    }

    function crmAssigneeSelect(conv, data, onChange) {
      const select = document.createElement("select");
      select.setAttribute("aria-label", t("crm.side_owner"));
      const manage = Boolean(data.canManage);
      const me = state.crmMe || data.me;
      select.appendChild(crmOption("", t("crm.nobody"), !conv.assignedTo));
      for (const member of state.crmMembers) {
        if (!member.active || !(member.accountIds || []).includes(conv.accountId)) continue;
        const option = crmOption(member.id, member.id === me ? `${member.name} (${t("crm.me")})` : member.name, member.id === conv.assignedTo);
        if (!manage && member.id !== me && member.id !== conv.assignedTo) option.disabled = true;
        select.appendChild(option);
      }
      if (conv.assignedTo && ![...select.options].some((o) => o.value === conv.assignedTo)) {
        select.appendChild(crmOption(conv.assignedTo, crmMemberName(conv.assignedTo), true));
      }
      if (!manage && conv.assignedTo && conv.assignedTo !== me) select.options[0].disabled = true;
      select.addEventListener("change", () => {
        Promise.resolve(onChange(select.value)).catch((err) => {
          select.value = conv.assignedTo || "";
          toastError(err);
        });
      });
      return select;
    }

    async function crmEditCustomer(customer) {
      const values = await openModal({
        title: t("crm.edit_details"),
        build(body) {
          const fields = {};
          for (const [key, label, type] of [
            ["displayName", "crm.field_name", "text"], ["phone", "crm.field_phone", "tel"],
            ["email", "crm.field_email", "email"], ["company", "crm.field_company", "text"]
          ]) {
            const {label: el, input} = labeledInput(t(label), type);
            input.value = customer[key] || "";
            fields[key] = input;
            body.appendChild(el);
          }
          return () => Object.fromEntries(Object.entries(fields).map(([k, input]) => [k, input.value.trim()]));
        },
        confirmKey: "common.save"
      });
      if (!values) return;
      const data = await post("/api/crm/customers/update", {id: customer.id, ...values});
      renderCustomerDrawer(data);
      crmAfterChange();
    }

    async function crmTagCustomer(customerId, change) {
      await post("/api/crm/customers/tags", {customerIds: [customerId], ...change});
      state.crmBasicsAt = 0;
      await loadCrmBasics(true).catch(() => {});
      if (state.crmDrawerId === customerId) await refreshCustomerDrawer();
      crmAfterChange();
    }

    function crmNoteItem(note, canManage, afterChange) {
      const item = document.createElement("div");
      item.className = "crm-note" + (note.pinned ? " pinned" : "");
      const text = document.createElement("p");
      text.textContent = note.body;
      const meta = document.createElement("small");
      meta.textContent = `${crmMemberName(note.authorId) || "–"} · ${fmtTime(note.createdAt)}`;
      item.append(text, meta);
      if (note.authorId === state.crmMe || canManage) {
        const actions = document.createElement("div");
        actions.className = "crm-note-actions";
        actions.append(
          crmButton(note.pinned ? "crm.unpin" : "crm.pin", async () => {
            await post("/api/crm/notes/update", {id: note.id, pinned: !note.pinned});
            await afterChange();
          }, "crm-link"),
          crmButton("common.delete", async () => {
            await post("/api/crm/notes/delete", {id: note.id});
            await afterChange();
          }, "crm-link")
        );
        item.appendChild(actions);
      }
      return item;
    }

    function crmEventText(item) {
      const data = item.data || {};
      const who = item.actorId === "system" ? t("crm.ev_system") : (crmMemberName(item.actorId) || t("crm.ev_system"));
      switch (item.kind) {
        case "created": return t("crm.ev_created");
        case "assigned":
          if (data.to && data.to === item.actorId) return t("crm.ev_claimed", {who});
          return data.to ? t("crm.ev_assigned", {who, to: crmMemberName(data.to)}) : t("crm.ev_unassigned", {who});
        case "status": return t("crm.ev_status", {who, to: t("crm.status_" + (data.to || "open"))});
        case "tag": {
          const parts = [];
          if ((data.added || []).length) parts.push(t("crm.ev_tag_added", {who, tags: data.added.join(", ")}));
          if ((data.removed || []).length) parts.push(t("crm.ev_tag_removed", {who, tags: data.removed.join(", ")}));
          return parts.join(" · ");
        }
        case "note": return t("crm.ev_note", {who});
        case "task": return t("crm.ev_task", {who, title: data.title || ""});
        case "task_done": return t("crm.ev_task_done", {who, title: data.title || ""});
        case "merged": return t("crm.ev_merged", {who, n: data.count || 1});
        case "updated": return t("crm.ev_updated", {who});
        default: return item.kind;
      }
    }

    function crmSenderLabel(item, customer) {
      if (item.direction === "in") return customer.displayName || t("crm.no_name");
      if (item.senderKind === "staff") return crmMemberName(item.senderId);
      if (item.senderKind === "ai") return t("crm.sender_ai");
      if (item.senderKind === "schedule") return t("crm.sender_schedule");
      if (item.senderKind === "broadcast") return t("crm.sender_broadcast");
      if (item.senderKind === "api") return "API";
      return t("crm.sender_app");
    }

    function crmAppendTimeline(root, items, customer) {
      if (!items.length && !root.children.length) {
        root.appendChild(textSpan(t("crm.history_none"), "ux-muted"));
        return;
      }
      for (const item of items) {
        const el = document.createElement("div");
        el.dataset.at = String(item.at);
        if (item.type === "event") {
          el.className = "crm-tl-item event";
          el.textContent = `${crmEventText(item)} · ${fmtTime(item.at)}`;
        } else {
          el.className = "crm-tl-item " + (item.direction === "in" ? "in" : "out");
          const text = document.createElement("p");
          text.textContent = item.contentType ? (item.text || contentLabel({contentType: item.contentType})) : item.text;
          const meta = document.createElement("small");
          meta.textContent = `${crmSenderLabel(item, customer)} · ${crmAccountLabel(item.accountId)} · ${fmtTime(item.at)}`;
          el.append(text, meta);
        }
        root.appendChild(el);
      }
    }

    // -- follow-up tasks ----------------------------------------------------
    async function loadCrmTasks() {
      if (!hasPermission("read")) return;
      const scope = $("crmTaskScope").value || "mine";
      const status = $("crmTaskStatus").value || "open";
      listLoading("crmTaskList");
      const data = await api(`/api/crm/tasks?scope=${encodeURIComponent(scope)}&status=${encodeURIComponent(status)}`);
      state.crmTasks = data.tasks || [];
      if (data.members) state.crmMembers = data.members;
      if (data.me) state.crmMe = data.me;
      renderCrmTasks();
    }

    function renderCrmTasks() {
      const root = $("crmTaskList");
      root.replaceChildren();
      const tasks = state.crmTasks;
      if (!tasks.length) {
        root.appendChild(textSpan(t("crm.task_none"), "crm-empty"));
        return;
      }
      const now = Date.now();
      const endToday = crmEndOfToday();
      const groups = $("crmTaskStatus").value === "done"
        ? [["done", "crm.task_group_done", () => true]]
        : [
          ["overdue", "crm.task_group_overdue", (task) => task.dueAt && task.dueAt < now],
          ["today", "crm.task_group_today", (task) => task.dueAt && task.dueAt >= now && task.dueAt <= endToday],
          ["later", "crm.task_group_later", (task) => task.dueAt > endToday],
          ["nodate", "crm.task_group_nodate", (task) => !task.dueAt]
        ];
      for (const [cls, key, match] of groups) {
        const items = tasks.filter(match);
        if (!items.length) continue;
        const group = document.createElement("div");
        group.className = "crm-task-group " + cls;
        const head = document.createElement("h4");
        head.textContent = `${t(key)} (${items.length})`;
        group.appendChild(head);
        for (const task of items) group.appendChild(crmTaskRow(task, {showCustomer: true, afterChange: loadCrmTasks}));
        root.appendChild(group);
      }
    }

    function crmTaskRow(task, options = {}) {
      const row = document.createElement("div");
      row.className = "crm-task" + (task.status === "done" ? " done" : "");
      const box = document.createElement("input");
      box.type = "checkbox";
      box.checked = task.status === "done";
      box.disabled = !hasPermission("crm");
      box.setAttribute("aria-label", task.title);
      box.addEventListener("change", async () => {
        try {
          await post("/api/crm/tasks/update", {id: task.id, status: box.checked ? "done" : "open"});
          loadCrmSummary();
          if (options.afterChange) await options.afterChange();
        } catch (err) {
          box.checked = !box.checked;
          toastError(err);
        }
      });
      const main = document.createElement("div");
      main.className = "crm-task-main";
      const title = document.createElement("strong");
      title.textContent = task.title;
      main.appendChild(title);
      if (task.body) main.appendChild(textSpan(task.body, "ux-muted"));
      const meta = document.createElement("small");
      if (task.dueAt) {
        const due = textSpan(fmtTime(task.dueAt), "crm-due" + (task.status === "open" && task.dueAt < Date.now() ? " overdue" : ""));
        meta.appendChild(due);
      }
      if (task.assignedTo) meta.appendChild(textSpan(` · ${crmMemberName(task.assignedTo)}`));
      main.appendChild(meta);
      if (options.showCustomer && task.customerId) {
        const link = document.createElement("button");
        link.type = "button";
        link.className = "crm-link";
        link.textContent = t("crm.task_for", {name: task.customerName || t("crm.no_name")});
        link.addEventListener("click", () => openCustomer(task.customerId).catch(toastError));
        main.appendChild(link);
      }
      const actions = document.createElement("div");
      actions.className = "crm-note-actions";
      if (hasPermission("crm")) {
        actions.append(
          crmButton("common.edit", () => crmEditTask(task, options.afterChange), "crm-link"),
          crmButton("common.delete", async () => {
            const ok = await openModal({
              title: t("common.delete"),
              build(el) { el.appendChild(textSpan(t("crm.task_delete_confirm", {title: task.title}))); return () => true; },
              confirmKey: "common.delete",
              danger: true
            });
            if (!ok) return;
            await post("/api/crm/tasks/delete", {id: task.id});
            loadCrmSummary();
            if (options.afterChange) await options.afterChange();
          }, "crm-link")
        );
      }
      row.append(box, main, actions);
      return row;
    }

    function crmTaskForm(body, task) {
      const {label: titleLabel, input: title} = labeledInput(t("crm.task_title"), "text");
      title.placeholder = t("crm.task_title_ph");
      title.maxLength = 200;
      title.value = task ? task.title : "";
      const {label: dueLabel, input: due} = labeledInput(t("crm.task_due"), "datetime-local");
      const tomorrow = new Date();
      tomorrow.setDate(tomorrow.getDate() + 1);
      tomorrow.setHours(10, 0, 0, 0);
      due.value = crmLocalInputValue(task ? task.dueAt : tomorrow.getTime());
      const whoLabel = document.createElement("label");
      whoLabel.append(textSpan(t("crm.task_assignee")));
      const who = document.createElement("select");
      const current = task ? task.assignedTo : state.crmMe;
      who.appendChild(crmOption("", t("crm.nobody"), !current));
      for (const member of state.crmMembers.filter((m) => m.active)) {
        who.appendChild(crmOption(member.id, member.id === state.crmMe ? `${member.name} (${t("crm.me")})` : member.name, member.id === current));
      }
      whoLabel.appendChild(who);
      body.append(titleLabel, dueLabel, whoLabel);
      return () => {
        if (!title.value.trim()) return null;
        return {
          title: title.value.trim(),
          dueAt: due.value ? new Date(due.value).getTime() : 0,
          assignedTo: who.value
        };
      };
    }

    async function crmAddTask(target = {}, afterChange) {
      await loadCrmBasics().catch(() => {});
      const values = await openModal({
        title: t("crm.add_task").replace(/^\+\s*/, ""),
        build: (body) => crmTaskForm(body, null),
        confirmKey: "common.save"
      });
      if (!values) return;
      await post("/api/crm/tasks/create", {...target, ...values});
      toast(t("crm.saved"));
      loadCrmSummary();
      if (afterChange) await afterChange();
      else if (state.crmView === "tasks") await loadCrmTasks();
    }

    async function crmEditTask(task, afterChange) {
      const values = await openModal({
        title: t("common.edit"),
        build: (body) => crmTaskForm(body, task),
        confirmKey: "common.save"
      });
      if (!values) return;
      await post("/api/crm/tasks/update", {id: task.id, ...values});
      loadCrmSummary();
      if (afterChange) await afterChange();
    }

    // -- segments and settings ----------------------------------------------
    function crmFilterSummary(filter) {
      const parts = [];
      if (filter.q) parts.push(`"${filter.q}"`);
      const tagName = (id) => (state.crmTags.find((tag) => tag.id === id) || {}).name || "?";
      if ((filter.tagsAny || []).length) parts.push(filter.tagsAny.map(tagName).join(" / "));
      if ((filter.tagsAll || []).length) parts.push(filter.tagsAll.map(tagName).join(" + "));
      if ((filter.statuses || []).length) parts.push(filter.statuses.map((s) => t("crm.status_" + s)).join(", "));
      if ((filter.channels || []).length) parts.push(filter.channels.map((c) => (c === "telegram" ? "Telegram" : "LINE")).join(", "));
      if ((filter.assignedTo || []).length) {
        parts.push(filter.assignedTo.map((id) => (id === "none" ? t("crm.filter_unassigned") : crmMemberName(id))).join(", "));
      }
      if (filter.lastContact && filter.lastContact.within) parts.push(t("crm.summary_within", {n: filter.lastContact.within}));
      if (filter.lastContact && filter.lastContact.olderThan) parts.push(t("crm.summary_older", {n: filter.lastContact.olderThan}));
      return parts.join(" · ") || t("crm.segment_everyone");
    }

    async function loadCrmSegments() {
      if (!hasPermission("read")) return;
      await loadCrmBasics().catch(() => {});
      const [data, settings] = await Promise.all([api("/api/crm/segments"), api("/api/crm/settings")]);
      state.crmSegments = data.segments || [];
      const root = $("crmSegmentList");
      root.replaceChildren();
      if (!state.crmSegments.length) root.appendChild(textSpan(t("crm.segment_none"), "ux-muted"));
      for (const segment of state.crmSegments) {
        const card = document.createElement("section");
        card.className = "ux-card crm-segment";
        const name = document.createElement("h3");
        name.textContent = segment.name;
        const count = document.createElement("div");
        count.append(textSpan(String(segment.count), "crm-count"), textSpan(" " + t("crm.segment_people"), "ux-muted"));
        const actions = document.createElement("div");
        actions.className = "ux-row";
        actions.appendChild(crmButton("crm.segment_view", () => {
          state.crmSegment = {id: segment.id, name: segment.name};
          state.crmPage = 1;
          state.crmSelected.clear();
          setCrmView("list");
        }, "primary"));
        if (data.canManage) {
          actions.append(
            crmButton("crm.segment_rename", async () => {
              const name = await openModal({
                title: t("crm.segment_rename"),
                build(body) {
                  const {label, input} = labeledInput(t("crm.segment_name"), "text");
                  input.value = segment.name;
                  body.appendChild(label);
                  return () => input.value.trim();
                },
                confirmKey: "common.save"
              });
              if (!name) return;
              await post("/api/crm/segments/save", {id: segment.id, name, filter: segment.filter});
              await loadCrmSegments();
            }),
            crmButton("common.delete", async () => {
              const ok = await openModal({
                title: t("common.delete"),
                build(el) { el.appendChild(textSpan(t("crm.segment_delete_confirm", {name: segment.name}))); return () => true; },
                confirmKey: "common.delete",
                danger: true
              });
              if (!ok) return;
              await post("/api/crm/segments/delete", {id: segment.id});
              await loadCrmSegments();
            }, "danger")
          );
        }
        card.append(name, count, textSpan(crmFilterSummary(segment.filter || {}), "ux-muted"), actions);
        root.appendChild(card);
      }
      const values = settings.settings || {};
      $("crmAutoCloseDays").value = String(values.autoCloseDays ?? 3);
      if (![...$("crmAutoCloseDays").options].some((o) => o.value === String(values.autoCloseDays))) {
        $("crmAutoCloseDays").appendChild(crmOption(String(values.autoCloseDays), String(values.autoCloseDays), true));
      }
      $("crmAutoAssign").checked = Boolean(values.autoAssignOnReply);
      for (const id of ["crmAutoCloseDays", "crmAutoAssign", "crmSettingsSave"]) $(id).disabled = !settings.canEdit;
    }

    async function saveCrmSettings() {
      await post("/api/crm/settings", {
        autoCloseDays: Number($("crmAutoCloseDays").value) || 0,
        autoAssignOnReply: $("crmAutoAssign").checked
      });
      toast(t("crm.settings_saved"));
    }

    // -- chat list: filters, row badges and the customer panel ---------------
    async function loadCrmMap() {
      const accountId = selectedAccountId();
      if (!accountId || !hasPermission("read")) {
        state.crmMap = {};
        state.crmMapAccountId = "";
        decorateCrmRows();
        return;
      }
      let data;
      try {
        data = await api(`/api/crm/conversations?${accountQuery(accountId)}`);
      } catch (_) {
        return;
      }
      if (accountId !== selectedAccountId()) return;
      state.crmMap = data.conversations || {};
      state.crmMapAccountId = accountId;
      if (data.members) state.crmMembers = data.members;
      if (data.me) state.crmMe = data.me;
      decorateCrmRows();
    }

    function crmRowMatches(mid) {
      const filter = state.crmChatFilter;
      if (filter === "all") return true;
      const info = state.crmMap[mid];
      if (!info) return false;
      if (filter === "mine") return info.assignedTo === state.crmMe && info.status !== "closed";
      if (filter === "unassigned") return !info.assignedTo && info.status !== "closed";
      return info.status === filter;
    }

    function decorateCrmRows() {
      const show = Boolean(selectedAccountId()) && hasPermission("read");
      $("crmChatFilters").classList.toggle("hidden", !show);
      document.querySelectorAll("[data-crm-chat-filter]").forEach((chip) => {
        chip.setAttribute("aria-pressed", String(chip.dataset.crmChatFilter === state.crmChatFilter));
      });
      let mine = 0;
      let unassigned = 0;
      for (const info of Object.values(state.crmMap)) {
        if (info.status === "closed") continue;
        if (info.assignedTo === state.crmMe) mine += 1;
        else if (!info.assignedTo) unassigned += 1;
      }
      $("crmChipMineCount").textContent = mine ? String(mine) : "";
      $("crmChipUnassignedCount").textContent = unassigned ? String(unassigned) : "";
      document.querySelectorAll("#allList .line-row, #contactsList .line-row, #groupsList .line-row").forEach((row) => {
        const mid = row.dataset.mid || "";
        row.classList.toggle("crm-filtered", show && !crmRowMatches(mid));
        const old = row.querySelector(".crm-row-meta");
        if (old) old.remove();
        const info = state.crmMap[mid];
        if (!info) return;
        const meta = document.createElement("span");
        meta.className = "crm-row-meta";
        if (info.status === "new" || info.status === "pending") {
          const dot = document.createElement("span");
          dot.className = "crm-row-dot " + info.status;
          dot.title = t("crm.status_" + info.status);
          meta.appendChild(dot);
        }
        if (info.assignedTo) {
          const name = crmMemberName(info.assignedTo);
          const who = textSpan(crmInitial(name), "crm-assignee" + (info.assignedTo === state.crmMe ? " me" : ""));
          who.title = t("crm.handled_by", {name});
          meta.appendChild(who);
        }
        if (meta.children.length) {
          const side = row.querySelector(".line-row-side");
          if (side) side.prepend(meta);
        }
      });
    }

    function setCrmChatFilter(filter) {
      state.crmChatFilter = filter;
      try { localStorage.setItem(CRM_CHAT_FILTER_KEY, filter); } catch (_) { /* optional */ }
      decorateCrmRows();
    }

    function setCrmSideOpen(open) {
      state.crmSideOpen = Boolean(open) && hasPermission("read");
      try { localStorage.setItem(CRM_SIDE_KEY, state.crmSideOpen ? "on" : "off"); } catch (_) { /* optional */ }
      $("tabPanelLine").classList.toggle("crm-side-open", state.crmSideOpen);
      $("crmPanelToggle").setAttribute("aria-pressed", String(state.crmSideOpen));
      if (state.crmSideOpen) loadCrmSide().catch(() => {});
    }

    async function loadCrmSide() {
      if (!state.crmSideOpen) return;
      const body = $("crmSideBody");
      const accountId = selectedAccountId();
      const mid = state.target;
      if (!accountId || !mid) {
        state.crmSideKey = "";
        body.replaceChildren(textSpan(t("crm.side_pick_chat"), "ux-muted"));
        return;
      }
      const key = `${accountId}:${mid}`;
      const fresh = state.crmSideKey !== key;
      state.crmSideKey = key;
      if (fresh) {
        const row = document.createElement("div");
        row.className = "state-row";
        const spin = document.createElement("span");
        spin.className = "spinner";
        row.append(spin, textSpan(t("common.loading")));
        body.replaceChildren(row);
      }
      let data;
      try {
        data = await api(`/api/crm/chat?${accountQuery(accountId)}&chatId=${encodeURIComponent(mid)}`);
      } catch (err) {
        if (state.crmSideKey === key) body.replaceChildren(textSpan(err.message || t("common.load_failed"), "ux-muted"));
        return;
      }
      if (state.crmSideKey !== key) return;
      state.crmSide = data;
      if (data.members) state.crmMembers = data.members;
      if (data.me) state.crmMe = data.me;
      renderCrmSide();
    }

    function renderCrmSide() {
      const data = state.crmSide;
      const body = $("crmSideBody");
      body.replaceChildren();
      if (!data) return;
      const accountId = selectedAccountId();
      const chatId = state.target;
      const chatName = $("chatTitle").textContent;
      if (!data.supported) {
        body.appendChild(textSpan(t("crm.side_group"), "ux-muted"));
        return;
      }
      const conv = data.conversation;
      const customer = data.customer;
      const target = conv ? {conversationId: conv.id} : {accountId, chatId, name: chatName};
      if (!customer) {
        body.appendChild(textSpan(t("crm.side_pick_chat"), "ux-muted"));
        if (data.canEdit) {
          body.appendChild(crmButton("crm.side_start", () => crmSetStatus({accountId, chatId, name: chatName}, "open"), "primary"));
        }
        return;
      }

      const head = document.createElement("div");
      head.className = "crm-side-name";
      const name = document.createElement("strong");
      name.textContent = customer.displayName || chatName || t("crm.no_name");
      head.append(name, crmChannelBadges(customer.identities));
      if (customer.phone) head.appendChild(textSpan(customer.phone, "ux-muted"));
      head.appendChild(crmButton("crm.open_profile", () => openCustomer(customer.id), "crm-link"));
      body.appendChild(head);

      // Status
      const statusBlock = document.createElement("div");
      const statusTitle = document.createElement("h4");
      statusTitle.textContent = t("crm.side_status");
      const seg = document.createElement("div");
      seg.className = "crm-seg";
      for (const status of CRM_STATUSES) {
        const button = document.createElement("button");
        button.type = "button";
        button.textContent = t("crm.status_" + status);
        button.setAttribute("aria-pressed", String(conv && conv.status === status));
        button.disabled = !data.canEdit;
        button.addEventListener("click", () => crmSetStatus(target, status).catch(toastError));
        seg.appendChild(button);
      }
      statusBlock.append(statusTitle, seg);
      body.appendChild(statusBlock);

      // Owner
      const ownerBlock = document.createElement("div");
      const ownerTitle = document.createElement("h4");
      ownerTitle.textContent = t("crm.side_owner");
      ownerBlock.appendChild(ownerTitle);
      if (data.canEdit && conv) {
        ownerBlock.appendChild(crmAssigneeSelect(conv, data, (assignee) => crmAssign(target, assignee)));
      } else {
        ownerBlock.appendChild(textSpan(conv && conv.assignedTo ? crmMemberName(conv.assignedTo) : t("crm.nobody")));
      }
      if (data.canEdit && (!conv || conv.assignedTo !== state.crmMe)) {
        const claim = crmButton("crm.claim", () => crmAssign(target, "me").then(() => toast(t("crm.claimed"))), "primary");
        claim.style.marginTop = "6px";
        claim.style.width = "100%";
        ownerBlock.appendChild(claim);
      }
      body.appendChild(ownerBlock);

      // Tags
      const tagBlock = document.createElement("div");
      const tagTitle = document.createElement("h4");
      tagTitle.textContent = t("crm.sec_tags");
      const tags = document.createElement("div");
      tags.className = "crm-tags";
      for (const tag of customer.tags || []) {
        tags.appendChild(crmTagChip(tag, data.canEdit ? (item) => crmTagCustomer(customer.id, {remove: [item.id]}).then(loadCrmSide) : null));
      }
      tagBlock.append(tagTitle, tags);
      if (data.canEdit) {
        const input = crmTagInput("crm.tag_add_ph");
        input.style.marginTop = "6px";
        input.addEventListener("keydown", (event) => {
          if (event.key !== "Enter" || !input.value.trim()) return;
          const value = input.value.trim();
          input.value = "";
          crmTagCustomer(customer.id, {add: [{name: value}]}).then(loadCrmSide).catch(toastError);
        });
        input.addEventListener("change", () => {
          const value = input.value.trim();
          if (!value || !state.crmTags.some((tag) => tag.name === value)) return;
          input.value = "";
          crmTagCustomer(customer.id, {add: [{name: value}]}).then(loadCrmSide).catch(toastError);
        });
        tagBlock.appendChild(input);
      }
      body.appendChild(tagBlock);

      // Follow-ups
      const taskBlock = document.createElement("div");
      const taskTitle = document.createElement("h4");
      taskTitle.textContent = t("crm.sec_tasks");
      taskBlock.appendChild(taskTitle);
      for (const task of data.tasks || []) taskBlock.appendChild(crmTaskRow(task, {afterChange: loadCrmSide}));
      if (data.canEdit) {
        taskBlock.appendChild(crmButton("crm.add_task", () => crmAddTask(conv ? {customerId: customer.id} : target, loadCrmSide), "crm-link"));
      }
      body.appendChild(taskBlock);

      // Notes
      const noteBlock = document.createElement("div");
      const noteTitle = document.createElement("h4");
      noteTitle.textContent = t("crm.sec_notes");
      noteBlock.appendChild(noteTitle);
      if (data.canEdit) {
        const area = document.createElement("textarea");
        area.rows = 2;
        area.placeholder = t("crm.quick_note_ph");
        area.maxLength = 5000;
        const save = crmButton("crm.save_note", async () => {
          if (!area.value.trim()) return;
          await post("/api/crm/notes/create", {customerId: customer.id, body: area.value.trim()});
          toast(t("crm.note_saved"));
          await loadCrmSide();
        });
        save.style.marginTop = "6px";
        noteBlock.append(area, save);
      }
      for (const note of data.notes || []) noteBlock.appendChild(crmNoteItem(note, data.canManage, loadCrmSide));
      body.appendChild(noteBlock);
    }

    async function crmAssign(target, assignee) {
      const data = await post("/api/crm/chat/assign", {...target, assignee});
      crmAfterChange(data);
    }

    async function crmSetStatus(target, status) {
      const data = await post("/api/crm/chat/status", {...target, status});
      crmAfterChange(data);
    }

    // After a change in the panel or the card, refresh what shows it.
    function crmAfterChange(panelData) {
      if (panelData && panelData.supported !== undefined && state.crmSideKey) {
        state.crmSide = panelData;
        renderCrmSide();
      } else if (state.crmSideOpen) {
        loadCrmSide().catch(() => {});
      }
      loadCrmMap().catch(() => {});
      loadCrmSummary();
      if (state.tab === "customers" && state.crmView === "list") loadCustomers().catch(() => {});
    }

    async function openCrmChat(accountId, chatId, name) {
      closeCustomerDrawer();
      if (accountId && accountId !== selectedAccountId()) {
        $("accountSelect").value = accountId;
        await selectAccount();
      }
      setTab("line");
      setTarget(chatId, name);
      await loadMessages();
    }

    function scheduleCrmRefresh() {
      clearTimeout(state.crmRefreshTimer);
      state.crmRefreshTimer = setTimeout(() => {
        loadCrmMap().catch(() => {});
        if (state.crmSideOpen) loadCrmSide().catch(() => {});
        if (state.tab === "customers" && state.crmView === "list") loadCustomers().catch(() => {});
        if (state.crmDrawerId) refreshCustomerDrawer().catch(() => {});
        loadCrmSummary();
      }, 400);
    }

    // -- hooks into the existing screens -------------------------------------
    const baseSetTabForCrm = setTab;
    setTab = function (tab) {
      baseSetTabForCrm(tab);
      if (state.tab === "customers") setCrmView(state.crmView || "list");
    };
    const baseSetTargetForCrm = setTarget;
    setTarget = function (mid, label) {
      baseSetTargetForCrm(mid, label);
      if (state.crmSideOpen) loadCrmSide().catch(() => {});
    };
    const baseRenderAllListForCrm = renderAllList;
    renderAllList = function () { baseRenderAllListForCrm(); decorateCrmRows(); };
    const baseRenderContactsListForCrm = renderContactsList;
    renderContactsList = function () { baseRenderContactsListForCrm(); decorateCrmRows(); };
    const baseRenderGroupsListForCrm = renderGroupsList;
    renderGroupsList = function () { baseRenderGroupsListForCrm(); decorateCrmRows(); };
    const baseStartEventStreamForCrm = startEventStream;
    startEventStream = function () {
      const before = state.eventSource;
      baseStartEventStreamForCrm();
      const source = state.eventSource;
      if (source && source !== before) source.addEventListener("crm_updated", scheduleCrmRefresh);
    };
    const baseRenderProfileForCrm = renderProfile;
    renderProfile = function (status) {
      baseRenderProfileForCrm(status);
      const accountId = selectedAccountId();
      if (status && status.authenticated && accountId && state.crmMapAccountId !== accountId) {
        state.crmMap = {};
        loadCrmMap().catch(() => {});
        loadCrmSummary();
      }
      setCrmSideOpen(state.crmSideOpen);
    };
    const baseApplyPermissionsForCrm = applyPermissions;
    applyPermissions = function () {
      baseApplyPermissionsForCrm();
      $("tabCustomers").classList.toggle("hidden", !hasPermission("read"));
      $("crmPanelToggle").classList.toggle("hidden", !hasPermission("read"));
    };

    document.querySelectorAll("[data-crm-view]").forEach((button) => {
      button.addEventListener("click", () => setCrmView(button.dataset.crmView));
    });
    document.querySelectorAll("[data-crm-chat-filter]").forEach((chip) => {
      chip.addEventListener("click", () => setCrmChatFilter(chip.dataset.crmChatFilter));
    });
    $("crmGuideButton").addEventListener("click", () => setCrmView("guide"));
    $("crmGuideBack").addEventListener("click", () => setCrmView("list"));
    $("crmSearch").addEventListener("input", () => {
      clearTimeout(state.crmSearchTimer);
      state.crmSearchTimer = setTimeout(() => {
        state.crmPage = 1;
        loadCustomers().catch(toastError);
      }, 300);
    });
    for (const id of ["crmFilterStatus", "crmFilterAssignee", "crmFilterTag", "crmFilterChannel", "crmFilterLast"]) {
      $(id).addEventListener("change", () => {
        state.crmPage = 1;
        state.crmSegment = null;
        loadCustomers().catch(toastError);
      });
    }
    $("crmSegmentClear").addEventListener("click", () => {
      state.crmSegment = null;
      state.crmPage = 1;
      loadCustomers().catch(toastError);
    });
    $("crmPrevPage").addEventListener("click", () => {
      state.crmPage = Math.max(1, state.crmPage - 1);
      loadCustomers().catch(toastError);
    });
    $("crmNextPage").addEventListener("click", () => {
      state.crmPage += 1;
      loadCustomers().catch(toastError);
    });
    $("crmSelectAll").addEventListener("change", () => {
      for (const customer of state.crmCustomers) {
        if ($("crmSelectAll").checked) state.crmSelected.add(customer.id);
        else state.crmSelected.delete(customer.id);
      }
      renderCustomers();
    });
    $("crmBulkTag").addEventListener("click", () => crmBulkTag().catch(toastError));
    $("crmBulkUntag").addEventListener("click", () => crmBulkUntag().catch(toastError));
    $("crmBulkMerge").addEventListener("click", () => crmBulkMerge().catch(toastError));
    $("crmBulkClear").addEventListener("click", () => { state.crmSelected.clear(); renderCustomers(); });
    $("crmAddCustomerButton").addEventListener("click", () => crmAddCustomer().catch(toastError));
    $("crmSaveSegmentButton").addEventListener("click", () => crmSaveSegment().catch(toastError));
    $("crmExportButton").addEventListener("click", crmExport);
    $("crmTaskScope").addEventListener("change", () => loadCrmTasks().catch(toastError));
    $("crmTaskStatus").addEventListener("change", () => loadCrmTasks().catch(toastError));
    $("crmAddTaskButton").addEventListener("click", () => crmAddTask({}).catch(toastError));
    $("crmSettingsSave").addEventListener("click", () => saveCrmSettings().catch(toastError));
    $("crmScrim").addEventListener("click", closeCustomerDrawer);
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && state.crmDrawerId && $("modalOverlay").classList.contains("hidden")) {
        closeCustomerDrawer();
      }
    });
    $("crmPanelToggle").addEventListener("click", () => setCrmSideOpen(!state.crmSideOpen));
    $("crmSideClose").addEventListener("click", () => setCrmSideOpen(false));
    setInterval(loadCrmSummary, 60000);
