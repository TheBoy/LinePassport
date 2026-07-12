"""Interactive, menu-driven OkLine console — the full client by number.

Run ``okline`` with no arguments (or ``okline menu``) for a soft-coloured
terminal UI: a categorised, numbered menu you drive by typing numbers, no
commands to memorise.  On first use it goes straight to QR login and saves the
session.  Every CLI capability is reachable here.

The UI ships in **Thai by default** (with an English fallback) so a first-time,
non-technical user can succeed with no instruction.  Switch language from the
root menu ("ภาษา / Language") or by setting ``OKLINE_LANG=en``.  Instead of ever
forcing a raw ``mid`` / message id, targets are chosen from a numbered picker
(typing a name or a raw id still works as a fallback).
"""

from __future__ import annotations

import json
import os
from typing import Any, Callable, Union

from . import ui
from ._util import is_mid, reconfigure_stdout_utf8
from .entities import Group

# a menu item is either a leaf (label, action) or a submenu (label, [items])
Action = Callable[[Any], None]
Item = tuple[str, Union[Action, "list[Item]"]]


# ---------------------------------------------------------------------------
# i18n — tiny message catalogue (id -> {th, en}); Thai is the default
# ---------------------------------------------------------------------------
def _env_lang() -> str:
    v = (os.environ.get("OKLINE_LANG") or "").strip().lower()
    return "en" if v.startswith("en") else "th"


#: The currently-active UI language ("th" or "en").  Toggled at runtime by
#: :func:`act_language`; seeded from ``OKLINE_LANG`` at import time.
_LANG = _env_lang()

_I18N: dict[str, dict[str, str]] = {
    # -- app / navigation ---------------------------------------------------
    "app.title": {
        "th": "OkLine · LINE ในเทอร์มินัลของคุณ",
        "en": "OkLine · LINE in your terminal",
    },
    "menu.choose": {"th": "เลือก", "en": "choose"},
    "menu.invalid": {
        "th": "พิมพ์เลขจากรายการ หรือ 0 เพื่อกลับ",
        "en": "type a number from the list, or 0 to go back",
    },
    "ui.quit": {"th": "ออก", "en": "Quit"},
    "ui.back": {"th": "กลับ", "en": "Back"},
    "ui.pause": {"th": "กด Enter เพื่อไปต่อ…", "en": "press Enter to continue…"},
    "msg.bye": {"th": "ลาก่อน", "en": "bye"},
    "msg.no_session_bye": {"th": "ไม่มีเซสชัน — ลาก่อน", "en": "no session — bye."},
    "msg.cancelled": {"th": "ยกเลิกแล้ว", "en": "cancelled"},
    # -- sections -----------------------------------------------------------
    "sec.me": {"th": "ฉัน & บัญชี", "en": "Me & account"},
    "sec.contacts": {"th": "รายชื่อ & ผู้คน", "en": "Contacts & people"},
    "sec.groups": {"th": "กลุ่ม & แชท", "en": "Groups & chats"},
    "sec.send": {"th": "ส่งข้อความ", "en": "Send a message"},
    "sec.read": {"th": "อ่าน & ประวัติ", "en": "Read & history"},
    "sec.live": {"th": "สด & บอท", "en": "Live & bots"},
    "sec.e2ee": {"th": "การเข้ารหัส", "en": "Encryption (Letter Sealing)"},
    "sec.dev": {
        "th": "ขั้นสูง (สำหรับนักพัฒนา)",
        "en": "Advanced (for developers)",
    },
    "sec.language": {"th": "ภาษา / Language", "en": "ภาษา / Language"},
    # -- actions: me --------------------------------------------------------
    "act.whoami": {"th": "ฉันคือใคร · สถิติ", "en": "Who am I · stats"},
    "act.my_profile": {"th": "โปรไฟล์ของฉัน", "en": "My profile"},
    "act.set_name": {"th": "ตั้งชื่อที่แสดง", "en": "Set display name"},
    "act.set_status": {"th": "ตั้งข้อความสถานะ", "en": "Set status message"},
    "act.settings": {"th": "ตั้งค่าบัญชี", "en": "Account settings"},
    "act.logout": {
        "th": "ออกจากระบบ (+ ลบเซสชัน)",
        "en": "Log out (+ delete session)",
    },
    # -- actions: contacts --------------------------------------------------
    "act.contacts": {"th": "แสดง / ค้นหารายชื่อ", "en": "List / search contacts"},
    "act.find": {"th": "ค้นหารายชื่อตามชื่อ", "en": "Find a contact by name"},
    "act.profile_of": {"th": "ดูโปรไฟล์เพื่อน", "en": "Profile of a contact"},
    "act.add_friend": {"th": "เพิ่มเพื่อน", "en": "Add a friend"},
    "act.search_user": {"th": "ค้นหาผู้ใช้ด้วย LINE ID", "en": "Search a user by LINE ID"},
    "act.block": {"th": "บล็อก / ปลดบล็อก", "en": "Block / unblock"},
    "act.favorites": {"th": "รายการโปรด", "en": "Favorites"},
    "act.export": {"th": "ส่งออกรายชื่อ (CSV/JSON)", "en": "Export contacts (CSV/JSON)"},
    # -- actions: groups ----------------------------------------------------
    "act.groups": {"th": "กลุ่มของฉัน", "en": "List my groups"},
    "act.members": {"th": "สมาชิกในกลุ่ม", "en": "Group members"},
    "act.leave": {"th": "ออกจากกลุ่ม", "en": "Leave a group"},
    "act.accept": {"th": "ยอมรับคำเชิญ", "en": "Accept an invitation"},
    "act.boxes": {"th": "กล่องข้อความ", "en": "Message boxes"},
    # -- actions: send ------------------------------------------------------
    "act.send_text": {"th": "ข้อความ", "en": "Text"},
    "act.send_sticker": {"th": "สติกเกอร์", "en": "Sticker"},
    "act.send_location": {"th": "ตำแหน่งที่ตั้ง", "en": "Location"},
    "act.send_media": {"th": "รูปภาพ / ไฟล์", "en": "Image / file"},
    "act.reply": {"th": "ตอบกลับข้อความ", "en": "Reply to a message"},
    "act.react": {"th": "แสดงความรู้สึกต่อข้อความ", "en": "React to a message"},
    "act.unsend": {"th": "ยกเลิกการส่งข้อความ", "en": "Unsend a message"},
    "act.broadcast": {"th": "ส่งถึงหลายคน (broadcast)", "en": "Broadcast to several"},
    # -- actions: read ------------------------------------------------------
    "act.chatlog": {"th": "ประวัติแชท (ถอดรหัส E2EE)", "en": "Chat log (decrypts E2EE)"},
    "act.recent": {"th": "ข้อความล่าสุด", "en": "Recent messages"},
    "act.search_msgs": {"th": "ค้นหาข้อความในแชท", "en": "Search messages in a chat"},
    "act.backup": {"th": "สำรองแชทเป็นไฟล์ JSON", "en": "Back up a chat to JSON"},
    # -- actions: live ------------------------------------------------------
    "act.watch": {"th": "ดูข้อความเข้า (สด)", "en": "Watch incoming (live)"},
    "act.autoreply": {"th": "บอทตอบกลับอัตโนมัติ", "en": "Auto-reply bot"},
    "act.notify": {"th": "แจ้งเตือนตามคำสำคัญ", "en": "Keyword notifier"},
    # -- actions: e2ee ------------------------------------------------------
    "act.e2ee_status": {"th": "สถานะการเข้ารหัส", "en": "Encryption status"},
    "act.e2ee_send": {"th": "ส่งข้อความเข้ารหัส", "en": "Send an encrypted message"},
    "act.e2ee_decrypt": {
        "th": "ถอดรหัสข้อความล่าสุดในแชท",
        "en": "Decrypt a chat's latest sealed message",
    },
    "act.e2ee_roundtrip": {"th": "ทดสอบเข้ารหัส (round-trip)", "en": "Round-trip self-test"},
    # -- actions: dev -------------------------------------------------------
    "act.call": {"th": "เรียก endpoint ใดก็ได้", "en": "Call any endpoint"},
    "act.list_endpoints": {"th": "รายการ endpoints", "en": "List endpoints"},
    "act.selftest": {"th": "ทดสอบตัวเอง (อ่านอย่างเดียว)", "en": "Self-test (read-only)"},
    "act.recording": {"th": "บันทึก / เซฟ log", "en": "Recording / save log"},
    # -- generic prompts ----------------------------------------------------
    "prompt.target": {
        "th": "ส่งถึงใคร (พิมพ์เลข, ชื่อ หรือไอดี)",
        "en": "send to (number, name or id)",
    },
    "prompt.message_text": {"th": "ข้อความ", "en": "message"},
    "prompt.choose": {"th": "เลือกหมายเลข (0 = ยกเลิก)", "en": "choose a number (0 = cancel)"},
    "prompt.choose_option": {"th": "เลือก (0 = ยกเลิก)", "en": "choose (0 = cancel)"},
    "prompt.new_name": {"th": "ชื่อที่แสดงใหม่", "en": "new display name"},
    "prompt.new_status": {"th": "ข้อความสถานะใหม่", "en": "new status message"},
    "prompt.find_name": {"th": "ชื่อที่จะค้นหา", "en": "name to find"},
    "prompt.contact_filter": {
        "th": "กรองตามชื่อ (เว้นว่าง = ทั้งหมด)",
        "en": "filter by name (blank = all)",
    },
    "prompt.profile_of": {
        "th": "เลือกเพื่อน (เลข/ชื่อ/ไอดี)",
        "en": "pick a contact (number/name/id)",
    },
    "prompt.add_friend": {"th": "ไอดี LINE หรือ mid (U…)", "en": "LINE ID or mid (U…)"},
    "prompt.search_userid": {"th": "ไอดี LINE (เช่น nb.vtg)", "en": "LINE ID (e.g. nb.vtg)"},
    "prompt.add_as_friend": {"th": "เพิ่มเป็นเพื่อนไหม?", "en": "add as friend?"},
    "prompt.export_format": {"th": "รูปแบบ (csv/json)", "en": "format (csv/json)"},
    "prompt.output_file": {"th": "ไฟล์ปลายทาง", "en": "output file"},
    "prompt.latitude": {"th": "ละติจูด", "en": "latitude"},
    "prompt.longitude": {"th": "ลองจิจูด", "en": "longitude"},
    "prompt.title": {"th": "หัวข้อ", "en": "title"},
    "prompt.package_id": {"th": "package id", "en": "package id"},
    "prompt.sticker_id": {"th": "sticker id", "en": "sticker id"},
    "prompt.media_path": {"th": "พาธของรูป/ไฟล์", "en": "path to image/file"},
    "prompt.pick_chat": {"th": "เลือกแชท (เลข/ชื่อ/ไอดี)", "en": "pick a chat (number/name/id)"},
    "prompt.pick_message": {"th": "เลือกข้อความ (เลข/ไอดี)", "en": "pick a message (number/id)"},
    "prompt.reply_text": {"th": "ข้อความตอบกลับ", "en": "reply text"},
    "prompt.broadcast_text": {"th": "ข้อความที่จะกระจาย", "en": "message to broadcast"},
    "prompt.broadcast_targets": {
        "th": "ผู้รับ (คั่นด้วยช่องว่าง: ชื่อ/ไอดี)",
        "en": "targets (space-separated names/ids)",
    },
    "prompt.how_many": {"th": "จำนวนกี่ข้อความ", "en": "how many"},
    "prompt.keyword": {"th": "คำค้น", "en": "keyword"},
    "prompt.watch_echo": {"th": "ตอบกลับอัตโนมัติไหม?", "en": "echo replies?"},
    "prompt.autoreply_rule": {"th": "กติกา", "en": "rule"},
    "prompt.notify_keyword": {
        "th": "คำที่จะแจ้งเตือน (เว้นว่าง = ทั้งหมด)",
        "en": "alert keyword (blank = all)",
    },
    "prompt.endpoint": {
        "th": "endpoint (Namespace.Service.method)",
        "en": "endpoint (Namespace.Service.method)",
    },
    "prompt.endpoint_args": {"th": "args เป็น JSON array", "en": "args as JSON array"},
    "prompt.endpoint_filter": {
        "th": "กรอง (เว้นว่าง = ทั้งหมด)",
        "en": "filter (blank = all)",
    },
    "prompt.rec_format": {"th": "รูปแบบ (text/json/har)", "en": "format (text/json/har)"},
    "prompt.rec_file": {"th": "ไฟล์", "en": "file"},
    "prompt.log_save": {"th": "บันทึก log ไหม?", "en": "save a log?"},
    # -- sub-menus (block / favorites) --------------------------------------
    "block.title": {"th": "บล็อก / ปลดบล็อก", "en": "Block / unblock"},
    "block.list": {"th": "ดูรายการที่บล็อก", "en": "List blocked"},
    "block.add": {"th": "บล็อกคนใหม่", "en": "Block someone"},
    "block.remove": {"th": "ปลดบล็อก", "en": "Unblock"},
    "fav.title": {"th": "รายการโปรด", "en": "Favorites"},
    "fav.list": {"th": "ดูรายการโปรด", "en": "List favorites"},
    "fav.add": {"th": "เพิ่มรายการโปรด", "en": "Add a favorite"},
    "fav.remove": {"th": "นำออกจากรายการโปรด", "en": "Remove a favorite"},
    "prompt.block_target": {"th": "เลือกคนที่จะบล็อก", "en": "who to block"},
    "prompt.unblock_target": {"th": "เลือกคนที่จะปลดบล็อก", "en": "who to unblock"},
    "prompt.fav_target": {"th": "เลือกเพื่อน/แชท", "en": "which contact/chat"},
    # -- picker -------------------------------------------------------------
    "pick.none_available": {"th": "(ไม่มีให้เลือก)", "en": "(nothing to choose)"},
    "pick.more": {
        "th": "…และอีก {n} รายการ (พิมพ์ชื่อเพื่อค้นหา)",
        "en": "…and {n} more (type a name to search)",
    },
    "pick.out_of_range": {"th": "หมายเลขไม่อยู่ในรายการ", "en": "that number isn't in the list"},
    "pick.no_match": {"th": "ไม่พบ {q}", "en": "nothing matching {q}"},
    "pick.many_match": {"th": "พบ {n} รายการ: ", "en": "{n} matches: "},
    "pick.no_groups": {"th": "ยังไม่มีกลุ่มที่นี่", "en": "no groups here"},
    "pick.chosen": {"th": "เลือก", "en": "chosen"},
    # -- stickers -----------------------------------------------------------
    "sticker.pick": {"th": "เลือกสติกเกอร์", "en": "Pick a sticker"},
    "sticker.manual": {"th": "หรือใส่ id เอง", "en": "or enter ids manually"},
    "sticker.hi": {"th": "สวัสดี", "en": "Hi"},
    "sticker.haha": {"th": "หัวเราะ", "en": "Haha"},
    "sticker.love": {"th": "หัวใจ / รัก", "en": "Love"},
    "sticker.ok": {"th": "โอเค", "en": "OK"},
    "sticker.thanks": {"th": "ขอบคุณ", "en": "Thanks"},
    # -- reactions ----------------------------------------------------------
    "react.pick": {"th": "เลือกความรู้สึก", "en": "Pick a reaction"},
    "react.nice": {"th": "ถูกใจ", "en": "Nice"},
    "react.love": {"th": "รัก", "en": "Love"},
    "react.fun": {"th": "สนุก", "en": "Fun"},
    "react.amazing": {"th": "สุดยอด", "en": "Amazing"},
    "react.sad": {"th": "เศร้า", "en": "Sad"},
    "react.omg": {"th": "ตกใจ", "en": "OMG"},
    # -- confirmations ------------------------------------------------------
    "confirm.add_friend": {"th": "เพิ่ม {name} เป็นเพื่อน?", "en": "add {name} as a friend?"},
    "confirm.block": {"th": "บล็อก {name}?", "en": "block {name}?"},
    "confirm.unsend": {
        "th": "ยกเลิกการส่ง (ลบให้ทุกคน)?",
        "en": "unsend (delete for everyone)?",
    },
    "confirm.leave": {"th": "ออกจาก {name}?", "en": "leave {name}?"},
    "confirm.logout": {
        "th": "ออกจากระบบและลบเซสชันจริงหรือ?",
        "en": "really log out + delete the session?",
    },
    "confirm.broadcast": {"th": "ส่งถึง {n} คน?", "en": "send to {n} target(s)?"},
    "confirm.overwrite": {
        "th": "มีไฟล์ {path} อยู่แล้ว เขียนทับ?",
        "en": "{path} exists — overwrite?",
    },
    # -- result / status messages -------------------------------------------
    "msg.sent": {"th": "ส่งแล้ว", "en": "sent"},
    "msg.added": {"th": "เพิ่ม {name} แล้ว", "en": "added {name}"},
    "msg.blocked": {"th": "บล็อกแล้ว", "en": "blocked"},
    "msg.unblocked": {"th": "ปลดบล็อกแล้ว", "en": "unblocked"},
    "msg.favorited": {"th": "เพิ่มเป็นรายการโปรดแล้ว", "en": "favorited"},
    "msg.unfavorited": {"th": "นำออกจากรายการโปรดแล้ว", "en": "unfavorited"},
    "msg.not_found": {"th": "ไม่พบ", "en": "not found"},
    "msg.reacted": {"th": "แสดงความรู้สึกแล้ว", "en": "reacted"},
    "msg.unsent": {"th": "ยกเลิกการส่งแล้ว", "en": "unsent"},
    "msg.left": {"th": "ออกจากกลุ่มแล้ว", "en": "left"},
    "msg.accepted": {"th": "ยอมรับคำเชิญแล้ว", "en": "accepted"},
    "msg.name_set": {"th": "ตั้งชื่อที่แสดงเป็น {name}", "en": "display name → {name}"},
    "msg.status_set": {"th": "ตั้งสถานะเป็น {status}", "en": "status → {status}"},
    "msg.lang_set": {"th": "เปลี่ยนภาษาแล้ว", "en": "language changed"},
    "msg.saved_n": {"th": "บันทึก {n} ข้อความ → {path}", "en": "saved {n} messages → {path}"},
    "msg.wrote_contacts": {
        "th": "เขียน {n} รายชื่อ → {path}",
        "en": "wrote {n} contacts → {path}",
    },
    "msg.saved_log": {"th": "บันทึกแล้ว → {path}", "en": "saved → {path}"},
    "msg.removed_session": {"th": "ลบไฟล์ {path} แล้ว", "en": "removed {path}"},
    "msg.relogin_hint": {
        "th": "เปิด `okline` อีกครั้งเพื่อเข้าสู่ระบบใหม่",
        "en": "restart `okline` to log in again.",
    },
    "msg.server_logout_failed": {
        "th": "ออกจากระบบฝั่งเซิร์ฟเวอร์ล้มเหลว: {err}",
        "en": "server logout failed: {err}",
    },
    "msg.no_encrypted": {
        "th": "ไม่พบข้อความเข้ารหัสในแชทนั้น",
        "en": "no encrypted message found in that chat",
    },
    "msg.text_is": {"th": "ข้อความ = {text}", "en": "text = {text}"},
    "msg.recovered": {"th": "กู้คืนได้ = {text}", "en": "recovered = {text}"},
    "msg.no_buddy_detail": {
        "th": "(ไม่มีรายละเอียดเพิ่มเติม: {err})",
        "en": "(no buddy detail: {err})",
    },
    "msg.n_contacts": {"th": "{n} รายชื่อ", "en": "{n} contact(s)"},
    "msg.n_matches": {"th": "พบ {n} รายการ", "en": "{n} match(es)"},
    "msg.rules_hint": {
        "th": "ใส่กติกาแบบ  คำสำคัญ=คำตอบ  (บรรทัดว่างเพื่อเริ่ม)",
        "en": "enter rules as  keyword=reply  (blank line to start)",
    },
    "msg.watching": {"th": "กำลังเฝ้าดู", "en": "watching"},
    "msg.autoreply": {"th": "ตอบอัตโนมัติ ({n} กติกา)", "en": "auto-reply ({n} rules)"},
    "msg.notifying": {"th": "กำลังแจ้งเตือน", "en": "notifying"},
    "msg.bot_hint": {"th": "{banner}  (Ctrl-C เพื่อกลับ)", "en": "{banner}  (Ctrl-C to return)"},
    "msg.matched_replied": {"th": "ตรงกับ {kw} → ตอบแล้ว", "en": "matched {kw} → replied"},
    "msg.n_recorded": {
        "th": "บันทึกไว้ {n} รายการในเซสชันนี้",
        "en": "{n} exchange(s) recorded this session",
    },
    "msg.n_endpoints": {"th": "{n} endpoint(s)", "en": "{n} endpoint(s)"},
    "msg.n_failures": {"th": "{n} รายการล้มเหลว", "en": "{n} failure(s)"},
    "msg.non_text": {"th": "<ไม่ใช่ข้อความ>", "en": "<non-text>"},
    "msg.encrypted": {"th": "[เข้ารหัส]", "en": "[encrypted]"},
    "msg.encrypted_login": {
        "th": "[เข้ารหัส — เข้าสู่ระบบเพื่อโหลดกุญแจ]",
        "en": "[encrypted — log in to load keys]",
    },
    # -- errors -------------------------------------------------------------
    "err.not_number": {"th": "กรุณาใส่ตัวเลข", "en": "please enter a number"},
    "err.auth": {
        "th": "เซสชันหมดอายุ — กรุณาเข้าสู่ระบบใหม่",
        "en": "your session expired — please log in again",
    },
    "err.network": {
        "th": "เชื่อมต่อไม่ได้ — ตรวจสอบอินเทอร์เน็ตแล้วลองใหม่",
        "en": "couldn't connect — check your internet and try again",
    },
    "err.not_found": {"th": "ไม่พบสิ่งที่ต้องการ", "en": "not found"},
    "err.invalid_mid": {"th": "เพื่อน/แชทไม่ถูกต้อง", "en": "that contact/chat isn't valid"},
    "err.not_member": {"th": "คุณไม่ได้อยู่ในกลุ่มนี้", "en": "you're not a member of that chat"},
    "err.not_friend": {
        "th": "ต้องเป็นเพื่อนกันก่อนจึงจะทำสิ่งนี้ได้",
        "en": "you need to be friends first",
    },
    "err.rate_limited": {
        "th": "ส่งถี่เกินไป — พักสักครู่แล้วลองใหม่",
        "en": "too many requests — please wait a bit and retry",
    },
    "err.msg_not_found": {"th": "ไม่พบข้อความนั้น", "en": "that message was not found"},
    "err.api": {"th": "ทำรายการไม่สำเร็จ: {detail}", "en": "that didn't work: {detail}"},
    "err.generic": {"th": "เกิดข้อผิดพลาด: {detail}", "en": "something went wrong: {detail}"},
    "err.unknown_reaction": {"th": "ไม่รู้จักความรู้สึกนี้", "en": "unknown reaction"},
    "err.unknown_endpoint": {
        "th": "ไม่รู้จัก endpoint นี้ — ใช้ 'รายการ endpoints'",
        "en": "unknown endpoint — use 'list endpoints'",
    },
    "err.bad_json": {"th": "JSON ไม่ถูกต้อง: {err}", "en": "bad JSON: {err}"},
    "err.e2ee_not_loaded": {
        "th": "ยังไม่ได้โหลดกุญแจเข้ารหัส — เข้าสู่ระบบก่อน",
        "en": "encryption keys not loaded — log in first",
    },
    # -- whoami / e2ee labels ----------------------------------------------
    "kv.name": {"th": "ชื่อ", "en": "name"},
    "kv.mid": {"th": "mid", "en": "mid"},
    "kv.userid": {"th": "LINE ID", "en": "user id"},
    "kv.status": {"th": "สถานะ", "en": "status"},
    "kv.contacts": {"th": "รายชื่อ", "en": "contacts"},
    "kv.groups": {"th": "กลุ่ม", "en": "groups"},
    "kv.favorites": {"th": "รายการโปรด", "en": "favorites"},
    "kv.blocked": {"th": "ที่บล็อก", "en": "blocked"},
    "kv.e2ee": {"th": "การเข้ารหัส", "en": "e2ee"},
    "kv.groups_fmt": {"th": "{n} (+{inv} คำเชิญ)", "en": "{n} (+{inv} invited)"},
    "e2ee.on": {"th": "พร้อม", "en": "ready"},
    "e2ee.off": {"th": "ปิด", "en": "off"},
    "e2ee.ready": {"th": "พร้อมใช้งาน", "en": "ready"},
    "e2ee.keys_loaded": {"th": "กุญแจที่โหลด", "en": "keys loaded"},
    "e2ee.latest_key": {"th": "รหัสกุญแจล่าสุด", "en": "latest key id"},
    "e2ee.my_mid": {"th": "mid ของฉัน", "en": "my mid"},
    "e2ee.yes": {"th": "ใช่", "en": "yes"},
    "e2ee.no": {"th": "ไม่", "en": "no"},
    "e2ee.load_hint": {
        "th": "เข้าสู่ระบบด้วย QR (เปิดใหม่) เพื่อโหลดกุญแจเข้ารหัส",
        "en": "log in with the menu's QR (restart) to load E2EE keys.",
    },
    "e2ee.peer_prompt": {"th": "เพื่อน (mid หรือชื่อ)", "en": "peer (mid or name)"},
    # -- group displays -----------------------------------------------------
    "grp.member_invited": {"th": "สมาชิก {m}   คำเชิญ {i}", "en": "member {m}   invited {i}"},
    "grp.n_members": {"th": "({n} สมาชิก)", "en": "({n} members)"},
    "grp.not_found": {"th": "ไม่พบกลุ่ม", "en": "group not found"},
    "kind.group": {"th": "กลุ่ม", "en": "group"},
    # -- header -------------------------------------------------------------
    "hdr.e2ee": {"th": "การเข้ารหัส ", "en": "e2ee "},
    # -- login --------------------------------------------------------------
    "login.node_required": {
        "th": "ต้องใช้ Node.js 18+ เพื่อลงลายเซ็นคำขอ (X-Hmac)",
        "en": "Node.js 18+ is required to sign requests (X-Hmac).",
    },
    "login.node_hint": {
        "th": "ติดตั้งจาก https://nodejs.org รัน `node --version` แล้วลองใหม่",
        "en": "Install from https://nodejs.org, run `node --version`, then retry.",
    },
    "login.qr_scan": {"th": "สแกน QR นี้ด้วยแอป LINE", "en": "Scan this QR with the LINE app"},
    "login.qr_hint": {"th": "  (เพิ่มเพื่อน › QR)", "en": "  (Add friends › QR)"},
    "login.no_qrcode": {
        "th": "ยังไม่ได้ติดตั้ง qrcode — เปิดลิงก์นี้บนมือถือ หรือ `pip install qrcode` เพื่อแสดง QR",
        "en": "qrcode not installed — open this link on your phone, or `pip install qrcode` to show a QR",
    },
    "login.confirm_pin": {"th": "ยืนยัน PIN นี้: {pin}", "en": "Confirm this PIN: {pin}"},
    "login.saved": {
        "th": "เข้าสู่ระบบแล้ว — บันทึกเซสชันที่ {path}",
        "en": "Logged in — session saved to {path}.",
    },
    "login.failed": {"th": "เข้าสู่ระบบล้มเหลว: {err}", "en": "login failed: {err}"},
    "login.incomplete": {"th": "การเข้าสู่ระบบไม่สมบูรณ์", "en": "login did not complete."},
    "login.no_session": {
        "th": "ยังไม่มีเซสชัน — เริ่มเข้าสู่ระบบด้วย QR…",
        "en": "No saved session — starting QR login…",
    },
}


def t(key: str, **kw: Any) -> str:
    """Translate ``key`` into the active language, with ``{name}`` interpolation.

    Falls back to English, then to the raw key, so a missing translation is
    never fatal.  ``**kw`` fills ``str.format`` placeholders.
    """
    entry = _I18N.get(key)
    s = key if entry is None else (entry.get(_LANG) or entry.get("en") or key)
    return s.format(**kw) if kw else s


# A handful of friendly, always-free stickers (package 11537) offered by name
# so the user never has to know a numeric package/sticker id.
_STICKERS: list[tuple[str, str, str]] = [
    ("sticker.hi", "11537", "52002734"),
    ("sticker.haha", "11537", "52002735"),
    ("sticker.love", "11537", "52002736"),
    ("sticker.ok", "11537", "52002739"),
    ("sticker.thanks", "11537", "52002744"),
]

# Predefined reactions shown as emoji + a friendly label instead of an enum.
_REACTIONS: list[tuple[str, str, str]] = [
    ("👍", "NICE", "react.nice"),
    ("❤️", "LOVE", "react.love"),
    ("😄", "FUN", "react.fun"),
    ("😲", "AMAZING", "react.amazing"),
    ("😢", "SAD", "react.sad"),
    ("😮", "OMG", "react.omg"),
]

#: How many entries a numbered picker prints before switching to "type a name".
_PICK_MAX = 40


# ---------------------------------------------------------------------------
# helpers — contacts / chats index
# ---------------------------------------------------------------------------
def _names(api: Any) -> dict[str, str]:
    out: dict[str, str] = {}
    ids = api.get_all_contact_ids() or []
    for i in range(0, len(ids), 100):
        res = api.get_contacts(ids[i : i + 100])
        for mid, w in (res.get("contacts", {}) or {}).items():
            c = w.get("contact", w) if isinstance(w, dict) else {}
            out[mid] = c.get("displayNameOverridden") or c.get("displayName") or ""
    return out


def _display_name(api: Any, mid: str) -> str:
    """Best-effort human name for ``mid`` (falls back to the mid itself)."""
    return _names(api).get(mid) or mid


def _chat_index(api: Any) -> list[tuple[str, str]]:
    """A combined, numbered-picker friendly ``[(mid, name)]`` list of the user's
    member groups *and* contacts — so a group name resolves just like a person."""
    entries: list[tuple[str, str]] = []
    try:
        chats = api.get_all_chat_mids() or {}
        member = chats.get("memberChatMids", []) or []
        if member:
            for g in api.get_chats(member).get("chats", []) or []:
                grp = Group.from_dict(g)
                label = f"[{t('kind.group')}] {grp.name or grp.chat_mid}"
                entries.append((grp.chat_mid, label))
    except Exception:
        pass  # groups are best-effort; contacts still work
    for mid, name in sorted(_names(api).items(), key=lambda kv: kv[1].lower()):
        entries.append((mid, name or mid))
    return entries


def _resolve_to(api: Any, to: str, entries: list[tuple[str, str]] | None = None) -> str | None:
    """A raw mid, or a *unique* contact/group name match → its mid."""
    if not to:
        return None
    if is_mid(to):
        return to
    entries = _chat_index(api) if entries is None else entries
    matches = [(m, n) for m, n in entries if to.lower() in n.lower()]
    if len(matches) == 1:
        print(ui.dim(f"  {ui.GLYPH['arrow']} {matches[0][1]} ({matches[0][0]})"))
        return matches[0][0]
    if not matches:
        print(ui.warn("  " + t("pick.no_match", q=to)))
    else:
        print(
            ui.warn("  " + t("pick.many_match", n=len(matches)))
            + ", ".join(n for _, n in matches[:8])
        )
    return None


def _pick_from(entries: list[tuple[str, str]], label: str) -> str | None:
    """Show a numbered picker over ``[(mid, name)]`` and return the chosen mid.

    Accepts a number, a raw mid (``is_mid``), or a name substring — so a
    non-technical user picks by number while power users can still type an id.
    ``0`` / blank cancels.
    """
    if not entries:
        print(ui.dim("  " + t("pick.none_available")))
        return None
    shown = entries[:_PICK_MAX]
    for i, (mid, name) in enumerate(shown, 1):
        print(f"  {ui.key(f'{i:>2}')}  {name}  {ui.dim(mid)}")
    if len(entries) > _PICK_MAX:
        print(ui.dim("  " + t("pick.more", n=len(entries) - _PICK_MAX)))
    raw = ui.prompt(label).strip()
    if not raw or raw == "0":
        return None
    if is_mid(raw):
        return raw
    if raw.isdigit():
        n = int(raw)
        if 1 <= n <= len(shown):
            return shown[n - 1][0]
        print(ui.warn("  " + t("pick.out_of_range")))
        return None
    matches = [(m, nm) for m, nm in entries if raw.lower() in nm.lower()]
    if len(matches) == 1:
        print(ui.dim(f"  {ui.GLYPH['arrow']} {matches[0][1]} ({matches[0][0]})"))
        return matches[0][0]
    if not matches:
        print(ui.warn("  " + t("pick.no_match", q=raw)))
    else:
        print(
            ui.warn("  " + t("pick.many_match", n=len(matches)))
            + ", ".join(nm for _, nm in matches[:8])
        )
    return None


def _pick_chat(api: Any, label: str = "") -> str | None:
    """Numbered picker over groups + contacts (name / raw-mid fallback)."""
    return _pick_from(_chat_index(api), label or t("prompt.target"))


def _pick_contact(api: Any, label: str = "") -> str | None:
    entries = [
        (m, n or m) for m, n in sorted(_names(api).items(), key=lambda kv: kv[1].lower())
    ]
    return _pick_from(entries, label or t("prompt.profile_of"))


def _pick_group(api: Any, label: str = "", *, invited: bool = False) -> str | None:
    """Numbered picker over the user's member (or invited) groups."""
    chats = api.get_all_chat_mids() or {}
    mids = chats.get("invitedChatMids" if invited else "memberChatMids", []) or []
    if not mids:
        print(ui.dim("  " + t("pick.no_groups")))
        return None
    entries: list[tuple[str, str]] = []
    for g in api.get_chats(mids).get("chats", []) or []:
        grp = Group.from_dict(g)
        entries.append((grp.chat_mid, grp.name or grp.chat_mid))
    return _pick_from(entries, label or t("prompt.pick_chat"))


def _pick_message(api: Any, chat: str, label: str = "") -> str | None:
    """List a chat's recent messages numbered and return the chosen message id."""
    names = _names(api)
    entries: list[tuple[str, str]] = []
    for m in reversed(api.get_recent_messages(chat, 20) or []):
        if not isinstance(m, dict):
            continue
        mid = str(m.get("id") or "")
        if not mid:
            continue
        who = names.get(m.get("from") or "") or (m.get("from") or "")[:8]
        text = m.get("text") or t("msg.non_text")
        entries.append((mid, f"{who}: {str(text)[:40]}"))
    if not entries:
        print(ui.dim("  " + t("pick.none_available")))
        return None
    return _pick_from(entries, label or t("prompt.pick_message"))


def _ask_to(api: Any, label: str = "") -> str | None:
    """The one target prompt used everywhere — a numbered picker over
    groups + contacts, with a typed name or raw mid as a fallback."""
    return _pick_chat(api, label or t("prompt.target"))


# ---------------------------------------------------------------------------
# helpers — input validation, confirmation, small menus
# ---------------------------------------------------------------------------
def _prompt_int(label: str, default: int) -> int:
    """Prompt for an integer, re-asking (not crashing) on non-numbers."""
    while True:
        raw = ui.prompt(label, str(default)).strip()
        if not raw:
            return default
        try:
            return int(raw)
        except ValueError:
            print(ui.warn("  " + t("err.not_number")))


def _prompt_float(label: str, default: float) -> float:
    """Prompt for a decimal number, re-asking on non-numbers."""
    while True:
        raw = ui.prompt(label, str(default)).strip()
        if not raw:
            return default
        try:
            return float(raw)
        except ValueError:
            print(ui.warn("  " + t("err.not_number")))


def _confirm(question: str, default: str = "n") -> bool:
    ans = ui.prompt(f"{question} (y/n)", default).strip().lower()
    return ans in ("y", "yes", "ใช่", "ช่")


def _confirm_overwrite(path: str) -> bool:
    """True if it's safe to write ``path`` (didn't exist, or user said yes)."""
    if os.path.exists(path):
        return _confirm(t("confirm.overwrite", path=path))
    return True


def _submenu(title_key: str, option_keys: list[str]) -> int | None:
    """Draw a small numbered sub-menu; return the 1-based choice or ``None``."""
    print(ui.title("  " + t(title_key)))
    for i, k in enumerate(option_keys, 1):
        print(f"  {ui.key(f'{i:>2}')}  {t(k)}")
    raw = ui.prompt(t("prompt.choose_option"), "1").strip()
    if raw.isdigit() and 1 <= int(raw) <= len(option_keys):
        return int(raw)
    print(ui.warn("  " + t("menu.invalid")))
    return None


def _friendly_error(exc: Exception) -> str:
    """Map a raised exception to a short, translated line for ordinary users."""
    from .enums import ErrorCode
    from .exceptions import LineApiError, LineAuthError, LineTransportError

    if isinstance(exc, LineAuthError):
        return t("err.auth")
    if isinstance(exc, LineTransportError):
        return t("err.network")
    if isinstance(exc, LineApiError):
        by_code = {
            int(ErrorCode.NOT_FOUND): "err.not_found",
            int(ErrorCode.INVALID_MID): "err.invalid_mid",
            int(ErrorCode.NOT_A_MEMBER): "err.not_member",
            int(ErrorCode.NOT_FRIEND): "err.not_friend",
            int(ErrorCode.EXCESSIVE_ACCESS): "err.rate_limited",
            int(ErrorCode.ABUSE_BLOCK): "err.rate_limited",
            int(ErrorCode.MESSAGE_NOT_FOUND): "err.msg_not_found",
        }
        key = by_code.get(exc.code) if exc.code is not None else None
        if key:
            return t(key)
        return t("err.api", detail=exc.reason or str(exc))
    return t("err.generic", detail=str(exc))


def _kv(rows: list[tuple[str, Any]]) -> None:
    ui.table([[ui.dim(k), str(v) if v is not None else ""] for k, v in rows])


# ---------------------------------------------------------------------------
# session
# ---------------------------------------------------------------------------
def _qr_login(path: str) -> Any | None:
    from .client import OkLine
    from .hmac_signer import LtsmBridge
    from .qrterm import print_qr

    if not LtsmBridge.is_available():
        print(
            ui.warn("  " + t("login.node_required"))
            + "\n"
            + ui.dim("  " + t("login.node_hint"))
        )
        return None
    api = OkLine(record=False)
    print("\n" + ui.title(t("login.qr_scan")) + ui.dim(t("login.qr_hint")) + "\n")

    def on_qr(url: str) -> None:
        try:
            print_qr(url)
        except ModuleNotFoundError:
            print(ui.warn("  " + t("login.no_qrcode")))
            print("  " + url)

    try:
        res = api.auth.qr_login(
            on_qr=on_qr,
            on_pin=lambda pin: print(
                "\n"
                + ui.accent(f"  {ui.GLYPH['arrow']}  " + t("login.confirm_pin", pin=pin))
                + "\n"
            ),
        )
    except Exception as exc:
        print(ui.warn(t("login.failed", err=exc)))
        api.close()
        return None
    if not res.access_token:
        print(ui.warn(t("login.incomplete")))
        api.close()
        return None
    info = getattr(api.auth, "last_e2ee_login", None)
    if info:
        try:
            api.e2ee.load_from_login(info["curve_key_id"], info["metadata"])
        except Exception:
            pass
    api.save_tokens(path)
    print(ui.ok(t("login.saved", path=path)) + "\n")
    return api


def _ensure_session(args: Any) -> Any | None:
    from .__main__ import _make_client

    api = _make_client(args)
    if api.tokens.access_token:
        return api
    api.close()
    path = getattr(args, "tokens_file", None) or "tokens.json"
    if os.path.exists(path):
        from .client import OkLine

        return OkLine.from_tokens_file(path)
    print(ui.dim("  " + t("login.no_session") + "\n"))
    try:
        return _qr_login(path)
    except KeyboardInterrupt:
        return None


# ---------------------------------------------------------------------------
# actions — me & account
# ---------------------------------------------------------------------------
def act_whoami(api: Any) -> None:
    p = api.get_profile()
    chats = api.get_all_chat_mids() or {}
    _kv(
        [
            (t("kv.name"), p.get("displayName")),
            (t("kv.mid"), p.get("mid")),
            (t("kv.userid"), p.get("userid")),
            (t("kv.status"), p.get("statusMessage")),
            (t("kv.contacts"), len(api.get_all_contact_ids() or [])),
            (
                t("kv.groups"),
                t(
                    "kv.groups_fmt",
                    n=len(chats.get("memberChatMids", [])),
                    inv=len(chats.get("invitedChatMids", [])),
                ),
            ),
            (t("kv.favorites"), len(api.get_favorite_mids() or [])),
            (t("kv.blocked"), len(api.get_blocked_contact_ids() or [])),
            (t("kv.e2ee"), t("e2ee.on") if api.e2ee.is_ready() else t("e2ee.off")),
        ]
    )


def act_my_profile(api: Any) -> None:
    p = api.get_profile() or {}
    _kv([(k, v) for k, v in p.items() if v not in (None, "", [], {})])


def act_set_name(api: Any) -> None:
    name = ui.prompt(t("prompt.new_name"))
    if name:
        api.set_display_name(name)
        print(ui.ok(t("msg.name_set", name=name)))


def act_set_status(api: Any) -> None:
    msg = ui.prompt(t("prompt.new_status"))
    api.set_status_message(msg)
    print(ui.ok(t("msg.status_set", status=msg)))


def act_settings(api: Any) -> None:
    s = api.get_settings()
    if isinstance(s, dict):
        _kv([(k, v) for k, v in list(s.items())[:30]])
    else:
        print(s)


def act_logout(api: Any) -> None:
    if not _confirm(t("confirm.logout")):
        return
    try:
        api.auth.logout()
    except Exception as exc:
        print(ui.warn("  " + t("msg.server_logout_failed", err=exc)))
    path = getattr(api, "_session_path", None) or "tokens.json"
    if os.path.exists(path):
        os.remove(path)
        print(ui.ok(t("msg.removed_session", path=path)))
    print(ui.dim("  " + t("msg.relogin_hint")))


# ---------------------------------------------------------------------------
# actions — contacts
# ---------------------------------------------------------------------------
def act_contacts(api: Any) -> None:
    q = ui.prompt(t("prompt.contact_filter")).lower()
    rows = sorted(_names(api).items(), key=lambda kv: kv[1].lower())
    if q:
        rows = [(m, n) for m, n in rows if q in n.lower()]
    ui.table([[ui.dim(m), n] for m, n in rows[:300]])
    print(ui.dim("  " + t("msg.n_contacts", n=len(rows))))


def act_find(api: Any) -> None:
    q = ui.prompt(t("prompt.find_name")).lower()
    if not q:
        return
    ui.table([[ui.dim(m), n] for m, n in _names(api).items() if q in n.lower()])


def act_profile_of(api: Any) -> None:
    mid = _pick_contact(api, t("prompt.profile_of"))
    if not mid:
        return
    name = _names(api).get(mid, mid)
    print(ui.title(f"  {name}") + ui.dim(f"  {mid}"))
    try:
        detail = api.get_buddy_detail(mid) or {}
        if isinstance(detail, dict):
            _kv([(k, v) for k, v in detail.items() if v not in (None, "", [], {})])
    except Exception as exc:
        print(ui.dim("  " + t("msg.no_buddy_detail", err=exc)))


def act_add_friend(api: Any) -> None:
    who = ui.prompt(t("prompt.add_friend"))
    if not who:
        return
    if is_mid(who) and who[:1].lower() == "u":
        mid: str | None = who
        name = _names(api).get(who, who)
    else:
        c = api.find_contact_by_userid(who) or {}
        mid = c.get("mid") if isinstance(c, dict) else None
        if not mid:
            print(ui.warn("  " + t("msg.not_found")))
            return
        name = c.get("displayName") or mid
    if not _confirm(t("confirm.add_friend", name=name)):
        return
    api.add_friend_by_mid(mid)
    print(ui.ok(t("msg.added", name=name)))


def act_search_user(api: Any) -> None:
    uid = ui.prompt(t("prompt.search_userid"))
    if not uid:
        return
    c = api.find_contact_by_userid(uid) or {}
    if not isinstance(c, dict) or not c.get("mid"):
        print(ui.warn("  " + t("msg.not_found")))
        return
    _kv(
        [
            (t("kv.mid"), c.get("mid")),
            (t("kv.name"), c.get("displayName")),
            (t("kv.status"), c.get("statusMessage")),
        ]
    )
    if _confirm(t("prompt.add_as_friend")):
        api.add_friend_by_mid(c["mid"])
        print(ui.ok(t("msg.added", name=c.get("displayName") or c["mid"])))


def act_block(api: Any) -> None:
    choice = _submenu("block.title", ["block.list", "block.add", "block.remove"])
    if choice is None:
        return
    if choice == 1:
        names = _names(api)
        ui.table(
            [[ui.dim(m), names.get(m, "")] for m in (api.get_blocked_contact_ids() or [])]
        )
    elif choice == 2:
        mid = _pick_chat(api, t("prompt.block_target"))
        if mid and _confirm(t("confirm.block", name=_display_name(api, mid))):
            api.block_contact(mid)
            print(ui.ok(t("msg.blocked")))
    else:
        names = _names(api)
        blocked = [(m, names.get(m, m)) for m in (api.get_blocked_contact_ids() or [])]
        mid = _pick_from(blocked, t("prompt.unblock_target"))
        if mid:
            api.unblock_contact(mid)
            print(ui.ok(t("msg.unblocked")))


def act_favorites(api: Any) -> None:
    choice = _submenu("fav.title", ["fav.list", "fav.add", "fav.remove"])
    if choice is None:
        return
    if choice == 1:
        names = _names(api)
        ui.table([[ui.dim(m), names.get(m, "")] for m in (api.get_favorite_mids() or [])])
        return
    add = choice == 2
    mid = _pick_chat(api, t("prompt.fav_target"))
    if mid:
        api.set_chat_favorite(mid, 1 if add else 0)
        print(ui.ok(t("msg.favorited") if add else t("msg.unfavorited")))


def act_export_contacts(api: Any) -> None:
    fmt = ui.prompt(t("prompt.export_format"), "csv").lower()
    path = ui.prompt(t("prompt.output_file"), f"contacts.{fmt}")
    if not _confirm_overwrite(path):
        return
    rows = sorted(_names(api).items(), key=lambda kv: kv[1].lower())
    if fmt == "json":
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(
                [{"mid": m, "name": n} for m, n in rows], fh, ensure_ascii=False, indent=2
            )
    else:
        import csv

        with open(path, "w", encoding="utf-8-sig", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["mid", "name"])
            w.writerows(rows)
    print(ui.ok(t("msg.wrote_contacts", n=len(rows), path=os.path.abspath(path))))


# ---------------------------------------------------------------------------
# actions — groups & chats
# ---------------------------------------------------------------------------
def act_groups(api: Any) -> None:
    chats = api.get_all_chat_mids() or {}
    member = chats.get("memberChatMids", [])
    print(
        ui.dim(
            "  "
            + t(
                "grp.member_invited",
                m=len(member),
                i=len(chats.get("invitedChatMids", [])),
            )
            + "\n"
        )
    )
    rows = []
    for g in api.get_chats(member).get("chats", []) if member else []:
        grp = Group.from_dict(g)
        rows.append([ui.dim(grp.chat_mid), f"({grp.member_count})", grp.name])
    ui.table(rows)


def act_members(api: Any) -> None:
    gid = _pick_group(api, t("prompt.pick_chat"))
    if not gid:
        return
    chats = api.get_chats([gid]).get("chats", [])
    if not chats:
        print(ui.warn("  " + t("grp.not_found")))
        return
    grp = Group.from_dict(chats[0])
    print(
        ui.title(f"  {grp.name}")
        + ui.dim("  " + t("grp.n_members", n=grp.member_count) + "\n")
    )
    names: dict[str, str] = {}
    for i in range(0, len(grp.member_mids), 100):
        res = api.get_contacts(grp.member_mids[i : i + 100])
        for mid, w in (res.get("contacts", {}) or {}).items():
            c = w.get("contact", w) if isinstance(w, dict) else {}
            names[mid] = c.get("displayNameOverridden") or c.get("displayName") or ""
    ui.table([[ui.dim(mid), names.get(mid, "")] for mid in grp.member_mids])


def act_leave(api: Any) -> None:
    gid = _pick_group(api, t("prompt.pick_chat"))
    if gid and _confirm(t("confirm.leave", name=_display_name(api, gid))):
        api.leave_chat(gid)
        print(ui.ok(t("msg.left")))


def act_accept(api: Any) -> None:
    gid = _pick_group(api, t("prompt.pick_chat"), invited=True)
    if gid:
        api.accept_chat_invitation(gid)
        print(ui.ok(t("msg.accepted")))


def act_boxes(api: Any) -> None:
    boxes = api.get_message_boxes(limit=20)
    rows = []
    for b in boxes.get("messageBoxes", []) if isinstance(boxes, dict) else []:
        if isinstance(b, dict):
            rows.append([ui.dim(str(b.get("id"))), f"unread={b.get('unreadCount', '?')}"])
    ui.table(rows)


# ---------------------------------------------------------------------------
# actions — messaging
# ---------------------------------------------------------------------------
def _sent(res: Any) -> None:
    print(
        ui.ok(t("msg.sent"))
        + ui.dim(f"  id={res.get('id') if isinstance(res, dict) else res}")
    )


def act_send_text(api: Any) -> None:
    to = _ask_to(api)
    if not to:
        return
    text = ui.prompt(t("prompt.message_text"))
    if text:
        _sent(api.send_text(to, text))


def act_send_sticker(api: Any) -> None:
    to = _ask_to(api)
    if not to:
        return
    print(ui.title("  " + t("sticker.pick")))
    for i, (name_key, _pkg, _stk) in enumerate(_STICKERS, 1):
        print(f"  {ui.key(f'{i:>2}')}  {t(name_key)}")
    print(ui.dim("  " + t("sticker.manual")))
    raw = ui.prompt(t("prompt.choose"), "1").strip()
    if raw.isdigit() and 1 <= int(raw) <= len(_STICKERS):
        _, pkg, stk = _STICKERS[int(raw) - 1]
    else:
        pkg = ui.prompt(t("prompt.package_id"), "11537")
        stk = ui.prompt(t("prompt.sticker_id"), "52002734")
    _sent(api.send_sticker(to, pkg, stk))


def act_send_location(api: Any) -> None:
    to = _ask_to(api)
    if not to:
        return
    lat = _prompt_float(t("prompt.latitude"), 35.6586)
    lon = _prompt_float(t("prompt.longitude"), 139.7454)
    _sent(api.send_location(to, lat, lon, title=ui.prompt(t("prompt.title"), "")))


def act_send_media(api: Any) -> None:
    to = _ask_to(api)
    if not to:
        return
    path = ui.prompt(t("prompt.media_path"))
    if not path:
        return
    is_img = path.lower().rsplit(".", 1)[-1] in ("jpg", "jpeg", "png", "gif", "webp")
    _sent(api.send_image(to, path) if is_img else api.send_file(to, path))


def _reaction_menu() -> str | None:
    print(ui.title("  " + t("react.pick")))
    for i, (emoji, _name, label_key) in enumerate(_REACTIONS, 1):
        print(f"  {ui.key(f'{i:>2}')}  {emoji}  {t(label_key)}")
    raw = ui.prompt(t("prompt.choose"), "1").strip()
    if raw.isdigit() and 1 <= int(raw) <= len(_REACTIONS):
        return _REACTIONS[int(raw) - 1][1]
    print(ui.warn("  " + t("err.unknown_reaction")))
    return None


def act_react(api: Any) -> None:
    from .enums import PredefinedReactionType

    to = _pick_chat(api, t("prompt.pick_chat"))
    if not to:
        return
    mid = _pick_message(api, to, t("prompt.pick_message"))
    if not mid:
        return
    name = _reaction_menu()
    if not name:
        return
    api.react(mid, int(PredefinedReactionType[name]))
    print(ui.ok(t("msg.reacted")))


def act_unsend(api: Any) -> None:
    to = _pick_chat(api, t("prompt.pick_chat"))
    if not to:
        return
    mid = _pick_message(api, to, t("prompt.pick_message"))
    if not mid:
        return
    if not _confirm(t("confirm.unsend")):
        return
    api.unsend_message(mid)
    print(ui.ok(t("msg.unsent")))


def act_reply(api: Any) -> None:
    to = _pick_chat(api, t("prompt.pick_chat"))
    if not to:
        return
    rel = _pick_message(api, to, t("prompt.pick_message"))
    if not rel:
        return
    text = ui.prompt(t("prompt.reply_text"))
    if text:
        _sent(api.reply_text(to, text, rel))


def act_broadcast(api: Any) -> None:
    from .exceptions import LineApiError

    text = ui.prompt(t("prompt.broadcast_text"))
    raw = ui.prompt(t("prompt.broadcast_targets"))
    if not text or not raw:
        return
    index = _chat_index(api)
    targets = [tg for tg in (_resolve_to(api, x, index) for x in raw.split()) if tg]
    if not targets or not _confirm(t("confirm.broadcast", n=len(targets))):
        return
    ok = 0
    for mid in targets:
        try:
            api.send_text(mid, text)
            ok += 1
            print(ui.dim(f"  {ui.GLYPH['check']} {mid}"))
        except LineApiError as exc:
            print(ui.warn(f"  {mid}: " + _friendly_error(exc)))
            from .enums import ErrorCode

            if getattr(exc, "code", None) in {
                int(ErrorCode.EXCESSIVE_ACCESS),
                int(ErrorCode.ABUSE_BLOCK),
            }:
                print(ui.warn("  " + t("err.rate_limited")))
                break
    print(ui.ok(f"{ok}/{len(targets)}"))


# ---------------------------------------------------------------------------
# actions — read & history
# ---------------------------------------------------------------------------
def _print_log(api: Any, msgs: list, names: dict[str, str]) -> None:
    for m in reversed(msgs):
        if not isinstance(m, dict):
            continue
        text = m.get("text")
        if m.get("chunks"):
            if api.e2ee.is_ready():
                try:
                    text = api.decrypt_message(m).get("text")
                except Exception:
                    text = ui.dim(t("msg.encrypted"))
            else:
                text = ui.dim(t("msg.encrypted_login"))
        who = names.get(m.get("from") or "") or (m.get("from") or "")[:10]
        print(f"  {ui.dim(who.rjust(14))}  {text or ui.dim(t('msg.non_text'))}")


def act_chatlog(api: Any) -> None:
    cid = _pick_chat(api, t("prompt.pick_chat"))
    if not cid:
        return
    n = _prompt_int(t("prompt.how_many"), 30)
    print()
    _print_log(api, api.get_recent_messages(cid, n) or [], _names(api))


def act_recent(api: Any) -> None:
    cid = _pick_chat(api, t("prompt.pick_chat"))
    if not cid:
        return
    n = _prompt_int(t("prompt.how_many"), 10)
    print()
    _print_log(api, api.get_recent_messages(cid, n) or [], _names(api))


def act_search_messages(api: Any) -> None:
    cid = _pick_chat(api, t("prompt.pick_chat"))
    if not cid:
        return
    kw = ui.prompt(t("prompt.keyword")).lower()
    if not kw:
        return
    names = _names(api)
    hits = []
    for m in api.get_recent_messages(cid, 200) or []:
        text = m.get("text") if isinstance(m, dict) else None
        if text and kw in text.lower():
            hits.append(m)
    print(ui.dim("  " + t("msg.n_matches", n=len(hits)) + "\n"))
    _print_log(api, hits, names)


def act_backup(api: Any) -> None:
    cid = _pick_chat(api, t("prompt.pick_chat"))
    if not cid:
        return
    out = ui.prompt(t("prompt.output_file"), f"{cid}.json")
    if not _confirm_overwrite(out):
        return
    msgs = api.get_recent_messages(cid, 200) or []
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(msgs, fh, ensure_ascii=False, indent=2)
    print(ui.ok(t("msg.saved_n", n=len(msgs), path=os.path.abspath(out))))


# ---------------------------------------------------------------------------
# actions — live & bots
# ---------------------------------------------------------------------------
def _run_bot(api: Any, on_msg: Action, banner: str) -> None:
    from .bot import Bot

    bot = Bot(api)
    bot.on_message(on_msg)
    print(ui.dim("  " + t("msg.bot_hint", banner=banner)))
    try:
        bot.run()
    except KeyboardInterrupt:
        pass


def act_watch(api: Any) -> None:
    echo = _confirm(t("prompt.watch_echo"))

    def on_msg(ctx: Any) -> None:
        where = "group" if ctx.is_group else "dm"
        print(f"  {ui.dim('[' + where + ']')} {ui.dim(ctx.sender)}: {ctx.text!r}")
        if echo and ctx.text:
            ctx.reply(f"you said: {ctx.text}")

    _run_bot(api, on_msg, t("msg.watching"))


def act_autoreply(api: Any) -> None:
    rules: dict[str, str] = {}
    print(ui.dim("  " + t("msg.rules_hint")))
    while True:
        line = ui.prompt(t("prompt.autoreply_rule"))
        if not line:
            break
        if "=" in line:
            k, v = line.split("=", 1)
            rules[k.strip().lower()] = v.strip()
    if not rules:
        return

    def on_msg(ctx: Any) -> None:
        if not ctx.text:
            return
        low = ctx.text.lower()
        for kw, reply in rules.items():
            if kw in low:
                ctx.reply(reply)
                print(ui.dim("  " + t("msg.matched_replied", kw=kw)))
                break

    _run_bot(api, on_msg, t("msg.autoreply", n=len(rules)))


def act_notify(api: Any) -> None:
    kw = ui.prompt(t("prompt.notify_keyword")).lower()

    def on_msg(ctx: Any) -> None:
        if kw and kw not in (ctx.text or "").lower():
            return
        where = "group" if ctx.is_group else "dm"
        print(f"  {ui.dim('[' + where + ']')} {ctx.sender}: {ctx.text or t('msg.non_text')}")

    _run_bot(api, on_msg, t("msg.notifying"))


# ---------------------------------------------------------------------------
# actions — E2EE / encryption
# ---------------------------------------------------------------------------
def act_e2ee_status(api: Any) -> None:
    _kv(
        [
            (t("e2ee.ready"), t("e2ee.yes") if api.e2ee.is_ready() else t("e2ee.no")),
            (t("e2ee.keys_loaded"), len(getattr(api.e2ee, "my_keys", {}))),
            (t("e2ee.latest_key"), getattr(api.e2ee, "latest_key_id", None)),
            (t("e2ee.my_mid"), getattr(api.e2ee, "my_mid", None)),
        ]
    )
    if not api.e2ee.is_ready():
        print(ui.dim("  " + t("e2ee.load_hint")))


def act_e2ee_send(api: Any) -> None:
    to = _ask_to(api, t("prompt.target"))
    if not to:
        return
    text = ui.prompt(t("prompt.message_text"))
    if text:
        _sent(api.send_encrypted_text(to, text))


def act_e2ee_decrypt(api: Any) -> None:
    if not api.e2ee.is_ready():
        print(ui.warn("  " + t("err.e2ee_not_loaded")))
        return
    cid = _pick_chat(api, t("prompt.pick_chat"))
    if not cid:
        return
    sealed = next(
        (
            m
            for m in reversed(api.get_recent_messages(cid, 20) or [])
            if isinstance(m, dict) and m.get("chunks")
        ),
        None,
    )
    if not sealed:
        print(ui.dim("  " + t("msg.no_encrypted")))
        return
    out = api.decrypt_message(sealed)
    print(ui.ok(t("msg.text_is", text=repr(out.get("text")))))


def act_e2ee_roundtrip(api: Any) -> None:
    if not api.e2ee.is_ready():
        print(ui.warn("  " + t("err.e2ee_not_loaded")))
        return
    to = _ask_to(api, t("e2ee.peer_prompt"))
    if not to:
        return
    got = api.e2ee.roundtrip(to, "OkLine roundtrip ✓")
    style = ui.ok if got == "OkLine roundtrip ✓" else ui.warn
    print(style(t("msg.recovered", text=repr(got))))


# ---------------------------------------------------------------------------
# actions — advanced / dev
# ---------------------------------------------------------------------------
def act_call(api: Any) -> None:
    from .endpoints import THRIFT_ENDPOINTS

    ep = ui.prompt(t("prompt.endpoint"))
    if ep not in THRIFT_ENDPOINTS:
        print(ui.warn("  " + t("err.unknown_endpoint")))
        return
    raw = ui.prompt(t("prompt.endpoint_args"), "[]")
    try:
        args = json.loads(raw)
    except ValueError as exc:
        print(ui.warn("  " + t("err.bad_json", err=exc)))
        return
    print(json.dumps(api.transport.call(ep, args), ensure_ascii=False, indent=2))


def act_list_endpoints(api: Any) -> None:
    from .endpoints import THRIFT_ENDPOINTS

    grep = ui.prompt(t("prompt.endpoint_filter")).lower()
    keys = [k for k in sorted(THRIFT_ENDPOINTS) if grep in k.lower()]
    for k in keys:
        print(f"  {k}")
    print(ui.dim("  " + t("msg.n_endpoints", n=len(keys))))


def act_selftest(api: Any) -> None:
    from .selftest import print_results, run_selftest

    fails = print_results(run_selftest(api))
    print((ui.warn if fails else ui.ok)(t("msg.n_failures", n=fails)))


def act_recording(api: Any) -> None:
    print(ui.dim("  " + t("msg.n_recorded", n=len(api.history))))
    if _confirm(t("prompt.log_save")):
        fmt = ui.prompt(t("prompt.rec_format"), "text")
        path = ui.prompt(t("prompt.rec_file"), f"okline_log.{'har' if fmt == 'har' else fmt}")
        if not _confirm_overwrite(path):
            return
        api.save_log(path, fmt=fmt)
        print(ui.ok(t("msg.saved_log", path=os.path.abspath(path))))


# ---------------------------------------------------------------------------
# language toggle
# ---------------------------------------------------------------------------
def act_language(api: Any) -> None:
    global _LANG
    _LANG = "en" if _LANG == "th" else "th"
    print(ui.ok(t("msg.lang_set")))


# ---------------------------------------------------------------------------
# menu tree
# ---------------------------------------------------------------------------
#: The menu, expressed as ``(section_key, [(label_key, action), …])`` so both
#: :func:`_menu` (which translates it) and the tests (which check every key has
#: a translation) share one source of truth.
_SECTIONS: list[tuple[str, list[tuple[str, Action]]]] = [
    (
        "sec.me",
        [
            ("act.whoami", act_whoami),
            ("act.my_profile", act_my_profile),
            ("act.set_name", act_set_name),
            ("act.set_status", act_set_status),
            ("act.settings", act_settings),
            ("act.logout", act_logout),
        ],
    ),
    (
        "sec.contacts",
        [
            ("act.contacts", act_contacts),
            ("act.find", act_find),
            ("act.profile_of", act_profile_of),
            ("act.add_friend", act_add_friend),
            ("act.search_user", act_search_user),
            ("act.block", act_block),
            ("act.favorites", act_favorites),
            ("act.export", act_export_contacts),
        ],
    ),
    (
        "sec.groups",
        [
            ("act.groups", act_groups),
            ("act.members", act_members),
            ("act.leave", act_leave),
            ("act.accept", act_accept),
            ("act.boxes", act_boxes),
        ],
    ),
    (
        "sec.send",
        [
            ("act.send_text", act_send_text),
            ("act.send_sticker", act_send_sticker),
            ("act.send_location", act_send_location),
            ("act.send_media", act_send_media),
            ("act.reply", act_reply),
            ("act.react", act_react),
            ("act.unsend", act_unsend),
            ("act.broadcast", act_broadcast),
        ],
    ),
    (
        "sec.read",
        [
            ("act.chatlog", act_chatlog),
            ("act.recent", act_recent),
            ("act.search_msgs", act_search_messages),
            ("act.backup", act_backup),
        ],
    ),
    (
        "sec.live",
        [
            ("act.watch", act_watch),
            ("act.autoreply", act_autoreply),
            ("act.notify", act_notify),
        ],
    ),
    (
        "sec.e2ee",
        [
            ("act.e2ee_status", act_e2ee_status),
            ("act.e2ee_send", act_e2ee_send),
            ("act.e2ee_decrypt", act_e2ee_decrypt),
            ("act.e2ee_roundtrip", act_e2ee_roundtrip),
        ],
    ),
    (
        "sec.dev",
        [
            ("act.call", act_call),
            ("act.list_endpoints", act_list_endpoints),
            ("act.selftest", act_selftest),
            ("act.recording", act_recording),
        ],
    ),
]


def _dev_enabled() -> bool:
    v = (os.environ.get("OKLINE_DEV") or "").strip().lower()
    return v not in ("", "0", "false", "no", "off")


def _menu() -> list[Item]:
    """Build the translated menu tree; hides the developer section unless
    ``OKLINE_DEV`` is set, and appends the language toggle at the root."""
    items: list[Item] = []
    for sec_key, leaves in _SECTIONS:
        if sec_key == "sec.dev" and not _dev_enabled():
            continue
        sub: list[Item] = [(t(lbl), act) for lbl, act in leaves]
        items.append((t(sec_key), sub))
    items.append((t("sec.language"), act_language))
    return items


def _menu_keys() -> list[str]:
    """Every translation key that a menu label / title depends on (used by
    tests to guarantee no missing translation)."""
    keys = ["app.title", "sec.language"]
    for sec_key, leaves in _SECTIONS:
        keys.append(sec_key)
        keys.extend(lbl for lbl, _ in leaves)
    return keys


def _header(api: Any) -> list[str]:
    try:
        p = api.get_profile() or {}
    except Exception:
        p = {}
    name = p.get("displayName") or "?"
    mid = p.get("mid") or ""
    return [
        ui.bold(name) + ui.dim("   " + (mid[:20] + ui.GLYPH["ell"] if len(mid) > 20 else mid)),
        ui.dim(t("hdr.e2ee"))
        + (ui.accent(t("e2ee.on")) if api.e2ee.is_ready() else ui.dim(t("e2ee.off"))),
    ]


def _run(
    api: Any,
    title: str,
    items: list[Item],
    header: list[str],
    *,
    root: bool,
    builder: Callable[[], tuple[str, list[Item]]] | None = None,
) -> None:
    while True:
        if builder is not None:  # root: rebuild so a language switch shows live
            title, items = builder()
        ui.clear()
        print()
        ui.panel(header, head=title)
        print()
        ui.menu(
            [label for label, _ in items],
            quit_label=t("ui.quit") if root else t("ui.back"),
        )
        choice = ui.prompt("\n " + t("menu.choose")).strip()
        if choice in ("0", "q", "quit", "exit", "ออก"):
            if root:
                print(ui.dim(t("msg.bye")))
            return
        if not choice:
            continue
        if not choice.isdigit() or not (1 <= int(choice) <= len(items)):
            print(ui.warn("  " + t("menu.invalid")))
            ui.pause(t("ui.pause"))
            continue
        label, target = items[int(choice) - 1]
        if isinstance(target, list):
            _run(api, label, target, header, root=False)
            continue
        ui.clear()
        print()
        ui.rule(label)
        print()
        try:
            target(api)
        except KeyboardInterrupt:
            print(ui.dim("  " + t("msg.cancelled")))
        except Exception as exc:
            print(ui.warn("  " + _friendly_error(exc)))
        ui.pause(t("ui.pause"))


def interactive(args: Any) -> int:
    """Entry point for ``okline`` / ``okline menu``."""
    reconfigure_stdout_utf8()
    api = _ensure_session(args)
    if api is None:
        print(ui.dim(t("msg.no_session_bye")))
        return 1
    try:
        _run(
            api,
            t("app.title"),
            _menu(),
            _header(api),
            root=True,
            builder=lambda: (t("app.title"), _menu()),
        )
    finally:
        api.close()
    return 0
