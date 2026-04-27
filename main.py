from flask import Flask, request, jsonify
import subprocess, requests, os, uuid, json, base64, shutil, re
import gdown

app = Flask(__name__)

# ============================================================
# CẤU HÌNH
# ============================================================
LOGO_PATH  = "/app/assets/logo.png"
INTRO_PATH = "/app/assets/intro.mp4"
OUTRO_PATH = "/app/assets/outro.mp4"
FONT_PATH  = "/app/assets/font.ttf"
OUTPUT_DIR = "/tmp/outputs"
# ============================================================

os.makedirs(OUTPUT_DIR, exist_ok=True)


# ── DOWNLOAD ─────────────────────────────────────────────────

def extract_drive_id(url: str) -> str:
    m = re.search(r'/file/d/([a-zA-Z0-9_\-]+)', url)
    if m:
        return m.group(1)
    m = re.search(r'[?&]id=([a-zA-Z0-9_\-]+)', url)
    if m:
        return m.group(1)
    raise Exception(f"Không tìm được file ID trong URL: {url}")


def download_from_drive(file_id: str, dest: str):
    """Download từ Google Drive bằng gdown"""
    drive_url = f"https://drive.google.com/uc?id={file_id}&export=download"
    print(f"[Drive] ID: {file_id}")

    try:
        gdown.download(drive_url, dest, quiet=False, fuzzy=True)
    except Exception as e:
        raise Exception(f"gdown lỗi: {e}")

    if not os.path.exists(dest):
        raise Exception("gdown không tạo được file — kiểm tra share permission")

    # Kiểm tra không phải HTML
    with open(dest, "rb") as f:
        header = f.read(20)
    if header[:1] == b'<' or b'DOCTYPE' in header:
        os.remove(dest)
        raise Exception(
            "Drive trả về HTML thay vì video. "
            "Share file với 'Anyone with the link - Viewer'."
        )

    size_mb = os.path.getsize(dest) / 1024 / 1024
    if size_mb < 0.05:
        raise Exception(f"File quá nhỏ ({size_mb:.3f}MB)")

    print(f"[Drive] OK: {size_mb:.2f} MB")


def download_file(url: str, dest: str):
    if "drive.google.com" in url or "drive.usercontent.google.com" in url:
        file_id = extract_drive_id(url)
        download_from_drive(file_id, dest)
        return

    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    r = requests.get(url, stream=True, timeout=120, headers=headers)
    r.raise_for_status()
    with open(dest, "wb") as f:
        for chunk in r.iter_content(chunk_size=65536):
            if chunk:
                f.write(chunk)
    print(f"[HTTP] Downloaded: {os.path.getsize(dest)/1024/1024:.2f} MB")


# ── FFMPEG HELPERS ────────────────────────────────────────────

def get_duration(path: str) -> float:
    r = subprocess.run(
        ["ffprobe", "-v", "error",
         "-show_entries", "format=duration",
         "-of", "json", path],
        capture_output=True, text=True
    )
    if r.returncode != 0:
        raise Exception(f"ffprobe lỗi: {r.stderr}")
    data = json.loads(r.stdout)
    if "format" not in data or "duration" not in data.get("format", {}):
        raise Exception("Không đọc được duration — file không hợp lệ")
    return float(data["format"]["duration"])


def check_video_stream(path: str):
    r = subprocess.run([
        "ffprobe", "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=codec_name,width,height",
        "-of", "json", path
    ], capture_output=True, text=True)
    streams = json.loads(r.stdout).get("streams", [])
    if not streams:
        raise Exception(
            "File không có video stream — file bị hỏng hoặc Drive chưa share public."
        )
    print(f"[FFprobe] Video: {streams[0]}")
    return streams[0]


def create_srt(text: str, duration: float, path: str):
    words   = text.split()
    chunks  = [" ".join(words[i:i+6]) for i in range(0, len(words), 6)] or [text]
    seg     = duration / max(len(chunks), 1)
    content = ""
    for i, chunk in enumerate(chunks):
        s = i * seg
        e = min((i + 1) * seg, duration)
        content += f"{i+1}\n{_ts(s)} --> {_ts(e)}\n{chunk}\n\n"
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


def _ts(sec: float) -> str:
    h, r = divmod(int(sec), 3600)
    m, s = divmod(r, 60)
    return f"{h:02}:{m:02}:{s:02},{int((sec % 1) * 1000):03}"


def run_ffmpeg(cmd: list, step: str):
    print(f"[FFmpeg] {step}...")
    r = subprocess.run(cmd, capture_output=True)
    if r.returncode != 0:
        err = r.stderr.decode(errors="replace")[-800:]
        raise Exception(f"FFmpeg [{step}] lỗi:\n{err}")
    print(f"[FFmpeg] {step} OK")


# ── CORE ──────────────────────────────────────────────────────

def process_video(mp4_url: str, title: str, caption: str) -> str:
    uid   = str(uuid.uuid4())[:8]
    src   = f"/tmp/{uid}_src.mp4"
    s1    = f"/tmp/{uid}_resize.mp4"
    s2    = f"/tmp/{uid}_logo.mp4"
    s3    = f"/tmp/{uid}_sub.mp4"
    sub   = f"/tmp/{uid}.srt"
    final = f"{OUTPUT_DIR}/{uid}_final.mp4"

    try:
        # 1 ── Download
        print(f"\n[{uid}] === START ===")
        print(f"[{uid}] URL: {mp4_url[:100]}")
        download_file(mp4_url, src)

        # 2 ── Validate
        dur = get_duration(src)
        check_video_stream(src)
        print(f"[{uid}] {dur:.1f}s | {os.path.getsize(src)/1024/1024:.2f}MB")

        # 3 ── Resize 9:16
        run_ffmpeg([
            "ffmpeg", "-y", "-i", src,
            "-vf",
            "scale=1080:1920:force_original_aspect_ratio=decrease,"
            "pad=1080:1920:(ow-iw)/2:(oh-ih)/2:color=black",
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28",
            "-c:a", "aac", "-b:a", "96k",
            "-threads", "2",
            s1
        ], "resize_9_16")
        cur = s1

        # 4 ── Logo (chỉ chạy nếu file logo TỒN TẠI và đọc được)
        logo_ok = os.path.exists(LOGO_PATH) and os.path.getsize(LOGO_PATH) > 100
        if logo_ok:
            print(f"[{uid}] Logo found: {os.path.getsize(LOGO_PATH)} bytes")
            try:
                run_ffmpeg([
                    "ffmpeg", "-y",
                    "-i", cur,
                    "-i", LOGO_PATH,
                    "-filter_complex",
                    "[1:v]scale=150:-1,format=rgba[logo];"
                    "[0:v][logo]overlay=W-w-30:30:format=auto",
                    "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28",
                    "-c:a", "copy",
                    s2
                ], "add_logo")
                cur = s2
            except Exception as e:
                # Logo lỗi → bỏ qua, tiếp tục không có logo
                print(f"[{uid}] Logo SKIP (lỗi): {e}")
        else:
            print(f"[{uid}] No logo, skip")

        # 5 ── Phụ đề
        font_ok = os.path.exists(FONT_PATH) and os.path.getsize(FONT_PATH) > 100
        if caption and font_ok:
            create_srt(caption, dur, sub)
            try:
                run_ffmpeg([
                    "ffmpeg", "-y", "-i", cur,
                    "-vf",
                    f"subtitles={sub}:force_style='"
                    "FontSize=18,PrimaryColour=&HFFFFFF&,"
                    "OutlineColour=&H000000&,Outline=2,"
                    "Bold=1,Alignment=2,MarginV=80'",
                    "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28",
                    "-c:a", "copy",
                    s3
                ], "add_subtitle")
                cur = s3
            except Exception as e:
                print(f"[{uid}] Subtitle SKIP (lỗi): {e}")
        else:
            print(f"[{uid}] No caption/font, skip subtitle")

        # 6 ── Intro + Outro
        has_intro = os.path.exists(INTRO_PATH) and os.path.getsize(INTRO_PATH) > 1000
        has_outro = os.path.exists(OUTRO_PATH) and os.path.getsize(OUTRO_PATH) > 1000

        if has_intro or has_outro:
            clist = f"/tmp/{uid}_concat.txt"
            with open(clist, "w") as f:
                if has_intro: f.write(f"file '{INTRO_PATH}'\n")
                f.write(f"file '{cur}'\n")
                if has_outro: f.write(f"file '{OUTRO_PATH}'\n")
            try:
                run_ffmpeg([
                    "ffmpeg", "-y",
                    "-f", "concat", "-safe", "0", "-i", clist,
                    "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28",
                    "-c:a", "aac", "-b:a", "96k",
                    final
                ], "concat_intro_outro")
            except Exception as e:
                print(f"[{uid}] Concat SKIP: {e}")
                shutil.copy(cur, final)
            finally:
                if os.path.exists(clist):
                    os.remove(clist)
        else:
            shutil.copy(cur, final)
            print(f"[{uid}] No intro/outro, copy as final")

        final_mb = os.path.getsize(final) / 1024 / 1024
        print(f"[{uid}] === DONE: {final_mb:.2f}MB ===")
        return final

    finally:
        for f in [src, s1, s2, s3, sub]:
            if os.path.exists(f):
                os.remove(f)


# ── ENDPOINTS ─────────────────────────────────────────────────

@app.route("/", methods=["GET"])
def health():
    return jsonify({
        "status":  "ok",
        "message": "FFmpeg server running",
        "assets": {
            "logo":  os.path.exists(LOGO_PATH),
            "intro": os.path.exists(INTRO_PATH),
            "outro": os.path.exists(OUTRO_PATH),
            "font":  os.path.exists(FONT_PATH),
        }
    })


@app.route("/process", methods=["POST"])
def handle_process():
    data     = request.json or {}
    mp4_url  = data.get("mp4_url", "").strip()
    title    = data.get("title", "video").strip()
    caption  = data.get("caption", "").strip()
    hashtag  = data.get("hashtag", "").strip()
    callback = data.get("callback_webhook", "").strip()

    if not mp4_url:
        return jsonify({"error": "mp4_url là bắt buộc"}), 400

    print(f"\nNEW JOB: {title[:60]}")

    try:
        final_path = process_video(mp4_url, title, caption)
        size       = os.path.getsize(final_path)

        with open(final_path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode()
        os.remove(final_path)

        result = {
            "status":    "success",
            "title":     title,
            "caption":   caption,
            "hashtag":   hashtag,
            "video_b64": b64,
            "size_mb":   round(size / 1024 / 1024, 2)
        }

        if callback:
            try:
                cb_r = requests.post(callback, json=result, timeout=60)
                print(f"Callback → HTTP {cb_r.status_code}")
            except Exception as cb_e:
                print(f"Callback lỗi: {cb_e}")
            return jsonify({
                "status":  "ok",
                "size_mb": result["size_mb"],
                "message": "Xử lý xong, đã gọi callback"
            })

        return jsonify(result)

    except Exception as e:
        msg = str(e)
        print(f"ERROR: {msg}")
        return jsonify({"error": msg}), 500


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port, debug=False)