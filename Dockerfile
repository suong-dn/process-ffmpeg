FROM python:3.11-slim

# Cài FFmpeg + font tiếng Việt
RUN apt-get update && apt-get install -y \
    ffmpeg \
    fonts-roboto \
    fonts-noto \
    wget \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy assets (logo, intro, outro, font)
# Tạo thư mục assets — bạn cần upload file vào đây
RUN mkdir -p /app/assets

# Copy font Roboto từ system
RUN cp /usr/share/fonts/truetype/roboto/Roboto-Bold.ttf /app/assets/font.ttf || \
    find /usr/share/fonts -name "*.ttf" | head -1 | xargs -I{} cp {} /app/assets/font.ttf

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8080

CMD ["gunicorn", "--bind", "0.0.0.0:8080", "--timeout", "300", "--workers", "1", "main:app"]
