from flask import Flask, request, jsonify
import subprocess, requests, os, uuid, json, base64, shutil, re
import gdown

app = Flask(__name__)

# ============================================================
# CẤU HÌNH
# ============================================================
LOGO_PATH   = "/app/assets/logo.png"
INTRO_PATH  = "/app/assets/intro.mp4"
OUTRO_PATH  = "/app/assets/outro.mp4"
FONT_PATH   = "/app/assets/font.ttf"
OUTPUT_DIR  = "/tmp/outputs"
# ============================================================

os.makedirs(OUTPUT_DIR, exist_ok=True)


def extract_drive_file_id(url: str) -> str:
    """Trích xuất file ID từ mọi dạng URL Google Drive"""
    # Dạng: /file/d/FILE_ID/view
    m = re.search(r'/file/d/([a-zA-Z0-9_\-]+)', url)
    if m:
        return m.group(1)
    # Dạng: ?id=FILE_ID hoặc &id=FILE_ID
    m = re.search(r'[?&]id=([a-zA-Z0-9_\-]+)', url)
    if m:
        return m.group(1)
    raise Exception(f"Không tìm được file ID trong URL: {url}")


def download_from_google_drive(file_id: str, dest: str):
    """Download bằng gdown — xử lý auth và warning tự động"""
    # ✅ Dùng đúng URL format cho gdown download
    url = f"https://drive.google.com/uc?id={file_id}"
    print(f"[Drive] Downloading via gdown, file ID: {file_id}")

    try:
        gdown.download(url, dest, quiet=False, fuzzy=True)
    except Exception as e:
        raise Exception(f"gdown thất bại: {e}")

    if not os.path.exists(dest):
        raise Exception(f"gdown không tạo được file. File ID: {file_id}")

    size_mb = os.path.getsize(dest) / 1024 / 1024
    print(f"[Drive] Downloaded: {size_mb:.2f} MB")

    # Kiểm tra file có phải video thật không (không phải HTML)
    with open(dest, "rb") as f:
        magic = f.read(16)
    print(f"[Drive] Magic bytes: {magic.hex()}")

    # HTML thường bắt đầu bằng '<' (0x3c)
    if magic[:1] == b'<' or magic[:5].lower() == b'<!doc':
        os.remove(dest)
        raise Exception(
            f"Google Drive trả về trang HTML thay vì video. "
            f"File chưa được share 'Anyone with the link'. "
            f"File ID: {file_id}"
        )

    if size_mb < 0.1:
        raise Exception(f"File quá nhỏ ({size_mb:.2f}MB) — download thất bại")


def download_file(url: str, dest: str):
    """Download file — tự nhận biết Google Drive hay URL thường"""
    if "drive.google.com" in url or "drive.usercontent.google.com" in url:
        file_id = extract_drive_file_id(url)
        print(f"[Drive] File ID: {file_id}")
        download_from_google_drive(file_id, dest)
        return

    # URL thường (không phải Drive)
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": "https://www.google.com",
    }
    r = requests.get(url, stream=True, timeout=120, headers=headers)
    r.raise_for_status()
    with open(dest, "wb") as f:
        for chunk in r.iter_content(chunk_size=65536):
            if chunk:
                f.write(chunk)


def create_subtitle_file(text: str, duration: float, path: str):
    words   = text.split()
    chunks  = [" ".join(words[i:i+6]) for i in range(0, len(words), 6)] or [text]
    seg_dur = duration / max(len(chunks), 1)
    content = ""
    for i, chunk in enumerate(chunks):
        s = i * seg_dur
        e = min((i + 1) * seg_dur, duration)
        content += f"{i+1}\n{ts(s)} --> {ts(e)}\n{chunk}\n\n"
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


def ts(sec: float) -> str:
    h, r = divmod(int(sec), 3600)
    m, s = divmod(r, 60)
    ms   = int((sec % 1) * 1000)
    return f"{h:02}:{m:02}:{s:02},{ms:03}"


def get_duration(path: str) -> float:
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", path],
        capture_output=True, text=True
    )
    return float(json.loads(r.stdout)["format"]["duration"])


def process_video(mp4_url: str, title: str, caption: str) -> str:
    uid   = str(uuid.uuid4())[:8]
    src   = f"/tmp/{uid}_input.mp4"
    s1    = f"/tmp/{uid}_s1.mp4"   # sau resize 9:16
    s2    = f"/tmp/{uid}_s2.mp4"   # sau logo
    s3    = f"/tmp/{uid}_s3.mp4"   # sau phụ đề
    sub   = f"/tmp/{uid}.srt"
    final = f"{OUTPUT_DIR}/{uid}_final.mp4"

    try:
        # 1 ── Download
        print(f"[{uid}] Downloading: {mp4_url[:80]}...")
        download_file(mp4_url, src)

        mb = os.path.getsize(src) / 1024 / 1024
        print(f"[{uid}] Downloaded: {mb:.2f} MB")

        dur = get_duration(src)
        print(f"[{uid}] Duration: {dur:.1f}s")

        # Kiểm tra video stream
        probe = subprocess.run([
            "ffprobe", "-v", "error", "-show_streams",
            "-select_streams", "v:0",
            "-show_entries", "stream=codec_name,width,height",
            "-of", "json", src
        ], capture_output=True, text=True)
        video_streams = json.loads(probe.stdout).get("streams", [])
        print(f"[{uid}] Video streams: {video_streams}")

        if not video_streams:
            raise Exception(
                "File không có video stream — chỉ có audio. "
                "Kiểm tra lại file trên Google Drive."
            )

        # 2 ── Resize 9:16
        print(f"[{uid}] Resize 9:16...")
        subprocess.run([
            "ffmpeg", "-y", "-i", src,
            "-vf", "scale=1080:1920:force_original_aspect_ratio=decrease,"
                   "pad=1080:1920:(ow-iw)/2:(oh-ih)/2:color=black",
            "-c:v", "libx264",
            "-preset", "ultrafast",   # ← đổi từ "fast" sang "ultrafast" (ít RAM hơn)
            "-crf", "28",             # ← tăng từ 23 lên 28 (file nhỏ hơn, ít RAM hơn)
            "-c:a", "aac", "-b:a", "96k",  # ← giảm audio bitrate
            "-threads", "1",          # ← giới hạn thread để tránh OOM
            s1
        ], check=True, capture_output=True)
        cur = s1

        # 3 ── Logo watermark
        if os.path.exists(LOGO_PATH):
            print(f"[{uid}] Adding logo...")
            subprocess.run([
                "ffmpeg", "-y", "-i", cur, "-i", LOGO_PATH,
                "-filter_complex", "[1:v]scale=150:-1[logo];[0:v][logo]overlay=W-w-30:30",
                "-c:v", "libx264", "-preset", "ultrafast", "-crf", "23",
                "-c:a", "copy", s2
            ], check=True, capture_output=True)
            cur = s2

        # 4 ── Phụ đề tiếng Việt
        if caption and os.path.exists(FONT_PATH):
            print(f"[{uid}] Adding subtitles...")
            create_subtitle_file(caption, dur, sub)
            subprocess.run([
                "ffmpeg", "-y", "-i", cur,
                "-vf", f"subtitles={sub}:force_style='"
                       "FontSize=18,PrimaryColour=&HFFFFFF&,"
                       "OutlineColour=&H000000&,Outline=2,Bold=1,"
                       "Alignment=2,MarginV=80'",
                "-c:v", "libx264", "-preset", "ultrafast", "-crf", "23",
                "-c:a", "copy", s3
            ], check=True, capture_output=True)
            cur = s3

        # 5 ── Ghép intro + video + outro
        has_intro = os.path.exists(INTRO_PATH)
        has_outro = os.path.exists(OUTRO_PATH)
        if has_intro or has_outro:
            print(f"[{uid}] Concat intro/outro...")
            clist = f"/tmp/{uid}_concat.txt"
            with open(clist, "w") as f:
                if has_intro: f.write(f"file '{INTRO_PATH}'\n")
                f.write(f"file '{cur}'\n")
                if has_outro: f.write(f"file '{OUTRO_PATH}'\n")
            subprocess.run([
                "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", clist,
                "-c:v", "libx264", "-preset", "ultrafast", "-crf", "23",
                "-c:a", "aac", "-b:a", "128k", final
            ], check=True, capture_output=True)
            os.remove(clist)
        else:
            shutil.copy(cur, final)

        print(f"[{uid}] Final: {os.path.getsize(final)/1024/1024:.1f}MB")
        return final

    finally:
        for f in [src, s1, s2, s3, sub]:
            if os.path.exists(f): os.remove(f)


# ── ENDPOINTS ──────────────────────────────────────────────

@app.route("/", methods=["GET"])
def health():
    return jsonify({"status": "ok", "message": "FFmpeg server running"})


@app.route("/process", methods=["POST"])
def handle_process():
    data     = request.json or {}
    mp4_url  = data.get("mp4_url", "")
    title    = data.get("title", "video")
    caption  = data.get("caption", "")
    hashtag  = data.get("hashtag", "")
    callback = data.get("callback_webhook", "")

    if not mp4_url:
        return jsonify({"error": "mp4_url is required"}), 400

    print(f"Job: {title[:60]}")
    try:
        out  = process_video(mp4_url, title, caption)
        size = os.path.getsize(out)

        with open(out, "rb") as f:
            b64 = base64.b64encode(f.read()).decode()
        os.remove(out)

        result = {
            "status":    "success",
            "title":     title,
            "caption":   caption,
            "hashtag":   hashtag,
            "video_b64": b64,
            "size_mb":   round(size / 1024 / 1024, 2)
        }

        if callback:
            r = requests.post(callback, json=result, timeout=60)
            print(f"Callback → {r.status_code}")
            return jsonify({"status": "ok", "size_mb": result["size_mb"]})

        return jsonify(result)

    except subprocess.CalledProcessError as e:
        err = (e.stderr or b"").decode()[-500:]
        print(f"FFmpeg error: {err}")
        return jsonify({"error": "FFmpeg failed", "detail": err}), 500
    except Exception as e:
        print(f"Error: {e}")
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
