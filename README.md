# Science Shorts Cloud Factory (4 Channels × 6 Shorts/Day = 24/Day)

ระบบสร้างและอัปโหลด **YouTube Shorts ภาษาอังกฤษ (9:16)** อัตโนมัติ 24 ชั่วโมงบน Cloud 100% (ทำงานต่อเนื่องแม้ปิดเครื่องคอมพิวเตอร์):
1. **Physics** (`physics`) — วันละ 6 คลิป (00:00, 04:00, 08:00, 12:00, 16:00, 20:00)
2. **Chemistry** (`chemistry`) — วันละ 6 คลิป (00:15, 04:15, 08:15, 12:15, 16:15, 20:15)
3. **Biology** (`biology`) — วันละ 6 คลิป (00:30, 04:30, 08:30, 12:30, 16:30, 20:30)
4. **General Science** (`general_science`) — วันละ 6 คลิป (00:45, 04:45, 08:45, 12:45, 16:45, 20:45)

## จุดเด่นของระบบ
- **Hook แรง + จบครบในคลิปเดียว**: Gemini เขียนสคริปต์ภาษาอังกฤษ (75–105 คำ) เปิดด้วย Hook 2 วินาทีแรก และอธิบาย Main Idea จนจบสมบูรณ์
- **ตัดต่อวิดีโอจริงเร็ว 2–3 วินาที/คัต**: ดึงฟุตเทจวิดีโอจริง HD/9:16 จาก Pexels (+ Pixabay fallback) 12–15 คลิปต่อวิดีโอ ตัดสลับทุก 2–3 วินาทีด้วย FFmpeg พร้อมซับไตเติลคำต่อคำแบบ Shorts
- **ไม่ซ้ำหัวข้อตลอดกาล**: เก็บประวัติหัวข้อที่โพสต์แล้วใน SQLite (`/data/factory.db`) บน Cloud Volume
- **ทำความสะอาดไฟล์ชั่วคราวอัตโนมัติ**: ลบไฟล์วิดีโอหลังอัปโหลดเสร็จทันที ไม่เปลืองพื้นที่ Cloud

---

## ข้อสำคัญเรื่อง YouTube API Quota (วันละ 6 คลิป/ช่อง)
YouTube Data API v3 ให้โควตาฟรีเริ่มต้น **10,000 units/วัน/โปรเจกต์** และ `videos.insert` ใช้ **1,600 units/คลิป** (= สูงสุด ~6 คลิป/วัน/โปรเจกต์)
ดังนั้นเพื่อให้ทั้ง 4 ช่องอัปโหลดได้ **ช่องละ 6 คลิป/วัน** แนะนำให้สร้าง **Google Cloud Project แยกช่องละ 1 โปรเจกต์** (รวม 4 โปรเจกต์) หรือยื่นขอเพิ่ม Quota จาก Google

---

## วิธี Deploy ขึ้น Cloud (Railway / Render / Cloud VPS)

### 1. เตรียม API Keys
- `GEMINI_API_KEY` — จาก [Google AI Studio](https://aistudio.google.com/)
- `PEXELS_API_KEY` — จาก [Pexels API](https://www.pexels.com/api/) (ฟรี)
- `PIXABAY_API_KEY` — (ไม่บังคับ) จาก [Pixabay API](https://pixabay.com/api/docs/)

### 2. สร้าง YouTube Refresh Token สำหรับ 4 ช่อง
เปิด YouTube Data API v3 ใน Google Cloud Console แล้วดาวน์โหลด `client_secret.json` จากนั้นรันคำสั่งนี้ **ครั้งเดียว** เพื่อเอา Token ไปใส่ใน Cloud:
```bash
python scripts/get_youtube_token.py client_secret_physics.json
```
นำ `CLIENT_ID`, `CLIENT_SECRET`, `REFRESH_TOKEN` ไปใส่ใน Environment Variables ของแต่ละช่อง (`PHYSICS_YT_*`, `CHEMISTRY_YT_*`, `BIOLOGY_YT_*`, `GENERAL_SCIENCE_YT_*`)

### 3. Deploy บน Railway (แนะนำ) หรือ Render
1. Push โฟลเดอร์นี้ขึ้น GitHub (Private repo)
2. บน **Railway**:
   - New Project -> Deploy from GitHub repo (ระบบจะอ่าน `Dockerfile` และ `railway.json` อัตโนมัติ)
   - เพิ่ม **Volume** mount ที่ `/data` เพื่อเก็บ `factory.db` ถาวร
   - ใส่ Environment Variables ตามไฟล์ `.env.example`
3. บน **Render**:
   - New -> Blueprint -> เลือก repo (ระบบจะอ่าน `render.yaml` พร้อม Disk `/data` 1GB อัตโนมัติ)
4. บน **Cloud VPS (Ubuntu/Docker)**:
   ```bash
   docker build -t science-shorts-cloud .
   docker run -d --restart unless-stopped \
     --env-file .env \
     -v science_data:/data \
     -p 8080:8080 \
     science-shorts-cloud
   ```

### 4. ตรวจสอบสถานะการทำงาน
เปิดเบราว์เซอร์ไปที่ URL ของ Cloud service:
- `GET /health` — ตรวจว่าระบบออนไลน์
- `GET /status` — ดูจำนวนคลิปที่โพสต์วันนี้, เวลารันรอบถัดไปของทั้ง 4 ช่อง, และประวัติ 20 คลิปล่าสุด
- `POST /trigger/physics` — สั่งทดสอบรันช่อง `physics` ทันที 1 คลิป
