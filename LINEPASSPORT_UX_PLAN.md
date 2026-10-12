# LinePassport — แผนปรับปรุง UX/UI และระบบ (handoff สำหรับ Claude Code)

> ไฟล์นี้สรุปผลรีวิว การตัดสินใจออกแบบ และรายการงานที่ต้องทำในโค้ด
> จัดทำ 2026-10-12 จากการรันเว็บจริง (`okline web`) และอ่านโค้ด `okline/webapp.py`
> Mockup ทั้งหมดอยู่ใน Claude Design canvas "LinePassport Redesign" (10 artboards)

---

## 0. บริบทโปรเจกต์

- Repo: `https://github.com/TheBoy/LinePassport` (แพ็กเกจ PyPI ชื่อ `okline`)
- Python 3.9+ · Node.js 18+ (ใช้รัน `ltsm.wasm` สำหรับเซ็น `X-Hmac`)
- หน้าเว็บทั้งหมดอยู่ในไฟล์เดียว **`okline/webapp.py` (~22,300 บรรทัด)**: HTML + CSS + JS เป็น string ใน Python และ handler ฝั่ง server อยู่ในไฟล์เดียวกัน
- รัน: `pip install -e . && python -m okline web --no-open --port 8765 --state-dir .okline`
- ทดสอบ: `pytest` (485 เคส) — **ต้องรันผ่านทุกครั้งก่อน commit**
- ภาษา: โหมดไทยเป็นค่าเริ่มต้น (`state.lang`), ข้อความทั้งหมดอยู่ใน dict `I18N` (ประมาณบรรทัด 4800–5700) ใช้ `t("key")`
- หน้า admin แยกต่างหากที่ `/god` (รหัสผ่านอีกชุด) — ตั้งค่า Ollama อยู่ที่นี่

### ตำแหน่งสำคัญใน `okline/webapp.py` (เลขบรรทัดโดยประมาณ ณ commit `84e68e6`)

| ส่วน | บรรทัด |
|---|---|
| CSS | ~70–3250 |
| HTML: auth panel | 3261 |
| HTML: top bar + account select + settings menu | 3296–3320 |
| HTML: settings panel / login wizard | 3334–3545 |
| HTML: workspace tabs (`#tabLine … #tabLogs`) | 3549–3557 |
| HTML: tab LINE (รายชื่อ + แชท) | 3559–3683 |
| HTML: tab AI Chat Bot (assistant) | 3684–3958 |
| HTML: tab API | 3959–4194 |
| HTML: tab Bot (scheduler) | 4195–4630 |
| HTML: tab Logs | 4631–4725 |
| HTML: tab Tools | 4726+ |
| I18N dict | ~4800–5700 |
| JS: constants `CONVERSATION_BACKGROUND_REFRESH_MS = 8000` | 5827 |
| JS: notifications (`showSystemNotification`, `processNewMessageNotifications`) | 7487–7553 |
| JS: `refreshConversationsInBackground` | 7555 |
| JS: send message (`/api/send`) | 7883 |
| JS: `renderSchedules` | 9067 |
| JS: timers (`setInterval`, `visibilitychange`) | 10988–11010 |
| `GOD_HTML` (หน้า /god) | 11045 |
| `ROLE_DEFINITIONS` (admin / operator) | 12626 |
| `AI_AUTO_REPLY_POLL_SECONDS = 5.0` | 12239 |
| `Scheduler._run` (10 วิ) / `AiAutoReplyEngine._run` (5 วิ) | 14100–14127 |
| `WebState.__init__` | 14129 |
| `chat_summary_map` / `inbox_message_boxes` (snapshot cache 5 วิ) | 14518 |
| `append_bot_log` / `list_bot_logs` | 15381 / 15413 |
| `poll_ai_auto_replies` | 17948 |
| HTTP router: `/api/send` | 19301 |
| `_conversations` (GET /api/conversations) | 19399 |

---

## 1. ผลรีวิว UX ปัจจุบัน (สรุป)

### 1.1 ปัญหาโครงสร้างรวม
1. **เมนูซ้อน 3 ชั้น** — แท็บหลัก 6 อัน (`LINE · AI Chat Bot · API · Tools · Bot · Logs`) + แท็บย่อย 2–5 อันต่อแท็บ + ปุ่มซ้ำในการ์ด; "Bot" กับ "AI Chat Bot" คนแยกไม่ออก
2. **ของนักพัฒนา (API / Tools / Logs) โชว์เท่ากับงานหลัก**
3. **บัญชี LINE เป็น dropdown บน header แยกจากโปรไฟล์ในแท็บ LINE** และมี pill สถานะขัดกัน (`Not connected` + `Ready to send` พร้อมกัน)
4. **ยังไม่มีบัญชี = เห็นทุกแท็บแต่กดไม่ได้** แทนที่จะเห็นแค่ QR
5. **Login wizard ขั้นที่ 1 เป็นแค่ปุ่ม Start + checkbox Proxy** — ขั้นเกินจำเป็น
6. **แชทต้องกรอก "Target MID or contact name" แล้วกด Open** — จุดที่คนงงที่สุด
7. **ภาษาไทยปนอังกฤษ** แม้ในโหมดไทย: "AI Chat Bot", "Contact/Group", "Knowledge / RAG", "0 chunks", "Not indexed", "Generate Knowledge / RAG", "Log"
8. มือถือ: header กินจอ 1/3

### 1.2 ส่งตามเวลา (tab Bot)
- รายการงานโชว์ปลายทางเป็น **MID ดิบ** (`job.to` ใน `renderSchedules`) ไม่ใช่ชื่อ
- สถานะ 5 แบบเป็นข้อความเทาต่อท้ายบรรทัดเดียว สแกนไม่ออก
- ปุ่ม 4 ปุ่มต่อแถวน้ำหนักเท่ากัน รวม "ลบ" สีแดงติดกับ "ส่งเดี๋ยวนี้"
- ฟอร์มสร้างงาน **~18 ช่องในหน้าเดียว**; "ส่งเมื่อ" ยังโชว์แม้เลือก "ส่งซ้ำ"; เวลาเป็น dropdown ชม.:นาที 6 ชุด; ค่าเริ่มต้น "ทุก 15 นาที ไม่จำกัดครั้ง" เสี่ยงโดนแบน
- ตัวแปร `{1D} {2D} {3D} {date} {time} {rand:1-100}` ไม่มีคำอธิบาย
- ไม่มี "ส่งทดสอบ" ก่อนบันทึก — ต้องสร้างงานแล้วกด "ส่งเดี๋ยวนี้" ซึ่งส่งหาลูกค้าจริง
- แพทเทิร์น: ต้องสร้างหมวดหมู่ก่อน (dropdown ว่าง = ทางตัน), ซ้อน 3 ชั้น
- "ตั้งค่า AI" ในแท็บนี้คือ AI สร้างรูป (Gemini/nanobanana) คนละตัวกับ AI ตอบอัตโนมัติ (Ollama) → สับสน
- Log เป็นหน้าจอดำแบบ terminal

### 1.3 ตอบอัตโนมัติ (tab AI Chat Bot)
- เปิดมาเจอ "ยังไม่ได้ตั้งค่า" **ไม่มีปุ่มตั้งค่าในหน้านี้** — ต้องไป `/god` (รหัสอีกชุด) กรอก Ollama URL / model / embedding / Top K / Temperature
- ต้องติดตั้ง Ollama เอง ไม่มีทางเลือก API key
- สวิตช์หลัก + Contact + Group เรียงเท่ากันไม่มีลำดับชั้น
- "แชท" (ทดลองถาม AI) อยู่คนละแท็บกับ "Knowledge" ต้องสลับไปมา และไม่บอกว่าตอบจากข้อมูลตรงไหน
- Knowledge เป็น textarea `knowledge.md` เปล่า + ปุ่ม 2 อัน ("บันทึกและสร้างดัชนี" / "สร้างดัชนีใหม่")
- Knowledge API (URL / JSON path / Bearer) โชว์เต็มใต้ข้อมูลร้าน
- Knowledge ผูกกับ **ผู้ใช้เว็บ** ไม่ใช่บัญชี LINE → ร้านที่มี 2 คนดูแลข้อมูลไม่แชร์กัน

### 1.4 การ refresh แชท / แจ้งเตือน
- `refreshConversationsInBackground` ทุก 8 วิ **แต่ `return` ทันทีถ้าแท็บซ่อนและไม่ได้กดกระดิ่ง** (`!state.notificationsEnabled && !lineViewIsVisible`)
- เบราว์เซอร์บีบ `setInterval` ในแท็บซ่อนเหลือ ≤1 ครั้ง/นาที (Chrome intensive throttling) · มือถือหยุดเมื่อล็อกจอ
- Server poll (`AiAutoReplyEngine`) ทำงาน **เฉพาะบัญชีที่เปิด AI ตอบอัตโนมัติ**
- ใช้ `getMessageBoxes` ทุกรอบ ไม่ใช้ long-poll (`fetchOps`) ของ SDK
- แจ้งเตือน: opt-in ผ่านกระดิ่ง, ขอสิทธิ์เฉพาะตอนกด, `renotify: true` → เด้งทุกข้อความ, สูงสุด 5 ใบ/รอบ, เสียงทุกครั้ง, ไม่มี cooldown
- ไม่มี backoff เมื่อ error, ไม่มีตัวบอกอายุข้อมูล
- `/api/send` **ไม่บันทึกว่าใครกดส่ง** → ทำ KPI ไม่ได้

---

## 2. การตัดสินใจออกแบบ (ตาม mockup)

### 2.1 โครงหน้าใหม่
- **Sidebar ซ้าย 240px** แทนแท็บบน: `แชท · ตอบอัตโนมัติ · ส่งตามเวลา · KPI พนักงาน (admin) · ตั้งค่า`
- กลุ่ม "สำหรับนักพัฒนา" ล่างสุดตัวเล็ก: `API & เครื่องมือ · บันทึกการทำงาน` (รวม tab API + Tools + Logs)
- **การ์ดบัญชี LINE** มุมซ้ายบน (avatar + ชื่อ + จุดสถานะเดียว "เชื่อมต่อแล้ว/หลุด") กดเพื่อสลับบัญชี — ลบ dropdown บน header และ pill `authPill/nodePill`
- **ยังไม่มีบัญชี** → ทั้งหน้าเป็น QR + วิธีสแกน 3 ข้อ; Proxy/อีเมลอยู่ใน `<details>` "ตัวเลือกขั้นสูง"; ตัด step "Start"
- มือถือ: bottom tab bar 4 ปุ่ม
- สี: accent `#06C755`, ข้อความ `#1A1D21`, muted `#5B6472`, พื้น `#F5F6F8`, เส้น `#E6E8EC`; ฟอนต์ Noto Sans Thai
- ภาษาไทยล้วนในโหมดไทย (ดู 2.6)

### 2.2 แชท
- คลิกชื่อในรายการ → เปิดห้องทันที (ลบ `#targetInput` + `#messageCount` + ปุ่ม Open; MID พิมพ์ในช่องค้นหาได้)
- รายการแสดง avatar, ชื่อ, ข้อความล่าสุด, เวลา, badge unread
- Composer: แนบ / อีโมจิ / textarea / ปุ่ม "ส่ง" มีข้อความ; E2EE ระบบเลือกอัตโนมัติตามห้อง (ย้ายปุ่มกุญแจไปขั้นสูง)
- ชิปสถานะตัวเอง "● กำลังตอบ · ในกะ" (ดู 2.5)
- แบนเนอร์ตรวจ `Notification.permission` (ดู 2.7)

### 2.3 ส่งตามเวลา
- **หน้าเดียว = รายการงาน**: งานผิดพลาดลอยขึ้นบนสุดเป็นแถบแดง + ปุ่มแก้ (เชื่อมต่อใหม่ / ลองอีกครั้ง); แต่ละงาน: avatar+ชื่อผู้รับ, "ทุกวัน จ–ส 17:30", ป้ายสถานะมีสี, ตัวอย่างข้อความ, ประวัติส่ง 3 ครั้งล่าสุด ✓/✗, สวิตช์เปิด/ปิด, เมนู ⋯ (แก้ไข / ส่งทดสอบ / ลบ)
- **สร้างงาน = 3 ขั้น** `ส่งให้ใคร → ส่งอะไร → ส่งเมื่อไหร่`
  - ขั้น 2: การ์ดเลือกประเภท 4 แบบ, แสดงเฉพาะช่องของประเภทนั้น, ชิปตัวแปรภาษาไทย `{วันที่} {เวลา} {ชื่อผู้รับ} {เลขสุ่ม 1–100}`, แพทเทิร์น = "+ เพิ่มข้อความแบบที่ 2" ในขั้นนี้ (ไม่มีหน้าแยก), พรีวิวข้อความจริง
  - ขั้น 3: การ์ดความถี่ `ครั้งเดียว / ทุกวัน / ทุกสัปดาห์ / กำหนดเอง`, `input type=time` ช่องเดียว, ปุ่มวัน จ–อา, ช่วงวันที่พับได้; "ส่งทุก X นาที / หยุดหลัง N ครั้ง / เข้ารหัส" อยู่ใน "ตัวเลือกขั้นสูง" พร้อมคำเตือนเรื่องบัญชีถูกระงับ; การ์ดสรุปขวา + ชื่องานตั้งให้อัตโนมัติ + "ส่งทดสอบหาตัวเอง" + "บันทึกและเปิดใช้งาน"
- AI สร้างรูป ย้ายไป ตั้งค่า → AI; วิธีใช้เป็นไอคอน ? มุมขวา; Log เต็มอยู่เมนูนักพัฒนา

### 2.4 ตอบอัตโนมัติ
- **ยังไม่ได้ตั้งค่า** → checklist 3 ขั้นในหน้าเดียวกัน: (1) เลือกสมอง AI — Gemini ใส่ API key (แนะนำ) หรือ Ollama บนเครื่อง + "บันทึกและทดสอบ" (2) ใส่ข้อมูลร้าน (3) ทดลองคุย; สวิตช์หลักเทาจนครบ — **ไม่ต้องไป `/god`**
- **หลังตั้งค่า**: สวิตช์ใหญ่ + สถิติ "ตอบไปแล้ว N ข้อความ" → การ์ด "ตอบใครบ้าง" (เพื่อน / กลุ่ม → ชิปเลือกกลุ่ม) · "AI ที่ใช้ตอบ" (สรุป + ปุ่มแก้) · "ข้อมูลที่ให้ AI ใช้ตอบ"
- **ข้อมูลร้าน** แทน Knowledge/RAG: หัวข้อสำเร็จรูปเป็น `<details>` (เวลาเปิด–ปิด / เมนูราคา / วิธีสั่ง-จ่าย / สิ่งที่ห้ามตอบ-ส่งต่อให้คน) + "เพิ่มหัวข้อ"; ปุ่มบันทึกอันเดียว (index ให้เอง); Knowledge API ใน `<details>` ขั้นสูง
- **ทดลองคุย** อยู่ข้างข้อมูล (split view); ทุกคำตอบบอก "ตอบจาก: <หัวข้อ>"; ตอบไม่ได้ → "ไม่มีข้อมูล — ส่งต่อให้คน · เพิ่มคำตอบ"
- **คำถามที่ AI ตอบไม่ได้** = กล่องงานค้างใต้ข้อมูล: พิมพ์คำตอบ → "บันทึกเข้าข้อมูลร้าน" คลิกเดียว
- ข้อมูลร้านควรผูกกับ **บัญชี LINE** (แชร์ระหว่างผู้ใช้เว็บที่ดูแลบัญชีเดียวกัน) — ต้องตัดสินใจเรื่อง migration

### 2.5 KPI พนักงานตอบลูกค้า (เมนูใหม่, admin เท่านั้น)
สมมติฐาน: "พนักงาน" = ผู้ใช้เว็บ (role operator) ที่ตอบผ่านบัญชี LINE ของร้าน

ตัวชี้วัด:
1. **เวลาตอบครั้งแรก** — ลูกค้าทัก → พนักงานคนแรกกดส่ง
2. **% ตอบภายใน SLA** (ตั้งได้, ค่าเริ่มต้น 5 นาที)
3. **จำนวนแชทที่รับผิดชอบ** — คนที่ตอบคนแรกหลังข้อความเข้า (ไม่นับจำนวนข้อความ)
4. **ค้างเกิน 30 นาที** (เรียลไทม์)
5. **AI ตอบ vs คนตอบ**
6. **เวลาอยู่หน้าจอ** 4 สถานะ ต่อคน ต่อกะ:
   - `active` กำลังตอบ = แท็บมองเห็น + focus + (มีแชทรอตอบ หรือ keydown/click ใน 60 วิ)
   - `standby` สแตนด์บาย = แท็บมองเห็น + focus + ไม่มีแชทรอ
   - `away` สลับไปหน้าอื่น = `document.visibilityState === "hidden"` หรือ `!document.hasFocus()`
   - `offline` ไม่ได้เปิด = ไม่มี heartbeat > 2 นาที
   - downgrade `standby` → `idle` ถ้าไม่มี input เกิน 15 นาที (กันกรณีเปิดค้างจอที่สอง)
   - นับเฉพาะในกะ; ให้พนักงานกด "ติดงานอื่น" ได้; แสดงสถานะตัวเองที่มุมจอเสมอ (โปร่งใส)
   - ไม่วัด: ความพึงพอใจ (LINE ไม่มี rating)

หน้าจอ: 4 stat tiles (ตัวที่ 4 "ค้างตอบตอนนี้" สีแดงกดได้) → ตารางรายคน (แถบ % + แถว AI เทียบ) → กราฟแท่งรายวันมีเส้นเป้า → รายการค้างตอบ → ไทม์ไลน์เวลาอยู่หน้าจอรายคน + แถบเตือน "ลูกค้าทัก N ครั้งตอนพนักงานสลับไปหน้าอื่น"

### 2.6 คำศัพท์ (ไทย) — ใช้แทนของเดิมทุกที่
| เดิม | ใหม่ |
|---|---|
| LINE (tab) | แชท |
| AI Chat Bot / ตอบ LINE อัตโนมัติ | ตอบอัตโนมัติ |
| Bot / รายการบอท / Scheduler / ตารางเวลา | ส่งตามเวลา / งาน |
| Contact / Group | เพื่อน / กลุ่ม |
| Knowledge / RAG | ข้อมูลร้าน |
| Generate Knowledge / RAG | สร้างข้อมูลเริ่มต้น |
| chunks / Not indexed / สร้างดัชนี | (ซ่อน) / "AI อัปเดตแล้ว" |
| Tools / API / Logs | API & เครื่องมือ / บันทึกการทำงาน |
| Patterns / แพทเทิร์น | ข้อความแบบที่ 2, 3… |
| Run now / ส่งเดี๋ยวนี้ | ส่งทดสอบหาตัวเอง (ก่อนบันทึก) · ส่งตอนนี้ (ในเมนู ⋯) |
| `{1D} {2D} {3D} {date} {time} {rand:1-100}` | `{วันที่} {เวลา} {ชื่อผู้รับ} {เลขสุ่ม 1–100}` (map กลับเป็น token เดิมตอนบันทึก) |
| God / /god | ผู้ดูแลระบบ (ย้ายตั้งค่า AI ออกมา) |

### 2.7 นโยบายแจ้งเตือนเบราว์เซอร์
ตรวจ `Notification.permission` ทุกครั้งที่เข้าหน้าแชท:
- `default` → แบนเนอร์เหลือง + ปุ่ม "เปิดแจ้งเตือน" (ต้อง user gesture) + "ไว้ทีหลัง" (ซ่อน 24 ชม. ใน localStorage)
- `denied` → แบนเนอร์แดง บอกวิธีปลดที่ไอคอนกุญแจข้าง URL
- `granted` → `state.notificationsEnabled = true` อัตโนมัติ (ตัด opt-in กระดิ่ง)
- บันทึก permission ลง heartbeat ของ KPI

ความถี่:
- แท็บมองเห็น + focus → ไม่เด้ง system notification; ใช้ badge บน title + เสียงเบา 1 ครั้งต่อห้องใหม่
- แท็บซ่อน → เด้งได้: **1 ใบ/ห้อง/2 นาที** (`tag` เดิม, `renotify: false`, body = "n ข้อความใหม่"); **>3 ห้องใน 1 นาที → รวม 1 ใบ** `tag: linepassport:digest` "ลูกค้า N คนรอตอบ"; **รอเกิน SLA → เด้งซ้ำ 1 ครั้ง** `requireInteraction: true` + เสียงดังขึ้น
- เสียงเฉพาะห้องใหม่ + escalate
- นอกกะ / นอกเวลาทำการ → ไม่เด้ง (เว้นแต่เปิดเองในตั้งค่า)
- AI ตอบไปแล้ว → ไม่เด้ง

### 2.8 สถาปัตยกรรม refresh แชท (เป้าหมาย)
```
LINE ──long-poll (fetchOps)──▶ server (1 เธรด/บัญชีที่ล็อกอิน, ตลอดเวลา)
                                   │ inbox cache + per-room lastIncomingAt / lastOutgoingAt / firstUnansweredAt
                                   │ ใช้ร่วม: AI auto-reply · KPI · แจ้งเตือน
                                   ▼
                           SSE GET /api/events  ──▶ แท็บเปิด/ซ่อน (ไม่โดน timer throttling)
                           Web Push (VAPID)     ──▶ ไม่มีแท็บ (service worker ที่มีอยู่)
```
- เบราว์เซอร์: `EventSource` 1 ตัวแทน `setInterval` 3 ตัว; poll fallback ทุก 30 วิเฉพาะตอน SSE หลุด
- backoff เมื่อ error: 5 → 10 → 30 → 60 วิ
- ตัวบอกอายุข้อมูลบนหน้าจอ ("อัปเดตล่าสุด 3 วิที่แล้ว", แดงถ้า > 1 นาที)

---

## 3. รายการงาน (เรียงตามลำดับที่แนะนำ)

> ทุกงาน: แก้ใน `okline/webapp.py` เว้นแต่ระบุ · เพิ่ม/แก้ test ใน `tests/test_webapp.py` · รัน `pytest` ก่อน commit · อย่าเปลี่ยน protocol/endpoints ของ LINE ใน `okline/endpoints.py`, `okline/hmac_signer.py`

### Phase A — ฐานข้อมูลและ backend (ไม่กระทบ UI, ทำก่อนเพื่อให้เริ่มเก็บข้อมูล)

- [ ] **A1. บันทึกผู้ส่งทุกครั้งที่พนักงานส่ง**
  - ใน handler `POST /api/send` (~19301) และ `/api/send-media`: หลังส่งสำเร็จ เรียก `self.state.append_bot_log("operator_send", account_id=…, data={"webUserId": user["id"], "to": to, "kind": "text|media"})`
  - test: ส่งแล้ว `list_bot_logs` มี entry `operator_send` พร้อม `webUserId`

- [ ] **A2. Server poll ทุกบัญชีที่ล็อกอิน (ไม่ใช่เฉพาะที่เปิด AI)**
  - `AiAutoReplyEngine._run` / `poll_ai_auto_replies` (17948): แยกเป็น 2 ขั้น — (1) `refresh_inbox_for_all_accounts()` ดึง `inbox_message_boxes` ทุกบัญชีที่มี session ลง `inbox_snapshots` (2) ส่วน AI reply เฉพาะบัญชีที่ `enabled`
  - ต่อห้องเก็บลง store key `room_state:{accountId}:{mid}` = `{lastIncomingAt, lastOutgoingAt, firstUnansweredAt, answeredBy: "ai"|webUserId|null, status: "waiting"|"answered"|"closed"}`
  - อัปเดต `answeredBy` จาก A1 (คนแรกที่ส่งหลัง `firstUnansweredAt`) และจาก AI reply
  - ปิดห้องอัตโนมัติเมื่อเงียบ 24 ชม.
  - `GET /api/conversations` (19399) อ่านจาก cache เมื่อ snapshot อายุ < 5 วิ (มีอยู่แล้วใน `chat_summary_map`) — ตรวจว่าทุก path ใช้ cache

- [ ] **A3. Presence heartbeat**
  - endpoint `POST /api/presence` body `{state: "active"|"standby"|"away", notification: "granted"|"denied"|"default", ts}`; เก็บเป็นช่วง `{webUserId, state, from, to}` รวมช่วงต่อเนื่อง (ไม่เก็บทุก 30 วิ); ไม่มี heartbeat > 120 วิ = `offline`
  - JS: ส่งทุก 30 วิ + ทันทีเมื่อ state เปลี่ยน (`visibilitychange`, `focus/blur`, keydown/click ครั้งแรกหลัง idle)
  - test: ลำดับ heartbeat → ช่วงที่รวมถูกต้อง

- [ ] **A4. ตั้งค่ากะ + SLA + เวลาทำการ**
  - store key `kpi_settings` = `{slaSeconds: 300, businessHours: {...}, shifts: {webUserId: [{days, start, end}]}}`; endpoints GET/PUT (admin)

- [ ] **A5. KPI aggregation endpoint**
  - `GET /api/kpi?accountId=&from=&to=` → `{tiles, perUser[], daily[], pendingNow[], presence[]}` คำนวณจาก A1–A4; นับเฉพาะแชทส่วนตัวที่ลูกค้าทักก่อน ไม่นับกลุ่ม/งานส่งตามเวลา
  - test: fixture log + room_state → ค่า first response, SLA%, presence totals ถูก

### Phase B — Refresh / แจ้งเตือน (JS + server)

- [ ] **B1. ตัดเงื่อนไขหยุด refresh ตอนแท็บซ่อน**
  - `refreshConversationsInBackground` (7555): ลบ `if (!state.notificationsEnabled && !lineViewIsVisible) return;` — ให้ยิงเสมอเมื่อมีบัญชี (server ตอบจาก cache แล้วใน A2 จึงถูก)
- [ ] **B2. Auto-enable notification เมื่อ `granted` + แบนเนอร์ตามสถานะ** (2.7) — เพิ่ม HTML แบนเนอร์เหนือ `#contactSearchRow`, i18n keys `notify.banner_*`, localStorage `okline.notifyBannerSnoozedUntil`
- [ ] **B3. นโยบายความถี่** ใน `processNewMessageNotifications` / `showSystemNotification` (7487–7553):
  - เพิ่ม `document.hasFocus()` ใน `isOpen`; path "visible แต่คนละห้อง" = toast + badge เท่านั้น
  - `renotify: false`; map `state.lastNotifiedAt[mid]` cooldown 120 วิ; `state.pendingCount[mid]` ใส่ body
  - `visibleAlerts.length > 3` → 1 ใบ `tag: "linepassport:digest"`
  - timer escalate: ห้อง unread ต่อเนื่อง > `slaSeconds` และไม่มี outgoing → 1 ใบ `requireInteraction: true`
  - เสียงเฉพาะห้องใหม่ + escalate
  - test: ใช้ fake `Notification` นับจำนวนใบต่อสถานการณ์
- [ ] **B4. SSE `/api/events`** — `text/event-stream` ส่ง `conversation_updated` เมื่อ A2 เห็นการเปลี่ยนแปลง; JS ใช้ `EventSource` แทน `setInterval` ของ conversations/messages; fallback poll 30 วิเมื่อ `onerror`; backoff 5→10→30→60
- [ ] **B5. ตัวบอกอายุข้อมูล** — ชิป "อัปเดตล่าสุด X วิที่แล้ว" ในหัวรายชื่อ, แดงเมื่อ > 60 วิ
- [ ] **B6. (ทีหลัง) long-poll `fetchOps`** แทน `getMessageBoxes` ใน A2; fallback เมื่อล้ม 3 ครั้งติด
- [ ] **B7. (ทีหลัง) Web Push** — VAPID key ใน state dir, `pushManager.subscribe`, service worker รับ `push`, ใช้ policy เดียวกับ B3

### Phase C — โครงหน้าใหม่ (UI)

- [ ] **C1. Sidebar + ลบแท็บบน** — แทน `.tabs` (3549) ด้วย `<aside>` 5 เมนู + กลุ่มนักพัฒนา; `state.tab` คงค่าเดิม (`line|assistant|bot|kpi|settings|incoming-api|tools|logs`) เพื่อไม่ต้องแก้ logic; ลบ `#accountSelect` บน header → การ์ดบัญชีใน sidebar (เปิด popover เลือกบัญชี); ลบ `#authPill #nodePill`
- [ ] **C2. หน้าแรกไม่มีบัญชี** — เมื่อ `accounts.length === 0` แสดง `#loginPanel` เต็มหน้าแบบ QR ทันที (เรียก `beginAddAccount` อัตโนมัติ), Proxy ใน `<details>`; ตัด step 1
- [ ] **C3. แชท: คลิกแล้วเปิด** — ลบ `#targetInput #messageCount #loadMessagesButton`; `renderAllList` ผูก click → `openConversation(mid)`; ช่องค้นหารับ MID; ปุ่มส่งมีคำว่า "ส่ง"; E2EE auto (เก็บ toggle ไว้ในเมนู ⋯)
- [ ] **C4. มือถือ** — media query ≤ 640px: sidebar → bottom tab bar; header ย่อ
- [ ] **C5. i18n** — เปลี่ยนคำตามตาราง 2.6 ใน `I18N` ทั้ง th/en; grep หา string อังกฤษที่ hardcode นอก `t()`

### Phase D — ส่งตามเวลา

- [ ] **D1. รายการงานใหม่** — `renderSchedules` (9067): resolve `job.to` → ชื่อ+avatar จาก `state.contacts/groups`; ป้ายสถานะสี; งาน error ลอยบนสุดเป็นแถบแดง; สวิตช์ + เมนู ⋯; ประวัติ 3 ครั้งจาก `list_bot_logs(scheduleId)`
- [ ] **D2. ฟอร์ม 3 ขั้น** — แยก `#scheduleForm` เป็น 3 pane + stepper; state `scheduleDraft`; ชื่องานอัตโนมัติ `"{ประเภท/ข้อความสั้น} → {ผู้รับ}"`; ตัวแปรไทย map ↔ token เดิม; พรีวิว; ปุ่ม "ส่งทดสอบหาตัวเอง" (ส่งไป mid ของโปรไฟล์ตัวเอง)
- [ ] **D3. ขั้น "ส่งเมื่อไหร่"** — preset ครั้งเดียว/ทุกวัน/ทุกสัปดาห์/กำหนดเอง → map เป็น `mode/interval/window/activeFrom/activeUntil` เดิม; `input type=time`; ปุ่มวัน; ขั้นสูงมีคำเตือน; ค่าเริ่มต้น interval = วันละครั้ง (ไม่ใช่ 15 นาที)
- [ ] **D4. แพทเทิร์นรวมในขั้น 2** — "+ เพิ่มข้อความแบบที่ 2" สร้าง pattern ให้อัตโนมัติ (หมวด "ทั่วไป" ถ้าไม่มี); ซ่อนหน้า `#botPatterns #botPatternCategories` ไว้ใต้ขั้นสูง
- [ ] **D5. ย้าย AI สร้างรูป → ตั้งค่า → AI**; วิธีใช้ → ปุ่ม ? ; Log → เมนูนักพัฒนา

### Phase E — ตอบอัตโนมัติ

- [ ] **E1. ย้ายตั้งค่า AI ออกจาก `/god`** — endpoint `GET/PUT /api/ai/settings` (admin) รองรับ `provider: "gemini"|"ollama"`; สำหรับ Gemini ใช้ key store เดียวกับ AI สร้างรูป; `okline/assistant.py` เพิ่ม Gemini chat+embedding client (หรือ fallback embedding แบบ local เมื่อใช้ Gemini); ปุ่ม "บันทึกและทดสอบ"
- [ ] **E2. Checklist setup state** — ถ้า `!configured` แสดง 3 ขั้น (2.4) แทน pill; สวิตช์ disabled จนครบ
- [ ] **E3. ข้อมูลร้านแบบหัวข้อ** — เก็บเป็น sections `[{id, title, body}]` แล้ว serialize เป็น markdown `## title\nbody` ให้ indexer เดิม; บันทึก = reindex อัตโนมัติ (debounce 2 วิ); ซ่อน Knowledge API ใน `<details>`
- [ ] **E4. Split view ข้อมูล + ทดลองคุย** — รวม `#assistantChatView` เข้ากับ `#assistantKnowledgeView`; response ต้องคืน `sources: [sectionTitle]` → แสดง "ตอบจาก: …"; ตอบไม่ได้ → ลิงก์ "เพิ่มคำตอบ" prefill กล่อง E5
- [ ] **E5. กล่องคำถามที่ตอบไม่ได้** — จาก `assistantUnansweredList` เดิม: input + ปุ่ม "บันทึกเข้าข้อมูลร้าน" (append ลง section "คำถามที่พบบ่อย" + reindex)
- [ ] **E6. (ตัดสินใจ) ผูกข้อมูลร้านกับบัญชี LINE** — migration จาก per-webUser → per-account; ต้องถามเจ้าของโปรเจกต์ก่อนทำ

### Phase F — KPI UI

- [ ] **F1. หน้า KPI** (admin) — ตาม 2.5 ใช้ข้อมูลจาก A5; ช่วงเวลา วันนี้/7 วัน/30 วัน/กำหนดเอง; ดาวน์โหลด CSV
- [ ] **F2. ชิปสถานะตัวเองในหน้าแชท + ปุ่ม "ติดงานอื่น"** (ส่ง state `busy` ใน A3)
- [ ] **F3. ตั้งค่า → KPI** — UI สำหรับ A4

---

## 4. เกณฑ์ตรวจรับ (acceptance)

- ผู้ใช้ใหม่เปิดเว็บครั้งแรก เห็น QR ภายใน 1 คลิก (หรือ 0 คลิก) และหลังสแกนเข้าหน้าแชทอัตโนมัติ
- คลิกชื่อในรายชื่อแล้วเห็นข้อความทันที ไม่มีช่องกรอก MID
- ไม่มีคำอังกฤษใน UI โหมดไทย ยกเว้นชื่อเฉพาะ (LINE, LinePassport, API key, Gemini, Ollama)
- สร้างงานส่งตามเวลา "ทุกวัน 09:00 ให้กลุ่ม X" ได้ใน ≤ 6 คลิก และกด "ส่งทดสอบหาตัวเอง" ได้ก่อนบันทึก
- ตั้งค่า AI ตอบอัตโนมัติด้วย Gemini key ได้จากหน้าตอบอัตโนมัติ โดยไม่ต้องเปิด `/god`
- แท็บซ่อน 10 นาที แล้วมีลูกค้าทัก → ได้ notification ภายใน 15 วิ และได้ **ไม่เกิน 1 ใบต่อห้องใน 2 นาที**
- หน้า KPI แสดงเวลาตอบครั้งแรกและ presence ของพนักงานได้หลังเก็บข้อมูล 1 วัน
- `pytest` ผ่านทั้งหมด; ไม่มี regression ใน `tests/test_webapp.py`

---

## 5. ข้อควรระวัง

- LinePassport ใช้บัญชี LINE ส่วนตัวผ่าน protocol ของ Chrome extension — การส่งถี่/จำนวนมากเสี่ยงถูกระงับ ค่าเริ่มต้นทุกอย่างต้องเอียงไปทาง "ส่งน้อย"
- อย่าแตะ `okline/ltsm/*`, `hmac_signer.py`, `endpoints.py` ในงานชุดนี้
- `webapp.py` ไฟล์เดียวใหญ่มาก — ถ้าจะแยกไฟล์ ให้ทำเป็นงานแยกต่างหากหลัง Phase A–B เสร็จ ไม่ปนกับงาน feature
- ทุก endpoint ใหม่ต้องผ่าน `_require_permission` ตาม `ROLE_DEFINITIONS` และเคารพ rate limit table (~12200)
- Presence tracking ต้องโปร่งใสกับพนักงาน: แสดงสถานะตัวเองเสมอ และมีคำอธิบายในหน้าตั้งค่า
