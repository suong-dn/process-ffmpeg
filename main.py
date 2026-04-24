from flask import Flask, request, jsonify
import subprocess, requests, os, uuid, json, base64
from pathlib import Path

app = Flask(__name__)

# ============================================================
# CẤU HÌNH — chỉnh sửa theo nhu cầu của bạn
# ============================================================
LOGO_PATH      = "/app/assets/logo.png"      # logo/watermark của bạn
INTRO_PATH     = "/app/assets/intro.mp4"     # video intro (2-3 giây)
OUTRO_PATH     = "/app/assets/outro.mp4"     # video outro (2-3 giây)
FONT_PATH      = "/app/assets/font.ttf"      # font chữ tiếng Việt (Roboto/NotoSans)
OUTPUT_DIR     = "/tmp/outputs"
# ============================================================

os.makedirs(OUTPUT_DIR, exist_ok=True)


def download_file(url: str, dest: str):
    """Download file từ URL về local"""
    r = requests.get(url, stream=True, timeout=120)
    r.raise_for_status()
    with open(dest, "wb") as f:
        for chunk in r.iter_content(chunk_size=65536):
            f.write(chunk)


def create_subtitle_file(subtitle_text: str, duration: float, path: str):
    """Tạo file SRT phụ đề đơn giản"""
    # Chia caption thành các đoạn 3 giây
    words   = subtitle_text.split()
    chunks  = []
    chunk_size = 6  # 6 từ mỗi dòng
    for i in range(0, len(words), chunk_size):
        chunks.append(" ".join(words[i:i+chunk_size]))

    segment_duration = duration / max(len(chunks), 1)
    srt_content = ""
    for i, chunk in enumerate(chunks):
        start = i * segment_duration
        end   = min((i + 1) * segment_duration, duration)
        start_ts = format_timestamp(start)
        end_ts   = format_timestamp(end)
        srt_content += f"{i+1}\n{start_ts} --> {end_ts}\n{chunk}\n\n"

    with open(path, "w", encoding="utf-8") as f:
        f.write(srt_content)


def format_timestamp(seconds: float) -> str:
    h  = int(seconds // 3600)
    m  = int((seconds % 3600) // 60)
    s  = int(seconds % 60)
    ms = int((seconds % 1) * 1000)
    return f"{h:02}:{m:02}:{s:02},{ms:03}"


def get_video_duration(path: str) -> float:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries",
         "format=duration", "-of", "json", path],
        capture_output=True, text=True
    )
    data = json.loads(result.stdout)
    return float(data["format"]["duration"])


def process_video(mp4_url: str, title: str, caption: str) -> str:
    uid        = str(uuid.uuid4())[:8]
    input_file = f"/tmp/{uid}_input.mp4"
    sub_file   = f"/tmp/{uid}_subs.srt"
    step1      = f"/tmp/{uid}_step1.mp4"   # sau resize 9:16
    step2      = f"/tmp/{uid}_step2.mp4"   # sau thêm logo
    step3      = f"/tmp/{uid}_step3.mp4"   # sau thêm phụ đề
    final      = f"{OUTPUT_DIR}/{uid}_final.mp4"

    try:
        # ── 1. Download video gốc ──────────────────────────────
        print(f"[{uid}] Downloading video...")
        download_file(mp4_url, input_file)
        duration = get_video_duration(input_file)
        print(f"[{uid}] Duration: {duration:.1f}s")

        # ── 2. Resize về 9:16 (1080x1920) ─────────────────────
        print(f"[{uid}] Step 1: Resize to 9:16...")
        subprocess.run([
            "ffmpeg", "-y", "-i", input_file,
            "-vf",
            "scale=1080:1920:force_original_aspect_ratio=decrease,"
            "pad=1080:1920:(ow-iw)/2:(oh-ih)/2:color=black",
            "-c:v", "libx264", "-preset", "fast", "-crf", "23",
            "-c:a", "aac", "-b:a", "128k",
            step1
        ], check=True, capture_output=True)

        # ── 3. Thêm logo/watermark góc trên phải ──────────────
        current = step1
        if os.path.exists(LOGO_PATH):
            print(f"[{uid}] Step 2: Adding logo...")
            subprocess.run([
                "ffmpeg", "-y",
                "-i", current,
                "-i", LOGO_PATH,
                "-filter_complex",
                "[1:v]scale=150:-1[logo];"
                "[0:v][logo]overlay=W-w-30:30",
                "-c:v", "libx264", "-preset", "fast", "-crf", "23",
                "-c:a", "copy",
                step2
            ], check=True, capture_output=True)
            current = step2
        else:
            print(f"[{uid}] Logo not found, skipping...")

        # ── 4. Thêm phụ đề tiếng Việt ─────────────────────────
        if caption and os.path.exists(FONT_PATH):
            print(f"[{uid}] Step 3: Adding subtitles...")
            create_subtitle_file(caption, duration, sub_file)
            subtitle_filter = (
                f"subtitles={sub_file}:force_style='"
                f"FontName=Roboto,FontSize=18,PrimaryColour=&HFFFFFF&,"
                f"OutlineColour=&H000000&,Outline=2,Bold=1,"
                f"Alignment=2,MarginV=80'"
            )
            subprocess.run([
                "ffmpeg", "-y", "-i", current,
                "-vf", subtitle_filter,
                "-c:v", "libx264", "-preset", "fast", "-crf", "23",
                "-c:a", "copy",
                step3
            ], check=True, capture_output=True)
            current = step3
        else:
            print(f"[{uid}] Subtitle/font not found, skipping...")

        # ── 5. Ghép intro + video + outro ─────────────────────
        has_intro = os.path.exists(INTRO_PATH)
        has_outro = os.path.exists(OUTRO_PATH)

        if has_intro or has_outro:
            print(f"[{uid}] Step 4: Concat intro/outro...")
            concat_list = f"/tmp/{uid}_concat.txt"
            with open(concat_list, "w") as f:
                if has_intro:
                    f.write(f"file '{INTRO_PATH}'\n")
                f.write(f"file '{current}'\n")
                if has_outro:
                    f.write(f"file '{OUTRO_PATH}'\n")

            subprocess.run([
                "ffmpeg", "-y",
                "-f", "concat", "-safe", "0",
                "-i", concat_list,
                "-c:v", "libx264", "-preset", "fast", "-crf", "23",
                "-c:a", "aac", "-b:a", "128k",
                final
            ], check=True, capture_output=True)
            os.remove(concat_list)
        else:
            # Không có intro/outro, copy file hiện tại làm final
            print(f"[{uid}] No intro/outro, using current as final...")
            subprocess.run(["cp", current, final], check=True)

        print(f"[{uid}] Done! Output: {final}")
        return final

    finally:
        # Dọn file tạm
        for f in [input_file, sub_file, step1, step2, step3]:
            if os.path.exists(f):
                os.remove(f)


# ============================================================
# API ENDPOINTS
# ============================================================

@app.route("/", methods=["GET"])
def health():
    return jsonify({"status": "ok", "message": "FFmpeg server running"})


@app.route("/process", methods=["POST"])
def handle_process():
    data = request.json
    if not data:
        return jsonify({"error": "No JSON body"}), 400

    mp4_url          = data.get("mp4_url", "")
    title            = data.get("title", "video")
    caption          = data.get("caption", "")
    callback_webhook = data.get("callback_webhook", "")

    if not mp4_url:
        return jsonify({"error": "mp4_url is required"}), 400

    # Xử lý async — trả về 200 ngay, xử lý nền
    # (Railway free tier có thể timeout nếu xử lý lâu)
    try:
        output_path = process_video(mp4_url, title, caption)

        # Đọc file output thành base64 để gửi về Make
        with open(output_path, "rb") as f:
            video_b64 = base64.b64encode(f.read()).decode("utf-8")

        # Dọn output
        os.remove(output_path)

        result = {
            "status":     "success",
            "title":      title,
            "video_b64":  video_b64,   # Make nhận base64, decode thành file
            "video_size":  os.path.getsize(output_path) if os.path.exists(output_path) else 0
        }

        # Gọi callback webhook của Make nếu có
        if callback_webhook:
            requests.post(callback_webhook, json=result, timeout=30)
            return jsonify({"status": "ok", "message": "Processing done, callback sent"})

        return jsonify(result)

    except subprocess.CalledProcessError as e:
        error_msg = e.stderr.decode() if e.stderr else str(e)
        print(f"FFmpeg error: {error_msg}")
        return jsonify({"error": "FFmpeg processing failed", "detail": error_msg}), 500
    except Exception as e:
        print(f"Error: {e}")
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port, debug=False)