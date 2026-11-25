# LinePassport Installation Guide

## Production-required environment

When LinePassport is exposed through a public hostname, configure these values before
the first startup:

```env
OKLINE_DATABASE_URL=postgresql://USER:PASSWORD@POSTGRES_HOST:5432/DATABASE
LINEPASSPORT_SETUP_TOKEN=REPLACE_WITH_A_RANDOM_SETUP_TOKEN
LINEPASSPORT_SECRET_KEY=REPLACE_WITH_A_FERNET_KEY
LINEPASSPORT_SECURE_COOKIES=1
LINEPASSPORT_TRUST_PROXY=1
```

Generate the two secrets locally:

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

- `LINEPASSPORT_SETUP_TOKEN` protects creation of the first administrator on a public bind.
- `LINEPASSPORT_SECRET_KEY` encrypts stored AI API keys. Keep it unchanged and back it up.
- LINE session tokens, certificates, and E2EE key material managed by the web app are
  encrypted with the same key. Losing the key requires adding those LINE accounts again.
- Set `LINEPASSPORT_TRUST_PROXY=1` only when all traffic reaches the app through your
  trusted reverse proxy. This lets rate limiting use the original client IP.
- Mount `/data` on persistent storage because it contains LINE sessions and the fallback
  encryption key.
- Run exactly one application replica for each database. The app enforces this with a
  PostgreSQL advisory lock so a second scheduler cannot send duplicate messages.
- Put the built-in HTTP server behind an HTTPS reverse proxy; do not expose port 8765
  directly to the Internet.

คู่มือนี้ใช้สำหรับติดตั้ง **LinePassport** จากซอร์สโค้ดใน repository นี้

> ห้ามใช้ `pip install okline` หากต้องการฟีเจอร์ LinePassport เวอร์ชันล่าสุด
> เพราะอาจได้แพ็กเกจต้นฉบับที่ไม่มีระบบสมาชิก, PostgreSQL, Scheduler และ AI
> ที่พัฒนาเพิ่มใน repository นี้

## ความต้องการของระบบ

- Python 3.9 ขึ้นไป (แนะนำ Python 3.12; ถ้าจะใช้ Claude ตอบลูกค้าอัตโนมัติ ต้องใช้ Python 3.10 ขึ้นไป)
- Node.js 18 ขึ้นไป
- Git
- PostgreSQL (แนะนำ PostgreSQL 16)
- RAM อย่างน้อย 1 GB สำหรับการใช้งานทั่วไป

ตรวจสอบโปรแกรมที่ติดตั้งแล้ว:

```text
python --version
node --version
git --version
```

Node.js จำเป็นสำหรับรัน `ltsm.wasm` เพื่อสร้างลายเซ็น `X-Hmac` ที่ LINE ใช้

## ติดตั้งบน Windows

เปิด PowerShell แล้วรัน:

```powershell
git clone https://github.com/TheBoy/LinePassport.git
cd LinePassport

python -m venv .venv
Set-ExecutionPolicy -Scope Process Bypass
.\.venv\Scripts\Activate.ps1

python -m pip install --upgrade pip
pip install -e ".[web,qr]"
```

## ติดตั้งบน Linux หรือ macOS

```bash
git clone https://github.com/TheBoy/LinePassport.git
cd LinePassport

python3 -m venv .venv
source .venv/bin/activate

python -m pip install --upgrade pip
pip install -e ".[web,qr]"
```

## เตรียม PostgreSQL

เข้าสู่ PostgreSQL ด้วยผู้ใช้ที่มีสิทธิ์สร้างฐานข้อมูล:

```bash
psql -U postgres
```

สร้างผู้ใช้และฐานข้อมูล:

```sql
CREATE USER linepassport WITH PASSWORD 'CHANGE_THIS_PASSWORD';
CREATE DATABASE linepassport OWNER linepassport;
```

ออกจาก `psql`:

```text
\q
```

หากรหัสผ่านมีอักขระพิเศษ เช่น `@`, `:`, `/` หรือ `#` ต้อง URL-encode
รหัสผ่านก่อนนำไปใส่ใน PostgreSQL URL

ตัวอย่าง URL:

```text
postgresql://linepassport:CHANGE_THIS_PASSWORD@127.0.0.1:5432/linepassport
```

## เปิด LinePassport

### Windows PowerShell

```powershell
$env:OKLINE_DATABASE_URL = "postgresql://linepassport:CHANGE_THIS_PASSWORD@127.0.0.1:5432/linepassport"
python -m okline web --host 127.0.0.1 --port 8765 --state-dir .okline
```

### Linux หรือ macOS

```bash
export OKLINE_DATABASE_URL="postgresql://linepassport:CHANGE_THIS_PASSWORD@127.0.0.1:5432/linepassport"
python -m okline web --host 127.0.0.1 --port 8765 --state-dir .okline
```

จากนั้นเปิด:

```text
http://127.0.0.1:8765
```

หยุดเซิร์ฟเวอร์ด้วย `Ctrl+C`

## การตั้งค่าครั้งแรก

1. เปิด LinePassport ที่ `http://127.0.0.1:8765`
2. เข้าเมนู **Settings** และตั้งอีเมลกับรหัสผ่านของ Admin
3. เข้าเมนูจัดการบัญชี LINE และกดเพิ่มบัญชี
4. ทำตามขั้นตอน Start, QR, PIN และ Done
5. เลือกบัญชี LINE จาก dropdown ด้านบน
6. ตั้งค่า Gemini, nanobananaapi.ai หรือ fal.ai ในหน้า AI เมื่อต้องการสร้างรูป
7. หลังเปิดระบบ Auth แล้ว ผู้ใช้อื่นสามารถสมัครผ่านหน้า Register ได้

บัญชี LINE, Pattern, Scheduler, AI Settings และ Bot Log ของสมาชิกแต่ละคน
ถูกแยกออกจากกันตามเจ้าของข้อมูล

## ติดตั้งด้วย Docker

Dockerfile ใน repository ติดตั้ง Python, Node.js และ dependency ที่จำเป็นให้แล้ว

### 1. สร้าง network และ volume

```bash
docker network create linepassport-network
docker volume create linepassport-postgres
docker volume create linepassport-data
```

### 2. เปิด PostgreSQL

```bash
docker run -d \
  --name linepassport-db \
  --restart unless-stopped \
  --network linepassport-network \
  -e POSTGRES_DB=linepassport \
  -e POSTGRES_USER=linepassport \
  -e POSTGRES_PASSWORD=CHANGE_THIS_PASSWORD \
  -v linepassport-postgres:/var/lib/postgresql/data \
  postgres:16
```

### 3. Build LinePassport

```bash
docker build -t linepassport:local .
```

### 4. เปิด Application

```bash
docker run -d \
  --name linepassport \
  --restart unless-stopped \
  --network linepassport-network \
  -p 8765:8765 \
  -e PORT=8765 \
  -e OKLINE_STATE_DIR=/data \
  -e OKLINE_DATABASE_URL=postgresql://linepassport:CHANGE_THIS_PASSWORD@linepassport-db:5432/linepassport \
  -e LINEPASSPORT_SETUP_TOKEN=REPLACE_WITH_A_RANDOM_SETUP_TOKEN \
  -e LINEPASSPORT_SECRET_KEY=REPLACE_WITH_A_FERNET_KEY \
  -e LINEPASSPORT_SECURE_COOKIES=0 \
  -v linepassport-data:/data \
  linepassport:local
```

ตรวจสอบสถานะ:

```bash
docker ps
docker logs -f linepassport
```

เปิด `http://127.0.0.1:8765`

> **หยุดหรืออัปเดต container ระหว่างที่งานส่งตามเวลากำลังส่งอยู่:** `docker stop`
> และการ deploy ใหม่จะส่งสัญญาณ SIGTERM แล้วรอ 10 วินาทีก่อนบังคับปิด LinePassport
> รับสัญญาณนี้เอง (image เรียกด้วย `exec` จึงไม่จำเป็นต้องใส่ `--init` หรือ `init: true`
> แต่ใส่ไว้ก็ไม่เสียหาย) แล้วปิดตัวอย่างเป็นระเบียบ งานที่ส่งไปแล้วบางแชทจะหยุดก่อนแชทถัดไป
> และส่งต่อเฉพาะแชทที่เหลือหลังเปิดกลับมา แชทที่ได้รับไปแล้วจะไม่ได้รับซ้ำ แม้ container
> จะถูกบังคับปิดหรือเซิร์ฟเวอร์ดับกะทันหันก็ตาม ดูรายละเอียดที่
> [Scheduled sends and restarts](docs/web.md#scheduled-sends-and-restarts)

## ติดตั้งบน Coolify

1. สร้าง PostgreSQL resource ใน Coolify
2. สร้าง Application จาก repository:

   ```text
   https://github.com/TheBoy/LinePassport
   ```

3. เลือก Build Pack เป็น `Dockerfile`
4. ตั้ง Container Port เป็น `8765`
5. เพิ่ม Persistent Storage โดย mount ไปที่ `/data`
6. เพิ่ม Environment Variables:

   ```env
   PORT=8765
   OKLINE_STATE_DIR=/data
   OKLINE_DATABASE_URL=postgresql://USER:PASSWORD@POSTGRES_HOST:5432/DATABASE
   LINEPASSPORT_SETUP_TOKEN=REPLACE_WITH_A_RANDOM_SETUP_TOKEN
   LINEPASSPORT_SECRET_KEY=REPLACE_WITH_A_FERNET_KEY
   LINEPASSPORT_SECURE_COOKIES=1
   LINEPASSPORT_TRUST_PROXY=1
   ```

7. เชื่อมโดเมนและเปิด HTTPS
8. Deploy และตรวจสอบว่า container มีสถานะ `healthy`

ห้ามเปิด PostgreSQL port สู่ Internet และไม่ควรเปิด LinePassport แบบ HTTP
สู่สาธารณะโดยไม่มี HTTPS

## อัปเดตระบบ

ก่อนอัปเดตทุกครั้ง ให้สำรองข้อมูลตามหัวข้อ "สำรองข้อมูล" ก่อน บางเวอร์ชันเปลี่ยนรูปแบบข้อมูลตอนเปิดระบบครั้งแรก
ถ้าจะย้อนกลับไปเวอร์ชันก่อนหน้า ต้องกู้ข้อมูลที่สำรองไว้ด้วย

### ติดตั้งจากซอร์ส

```bash
git pull origin main
python -m pip install -e ".[web,qr]"
```

จากนั้น restart LinePassport

### Docker

```bash
git pull origin main
docker build -t linepassport:local .
docker rm -f linepassport
```

แล้วรันคำสั่ง `docker run` ในหัวข้อ Docker อีกครั้ง ข้อมูลจะยังอยู่ใน PostgreSQL
และ volume `linepassport-data`

สำหรับ Coolify ให้เปิด Auto Deploy หรือกด Redeploy หลัง repository มี commit ใหม่

## ลูกค้า (CRM) และ Telegram

- ข้อมูลลูกค้า (การ์ดลูกค้า แท็ก โน้ต งานติดตาม กลุ่มลูกค้า) เก็บใน
  `crm.sqlite3` ภายใน state directory หรือในฐาน PostgreSQL เดียวกันเมื่อตั้ง
  `OKLINE_DATABASE_URL` (ตาราง `crm_*`) — ไม่ต้องตั้งค่าเพิ่ม
- **บอทแจ้งเตือนพนักงาน:** ตั้งค่า → Telegram → วาง token จาก @BotFather แล้วให้ทุกคน
  กด "เชื่อม Telegram ของฉัน"
- **Telegram Bot เป็นช่องทางแชท:** เพิ่มจากเมนูบัญชี → "+ เพิ่ม Telegram Bot"
  (ใช้บอทคนละตัวกับบอทแจ้งเตือน) ระบบใช้ long polling จึงต้องออกเน็ตไปที่
  `api.telegram.org` ได้ แต่ไม่ต้องเปิดพอร์ตหรือมี URL สาธารณะ
- **Telegram บัญชีส่วนตัว:** ติดตั้งส่วนเสริมก่อน

  ```bash
  pip install -e ".[telegram]"
  ```

  แล้วเพิ่มจากเมนูบัญชี → "+ เพิ่ม Telegram ส่วนตัว" (ต้องมี api_id / api_hash จาก
  my.telegram.org) — Docker image ติดตั้งส่วนเสริมนี้ให้แล้ว
- ถ้าเครื่องออกเน็ตตรงไป Telegram ไม่ได้ ตั้ง `LINEPASSPORT_TELEGRAM_PROXY`
  เช่น `socks5://user:pass@host:1080`
- token ของบอทและ session ของบัญชีส่วนตัวถูกเข้ารหัสด้วย `LINEPASSPORT_SECRET_KEY`
  เหมือน session ของ LINE
- **ลบบัญชีแล้วลูกค้าหายไปด้วย:** เมื่อลบบัญชี LINE, LINE OA หรือ Telegram ในหน้าจัดการบัญชี
  ลูกค้าที่คุยกับบัญชีนั้นบัญชีเดียวจะถูกลบไปด้วย พร้อมแท็ก โน้ต งานติดตาม และประวัติ
  ส่วนลูกค้าที่คุยกับบัญชีอื่นด้วยยังอยู่ หน้าต่างยืนยันบอกจำนวนลูกค้าที่จะถูกลบก่อนกดลบ
  และลบแล้วกู้คืนไม่ได้ (ยกเว้นจากไฟล์สำรอง)
- **เวลาแบบ 24 ชั่วโมง:** ทุกหน้าแสดงเวลาแบบ 24 ชั่วโมง (เช่น 14:05) แม้ใช้ภาษาอังกฤษ
  และช่องกรอกเวลาเป็นรายการชั่วโมง (00–23) กับนาที
- **รีเฟรชรายชื่อโมเดล:** ตั้งค่า → AI กด "รีเฟรชรายชื่อโมเดล" เพื่อดึงรายชื่อโมเดลจากผู้ให้บริการ
  AI ที่เลือก (ใช้ API key ที่กรอกไว้ หรือ key ที่บันทึกไว้ของผู้ให้บริการนั้น) แล้วเลือกจากรายการได้
  ส่วนรูปภาพ AI ปุ่มเดียวกันจะเพิ่มโมเดลสร้างรูปของ Gemini ที่ key นี้ใช้ได้
  ระบบรอผู้ให้บริการได้นานสุด 30 วินาที ถ้าไม่ตอบจะขึ้นว่าโหลดรายชื่อโมเดลไม่สำเร็จ

## AI สำหรับตอบอัตโนมัติ

เจ้าของร้านเลือกผู้ให้บริการ AI และใส่ API key ของตัวเองที่ ตั้งค่า → AI → "AI ที่ใช้ตอบลูกค้า"
(หรือขั้นที่ 1 ของการตั้งค่าตอบอัตโนมัติ) ไม่มี AI ส่วนกลางให้ใช้ร่วมกันแล้ว

- **ยอดนิยม:** Gemini (Google), Claude (Anthropic), ChatGPT (OpenAI)
- **ผู้ให้บริการอื่น:** GLM (Z.ai), DeepSeek, Qwen (Alibaba Cloud), Grok (xAI), Mistral AI,
  Kimi (Moonshot AI), OpenRouter
- **เชื่อม API เอง:** Custom API ใช้กับเซิร์ฟเวอร์ใดก็ได้ที่รองรับ API แบบเดียวกับ OpenAI
  (`POST {base URL}/chat/completions` และ `GET {base URL}/models` สำหรับรีเฟรชรายชื่อโมเดล)
  กรอก Base URL เช่น `https://api.example.com/v1` ชื่อโมเดล (ต้องกรอก) และ API key ถ้าเซิร์ฟเวอร์ต้องใช้
  เซิร์ฟเวอร์ LinePassport เป็นผู้เรียกที่อยู่นี้เอง จึงต้องเป็นที่อยู่ `https://` สาธารณะ
  ที่อยู่ภายใน เช่น `127.0.0.1`, `10.x.x.x`, `192.168.x.x` หรือ `169.254.169.254` จะถูกปฏิเสธ
  ชื่อโดเมนภาษาไทยให้ใส่แบบ `xn--` และ key จะถูกส่งไปที่ที่อยู่ที่บันทึกไว้เท่านั้น
  ถ้าเปลี่ยนไปใช้เซิร์ฟเวอร์อื่น ต้องกรอก key ของเซิร์ฟเวอร์นั้นใหม่
- ผู้ให้บริการแต่ละรายเก็บ API key แยกกัน สลับผู้ให้บริการแล้ว key ของรายหนึ่งจะไม่ถูกส่งไปให้อีกราย
  และหน้าเว็บแสดง key ที่บันทึกไว้แค่ `••••abcd` (key ที่สั้นกว่า 16 ตัวอักษรแสดงแค่ `••••`)
  ส่วนพนักงานไม่เห็นทั้ง key และที่อยู่ของ Custom API
- GLM (Z.ai) ใช้ได้เฉพาะ API key แบบ Standard ส่วน key ของ Coding Plan ใช้ที่นี่ไม่ได้
- ระบบตอบอัตโนมัติทำงานได้พร้อมกัน 4 งานสำหรับทุก workspace รวมกัน และแต่ละ workspace ใช้ได้ไม่เกิน 2 งาน
  ข้อความ Telegram และ LINE OA ที่เกินจะรอในคิวซึ่งรอได้ workspace ละ 20 ข้อความ
  ถ้าคิวเต็ม ข้อความที่รอนานที่สุดจะถูกข้ามและมีบันทึกในบันทึกการทำงาน
  ส่วนข้อความ LINE ที่เกินก็รอเช่นกัน เมื่อมีที่ว่าง ระบบจะตอบแชทที่รอนานที่สุดจากที่การตรวจรอบล่าสุดพบ
  (ไม่เกิน 8 แชทต่อบัญชี โดยผลัดกันระหว่างบัญชีของ workspace) สลับกับคิว ส่วนแชทที่เหลือรอการตรวจรอบถัดไป
  แชทที่ส่งมารวดเดียวเกิน 10 ข้อความที่ LINE แสดงให้เห็น (เช่นส่งรูปเป็นอัลบั้ม) จะได้ตอบหลังแชทที่รออยู่แล้ว
  ตอนที่การตรวจรอบล่าสุดเห็นว่าแชทนั้นยังไม่มีอะไรต้องตอบ แต่อาจได้ตอบก่อนแชทที่เริ่มรอก่อนแชทนั้นไม่กี่วินาที
  ในบัญชีที่มีแชทเกิน 500 แชท แชทแบบนี้ยังอาจแซงแชทที่รอนานกว่าได้ ถ้าข้อความก่อนหน้าของลูกค้ายังไม่มีใครตอบ
  (และในบัญชีที่มีแชท 1,000 แชทขึ้นไป ถ้าลูกค้าเพิ่งทักบัญชีนี้ครั้งแรก หรือแชทของลูกค้าไม่อยู่ใน 1,000 แชทล่าสุด)
- **Claude** ใช้แพ็กเกจ `anthropic` ซึ่งต้องใช้ **Python 3.10 ขึ้นไป** ติดตั้งด้วย

  ```bash
  pip install -e ".[claude]"
  ```

  Docker image ติดตั้งให้แล้ว ถ้าเซิร์ฟเวอร์ไม่มีแพ็กเกจนี้ Claude จะขึ้นว่าใช้ไม่ได้บนเซิร์ฟเวอร์นี้
- Knowledge ส่วนกลางในหน้า God (แท็บ "Knowledge / RAG") ใช้กับทุก workspace โดยค้นด้วยคำสำคัญ
  แล้วส่งเฉพาะส่วนที่ตรงกับคำถาม (ไม่เกิน 3 ส่วน) ไปยังผู้ให้บริการ AI ของ workspace นั้น จึงไม่ควรใส่ข้อมูลลับ
  คำภาษาอังกฤษต้องตรงทั้งคำหรือเป็นรูปที่ยาวหรือสั้นกว่าของคำนั้น ไม่นับตัวอักษรที่อยู่กลางคำอื่น
  และในหน้าของร้านจะอ้างที่มาว่า "ข้อมูลกลางจากผู้ดูแลระบบ" แทนชื่อไฟล์ในหน้า God
- **อัปเดตจากเวอร์ชันที่ใช้ Ollama หรือ AI ส่วนกลาง:** ก่อนอัปเดตให้สำรองข้อมูลตามหัวข้อ "สำรองข้อมูล"
  (ถ้าใช้ PostgreSQL ให้ใช้ `pg_dump` ส่วนระบบที่เก็บข้อมูลเป็นไฟล์ อย่างน้อยให้สำรอง `global_ai.json`,
  `ai_auto_reply.json` และ `bot_logs.json` ในโฟลเดอร์ state) ตอนเปิดระบบครั้งแรกหลังอัปเดต
  ระบบจะย้ายการตั้งค่า AI ของทุกร้านไปเป็นรูปแบบใหม่และลบการตั้งค่า AI ส่วนกลาง ซึ่งย้อนกลับเองไม่ได้
  เวอร์ชันเก่าอ่านรูปแบบใหม่ไม่ได้ (key ของ Gemini ที่ร้านใส่ไว้จะเหมือนหายไป และ AI ส่วนกลางก็ไม่มีแล้ว)
  ถ้าจะย้อนกลับไปเวอร์ชันก่อนหน้า ต้องกู้ข้อมูลที่สำรองไว้ด้วย
  ระบบเลิกรองรับ Ollama และ AI ส่วนกลางในหน้า God แล้ว ร้านที่เคยใช้สองอย่างนี้จะถูกปิดการตอบอัตโนมัติ
  และเห็นข้อความแจ้งให้เลือกผู้ให้บริการ AI และใส่ API key แล้วเปิดสวิตช์ตอบอัตโนมัติอีกครั้ง
  บัญชีที่ถูกปิดแบบนี้จะมีข้อความบอกในหน้าตอบอัตโนมัติจนกว่าจะเปิดอีกครั้ง ร้านที่เคยใช้ AI ส่วนกลาง
  แบบ "ตอบเฉพาะจากข้อมูลร้าน" ยังตั้งค่านี้ไว้เหมือนเดิม
  ถ้าติดตั้งเองและเคยใช้ Ollama บนเครื่อง ต้องใช้ API key ของผู้ให้บริการบนคลาวด์ หรือเซิร์ฟเวอร์
  ที่รองรับ API แบบ OpenAI ผ่านที่อยู่ `https://` สาธารณะ (Custom API)
- **ร้านที่ตั้งค่า Gemini ไว้ก่อนเวอร์ชันนี้:** ยังใช้โมเดลที่บันทึกไว้ ซึ่งส่วนใหญ่เป็น `gemini-2.5-flash`
  (หน้าตั้งค่าเดิมบันทึกโมเดลนี้ให้เสมอ) และ Google จะส่งคำขอของโมเดลนี้ไปโมเดลอื่นตั้งแต่ 1 ธันวาคม 2026
  ถ้าจะใช้ค่าเริ่มต้นใหม่ `gemini-3.8-flash` ให้เลือกโมเดลนี้ (หรือลบชื่อโมเดลในช่องออก)
  ที่ ตั้งค่า → AI แล้วกด "บันทึกและทดสอบ"

## LINE OA (ผ่าน LINE Official Account Manager)

- **ก่อนเพิ่ม:** บัญชี LINE ที่จะใช้สแกนต้องเป็นสมาชิกของ OA ใน OA Manager ไปที่
  ตั้งค่า → จัดการสิทธิ์ → เพิ่มสมาชิก (สิทธิ์ Operator ก็พอ) แล้วเปิดลิงก์คำเชิญด้วย
  บัญชี LINE นั้นเพื่อรับคำเชิญ ถ้าบัญชีนั้นยังไม่มี LINE Business ID ระบบ LINE จะให้ตั้งชื่อ
  Business ID ตอนรับคำเชิญ
- **เพิ่ม:** ตั้งค่า → จัดการบัญชี → "+ เพิ่ม LINE OA" แล้วสแกน QR ด้วยแอป LINE ของบัญชีนั้น
  กรอกรหัส 4 หลักในมือถือถ้ามีขึ้น ถ้าบัญชีดูแลหลาย OA จะให้เลือกว่าจะเพิ่มตัวไหน
- ล็อกอินด้วยอีเมลของ LINE Business ID ใช้ไม่ได้ (หน้าล็อกอินนั้นมี reCAPTCHA และรหัส
  ยืนยันทางอีเมล) จึงใช้การสแกน QR ด้วยบัญชี LINE แทน
- ระบบเปิดสตรีมข้อความสดของ OA Manager ไว้ OA ละ 1 สาย เหมือนเปิดแท็บ OA Manager ไว้
  ต้องออกเน็ตไปที่ `chat.line.biz`, `chat-streaming-api.line.biz`, `chat-content.line.biz`
  และ `access.line.me` ได้ ไม่ต้องเปิดพอร์ตหรือมี URL สาธารณะ
- cookie ของ OA Manager ถูกเข้ารหัสด้วย `LINEPASSPORT_SECRET_KEY` เหมือน session ของ LINE
- เมื่อ OA Manager ให้ออกจากระบบ บัญชีจะขึ้นว่าหลุดการเชื่อมต่อ กด "สแกน QR ใหม่"
  ที่ ตั้งค่า → จัดการบัญชี
- OA ต้องเปิดโหมดแชทไว้ (OA Manager → ตั้งค่า → การตอบกลับ → แชท) OA ที่ตั้งเป็นบอท
  OA Manager จะไม่ให้ดูแชท บัญชีนั้นจะขึ้นว่า "ปิดโหมดแชทใน OA Manager อยู่" และระบบจะลองใหม่
  ทุก 10 นาที ถ้า OA ใช้แชทบอทผ่าน webhook อยู่ เปิดทั้งแชทและ webhook พร้อมกันได้
- ข้อจำกัด: ใช้ระบบเดียวกับหน้าเว็บ OA Manager ซึ่ง LINE ไม่ได้เปิดให้ใช้อย่างเป็นทางการ
  ถ้า LINE เปลี่ยนระบบอาจใช้ไม่ได้ชั่วคราว, รองรับเฉพาะแชท 1:1 (ยังไม่รวมกลุ่ม),
  ส่งหากลุ่มลูกค้าและส่งตามเวลายังใช้กับ LINE OA ไม่ได้

## พร็อกซีสำหรับบัญชี LINE และ LINE OA

- **ตั้งที่ไหน:** ตอนเพิ่มบัญชี LINE (ขั้นที่ 1 → ติ๊ก "เชื่อมต่อผ่านพร็อกซี") หรือภายหลังที่
  ตั้งค่า → จัดการบัญชี → แก้ไข บัญชีนั้น รองรับ HTTP, HTTPS, SOCKS5 และ SOCKS5H
  ใช้เฉพาะบัญชีนั้น และรหัสผ่านของพร็อกซีถูกเก็บแบบเข้ารหัส
- **ควรเลือกแบบไหน:** Residential หรือ ISP ที่เป็น IP ประเทศไทย และใช้ IP เดิมตลอด
  (static หรือ sticky session) ถ้า IP เปลี่ยนบ่อยหรืออยู่ต่างประเทศ LINE อาจให้ออกจากระบบ
  หรือขอยืนยันตัวตนบ่อย ถ้ามีหลายบัญชี ให้แต่ละบัญชีใช้ IP ของตัวเอง
- **กรอกอย่างไร:** ผู้ให้บริการส่วนมากให้มาในรูป `host:port:username:password`
  ให้แยกกรอกในช่อง Host, Port, Username และ Password
- **หรือเลือกผู้ให้บริการ:** ในช่อง "แหล่งพร็อกซี" เลือก Bright Data, Oxylabs, Litport,
  Shifter หรือ Decodo แล้วกรอก username และ password ของพร็อกซีจากหน้า dashboard ของผู้ให้บริการ
  (Litport ใส่ Hub host และ pool ด้วย, Bright Data ใส่ IP ของ zone ได้ถ้าจอง IP ไทยไว้)
  ระบบใส่ host และ port ให้ ล็อกประเทศไทย และใช้ IP เดิมนานที่สุดที่ผู้ให้บริการอนุญาต
  (Oxylabs, Litport และ Decodo นานสุด 24 ชั่วโมง ส่วน Shifter ใช้ IP ที่วางไว้ในไทยในหน้าแพ็กเกจ)
  Oxylabs, Litport, Decodo และ Bright Data ที่ไม่ได้ใส่ IP ของ zone แต่ละบัญชีได้ session
  ของตัวเองจึงได้ IP คนละตัว ส่วน username ของ Shifter หรือ IP ของ zone ใน Bright Data คือ IP
  เดียวตายตัว ให้แต่ละบัญชีใช้คนละตัว สำหรับ Decodo, Oxylabs, Litport และ Bright Data ถ้าคัดลอก
  username ที่มีค่าอื่นติดมา (`-country-…`, `-session-…`, `_pool-…`) ระบบจะตัดเหลือแค่ชื่อบัญชี
  (ส่วน `-ip-…` ของ Bright Data จะใช้เป็น IP ของ zone ถ้าช่อง IP ว่างอยู่) ปุ่ม "ทดสอบพร็อกซี" บอกว่าพร็อกซีออกที่ IP
  ไหนและเป็น IP ไทยหรือไม่ (Oxylabs และ Bright Data ตรวจด้วยหน้าตรวจ IP ของผู้ให้บริการเอง)
  แล้วบัญชีจะใช้ IP ที่ทดสอบนั้นต่อ เลือก "กรอกเอง" เพื่อกรอก Host และ Port เองแบบเดิม
- **บังคับใช้พร็อกซีทั้งระบบ:** ในหน้า God (`/god`) → ตั้งค่าระบบ → บังคับใช้พร็อกซี เลือกได้แยกสำหรับ
  บัญชี LINE และบัญชี LINE OA เมื่อเปิด ช่อง "เชื่อมต่อผ่านพร็อกซี" จะถูกติ๊กไว้และเอาออกไม่ได้
  เพิ่มหรือเชื่อมต่อบัญชีใหม่โดยไม่มีพร็อกซีไม่ได้ บัญชีเดิมที่ยังไม่มีพร็อกซียังใช้งานต่อได้
  แต่จะขึ้นว่า "ต้องตั้งพร็อกซี" และต้องกรอกพร็อกซีก่อนเชื่อมต่อใหม่ หน้า God แสดงจำนวนบัญชีที่ยังไม่มีพร็อกซี
- **บัญชี LINE OA ตั้งพร็อกซีได้เหมือนกัน:** ตอนกด "+ เพิ่ม LINE OA" จะมีช่องพร็อกซีก่อนแสดง QR
  การล็อกอินและแชทของ OA ที่เพิ่มจากรอบนั้นจะผ่านพร็อกซีเดียวกัน เปลี่ยนหรือปิดภายหลังได้ที่ แก้ไข
  ของ OA นั้น ส่วน Telegram ไม่ใช้พร็อกซีนี้ (ใช้ `LINEPASSPORT_TELEGRAM_PROXY`)

## สำรองข้อมูล

สิ่งที่ควรสำรอง:

- PostgreSQL database
- State directory หรือ volume `/data` (รวม `crm.sqlite3` เมื่อไม่ได้ใช้ PostgreSQL)
- Reverse proxy และ environment variables

ตัวอย่างสำรอง PostgreSQL:

```bash
pg_dump -U linepassport -h 127.0.0.1 linepassport > linepassport-backup.sql
```

ห้าม commit ไฟล์ใน `.okline`, `/data`, LINE session, API key หรือ database password
ขึ้น Git

## แก้ปัญหาเบื้องต้น

### เปิดเว็บไม่ได้

ตรวจสอบว่า port ถูกใช้งานหรือไม่:

```powershell
Get-NetTCPConnection -LocalPort 8765
```

หรือเปลี่ยน port:

```powershell
python -m okline web --port 8766 --state-dir .okline
```

### PostgreSQL driver หาย

```bash
pip install "psycopg[binary]>=3.1"
```

### QR ไม่แสดง

```bash
pip install "qrcode>=7.0"
```

### LINE bridge หยุดทำงาน

ตรวจสอบ Node.js:

```text
node --version
```

ต้องเป็น Node.js 18 ขึ้นไป และต้องเรียก `node` ได้จาก `PATH`

### LINE token หลุด หรือขึ้นว่าต้องเชื่อมต่อใหม่

- ต้องเก็บ volume `/data` แบบ persistent และห้ามลบทิ้งตอน redeploy
- ต้องใช้ `LINEPASSPORT_SECRET_KEY` ค่าเดิมเสมอ หากเปลี่ยน key ระบบจะถอดรหัส
  LINE session เดิมไม่ได้
- ห้ามนำ session ของบัญชี LINE เดียวกันไปรันพร้อมกันทั้ง local และ production
- ระบบจะ refresh token และบันทึก token ใหม่ให้อัตโนมัติเมื่อ LINE อนุญาต
- หาก LINE ยกเลิก session แล้ว เช่น code `8` ระบบจะหยุด AI auto-reply และ
  Scheduler ของบัญชีนั้นเพื่อลด API call จากนั้นให้เข้า Account Management
  แล้วเชื่อมต่อบัญชีด้วย QR ใหม่
- ใช้ application replica เพียงหนึ่งตัวต่อ database ตามที่ระบุด้านบน

## Security Notes

- ใช้ LinePassport กับบัญชี LINE ของตนเองเท่านั้น
- LINE session และ token มีความสำคัญเทียบเท่ารหัสผ่าน
- ใช้ HTTPS เมื่อเปิดระบบผ่านโดเมน
- ใช้รหัสผ่าน PostgreSQL ที่คาดเดายาก
- จำกัดสิทธิ์และบัญชี LINE ที่สมาชิกแต่ละคนเข้าถึงได้
- ห้ามเปิด PostgreSQL สู่ Internet
- สำรอง PostgreSQL และ `/data` เป็นประจำ
