# FFmpeg Server — Hướng dẫn Setup

## Cấu trúc thư mục

```
process-ffmpeg/
├── main.py
├── requirements.txt
├── Dockerfile
├── README.md
└── assets/              ← upload file của bạn vào đây
    ├── logo.png         ← logo/watermark (PNG nền trong suốt, ~150x150px)
    ├── intro.mp4        ← video intro thương hiệu (2-5 giây, 1080x1920)
    ├── outro.mp4        ← video outro (2-5 giây, 1080x1920)
    └── font.ttf         ← font tiếng Việt (tự động copy từ system)
```

## Bước 1 — Chuẩn bị assets

### Logo (bắt buộc nếu muốn watermark)
- File PNG, nền trong suốt
- Kích thước: 150x150px hoặc nhỏ hơn
- Đặt tên: logo.png

### Intro/Outro (tùy chọn)
- File MP4, tỉ lệ 9:16 (1080x1920)
- Thời lượng: 2-5 giây
- QUAN TRỌNG: phải cùng codec và framerate với video chính
- Dùng FFmpeg để chuẩn hóa:
  ```
  ffmpeg -i your_intro.mp4 -vf scale=1080:1920 -c:v libx264 -r 30 assets/intro.mp4
  ffmpeg -i your_outro.mp4 -vf scale=1080:1920 -c:v libx264 -r 30 assets/outro.mp4
  ```

## Bước 2 — Push lên GitHub

```bash
git init
git add .
git commit -m "FFmpeg server"
git remote add origin https://github.com/YOUR_USERNAME/process-ffmpeg.git
git push -u origin main
```

## Bước 3 — Deploy Railway

1. Vào railway.app → project đã có → disconnect repo cũ
2. Connect lại repo GitHub mới
3. Railway tự build và deploy

## Bước 4 — Cấu hình Make

### Module HTTP gọi Railway:
```
URL:    https://your-app.railway.app/process
Method: POST
Headers:
  Content-Type: application/json

Body (JSON):
{
  "mp4_url":          "{{medias[1].url}}",
  "title":            "{{title}}",
  "caption":          "{{caption từ Groq}}",
  "callback_webhook": "https://hook.make.com/YOUR_WEBHOOK_ID"
}
```

### Scenario 2 (nhận kết quả):
- Trigger: Webhook
- Nhận: video_b64 (base64 encoded video đã xử lý)
- Decode base64 → upload YouTube/Facebook

## Test server

Mở trình duyệt:
```
https://your-app.railway.app/
```
Phải thấy: {"status": "ok", "message": "FFmpeg server running"}

## Lưu ý Railway Free Tier

- Free: $5 credit/tháng (~500 phút xử lý)
- Video 16 giây xử lý mất ~20-30 giây
- Timeout: 300 giây (đã cấu hình trong gunicorn)
- Nếu hết credit: nâng lên $5/tháng hoặc dùng Google Cloud Run
