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

- Python 3.9 ขึ้นไป (แนะนำ Python 3.12)
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

## สำรองข้อมูล

สิ่งที่ควรสำรอง:

- PostgreSQL database
- State directory หรือ volume `/data`
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

## Security Notes

- ใช้ LinePassport กับบัญชี LINE ของตนเองเท่านั้น
- LINE session และ token มีความสำคัญเทียบเท่ารหัสผ่าน
- ใช้ HTTPS เมื่อเปิดระบบผ่านโดเมน
- ใช้รหัสผ่าน PostgreSQL ที่คาดเดายาก
- จำกัดสิทธิ์และบัญชี LINE ที่สมาชิกแต่ละคนเข้าถึงได้
- ห้ามเปิด PostgreSQL สู่ Internet
- สำรอง PostgreSQL และ `/data` เป็นประจำ
