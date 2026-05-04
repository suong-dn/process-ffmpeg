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
    drive_url = f"https://drive.google.com/uc?id={file_id}&export=download"
    print(f"[Drive] ID: {file_id}")
    try:
        gdown.download(drive_url, dest, quiet=False, fuzzy=True)
    except Exception as e:
        raise Exception(f"gdown lỗi: {e}")

    if not os.path.exists(dest):
        raise Exception("gdown không tạo được file")

    with open(dest, "rb") as f:
        header = f.read(20)
    if header[:1] == b'<' or b'DOCTYPE' in header:
        os.remove(dest)
        raise Exception("Drive trả về HTML — share file với 'Anyone with the link'")

    size_mb = os.path.getsize(dest) / 1024 / 1024
    if size_mb < 0.05:
        raise Exception(f"File quá nhỏ ({size_mb:.3f}MB)")
    print(f"[Drive] OK: {size_mb:.2f} MB")


def download_file(url: str, dest: str):
    if "drive.google.com" in url or "drive.usercontent.google.com" in url:
        download_from_drive(extract_drive_id(url), dest)
        return
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    r = requests.get(url, stream=True, timeout=120, headers=headers)
    r.raise_for_status()
    with open(dest, "wb") as f:
        for chunk in r.iter_content(chunk_size=65536):
            if chunk:
                f.write(chunk)
    print(f"[HTTP] Downloaded: {os.path.getsize(dest)/1024/1024:.2f} MB")


# ── VIDEO INFO ────────────────────────────────────────────────

def get_video_info(path: str) -> dict:
    """Lấy thông tin video: duration, width, height, fps"""
    r = subprocess.run([
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-show_entries", "stream=codec_type,width,height,r_frame_rate",
        "-of", "json", path
    ], capture_output=True, text=True)

    if r.returncode != 0:
        raise Exception(f"ffprobe lỗi: {r.stderr}")

    data    = json.loads(r.stdout)
    fmt     = data.get("format", {})
    streams = data.get("streams", [])

    duration = float(fmt.get("duration", 0))
    width    = 0
    height   = 0

    for s in streams:
        if s.get("codec_type") == "video":
            width  = s.get("width", 0)
            height = s.get("height", 0)
            break

    if duration == 0:
        raise Exception("Không đọc được duration")
    if width == 0 or height == 0:
        raise Exception("Không có video stream hợp lệ")

    print(f"[Info] {width}x{height} | {duration:.1f}s")
    return {"duration": duration, "width": width, "height": height}


# ── TTS (Text-to-Speech tiếng Việt) ──────────────────────────

def generate_tts(text: str, dest: str, duration: float) -> bool:
    """
    Tạo file audio TTS tiếng Việt bằng gTTS (Google Text-to-Speech).
    Trả về True nếu thành công, False nếu thất bại.
    """
    try:
        from gtts import gTTS
        tts = gTTS(text=text, lang='vi', slow=False)
        tts_raw = dest.replace(".mp3", "_raw.mp3")
        tts.save(tts_raw)

        # Điều chỉnh tốc độ TTS cho vừa với thời lượng video
        # Lấy duration của TTS
        r = subprocess.run([
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "json", tts_raw
        ], capture_output=True, text=True)
        tts_data     = json.loads(r.stdout)
        tts_duration = float(tts_data["format"]["duration"])

        if tts_duration > 0:
            # Tính tốc độ cần thiết để TTS vừa khít video
            speed = tts_duration / max(duration * 0.85, 1)
            speed = max(0.5, min(speed, 2.0))  # giới hạn 0.5x - 2x

            subprocess.run([
                "ffmpeg", "-y", "-i", tts_raw,
                "-filter:a", f"atempo={speed:.2f}",
                "-c:a", "libmp3lame", "-b:a", "128k",
                dest
            ], capture_output=True, check=True)
        else:
            shutil.copy(tts_raw, dest)

        os.remove(tts_raw)
        print(f"[TTS] OK: {os.path.getsize(dest)/1024:.1f}KB")
        return True

    except ImportError:
        print("[TTS] gTTS không được cài — skip")
        return False
    except Exception as e:
        print(f"[TTS] Lỗi: {e} — skip")
        return False


# ── SUBTITLE ──────────────────────────────────────────────────

def create_srt(text: str, duration: float, path: str):
    """Tạo file SRT phụ đề từ caption"""
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
    print(f"[SRT] Created: {len(chunks)} segments")


def _ts(sec: float) -> str:
    h, r = divmod(int(sec), 3600)
    m, s = divmod(r, 60)
    return f"{h:02}:{m:02}:{s:02},{int((sec % 1) * 1000):03}"


# ── FFMPEG ────────────────────────────────────────────────────

def run_ffmpeg(cmd: list, step: str):
    print(f"[FFmpeg] {step}...")
    r = subprocess.run(cmd, capture_output=True)
    if r.returncode != 0:
        err = r.stderr.decode(errors="replace")[-800:]
        raise Exception(f"FFmpeg [{step}] lỗi:\n{err}")
    print(f"[FFmpeg] {step} OK")


# ── CORE PROCESSOR ────────────────────────────────────────────

def process_video(mp4_url: str, title: str, caption: str) -> str:
    uid   = str(uuid.uuid4())[:8]
    src   = f"/tmp/{uid}_src.mp4"
    s1    = f"/tmp/{uid}_logo.mp4"
    s2    = f"/tmp/{uid}_sub.mp4"
    s3    = f"/tmp/{uid}_audio.mp4"
    sub   = f"/tmp/{uid}.srt"
    tts   = f"/tmp/{uid}_tts.mp3"
    final = f"{OUTPUT_DIR}/{uid}_final.mp4"

    try:
        # 1 ── Download
        print(f"\n[{uid}] === START ===")
        download_file(mp4_url, src)

        # 2 ── Lấy thông tin video gốc
        info = get_video_info(src)
        dur  = info["duration"]
        w    = info["width"]
        h    = info["height"]
        cur  = src

        # 3 ── Copy giữ nguyên chất lượng gốc (không re-encode)
        #      Chỉ re-encode khi cần thêm logo/subtitle
        needs_reencode = False

        # 4 ── Thêm logo watermark (góc trên phải)
        logo_ok = os.path.exists(LOGO_PATH) and os.path.getsize(LOGO_PATH) > 100
        if logo_ok:
            print(f"[{uid}] Adding logo...")
            try:
                run_ffmpeg([
                    "ffmpeg", "-y",
                    "-i", cur,
                    "-i", LOGO_PATH,
                    "-filter_complex",
                    # Scale logo thành 8% chiều rộng video
                    f"[1:v]scale={max(w//12, 60)}:-1,format=rgba[logo];"
                    "[0:v][logo]overlay=W-w-20:20:format=auto",
                    # Copy stream gốc, chỉ re-encode video
                    "-c:v", "libx264", "-preset", "fast", "-crf", "18",
                    "-c:a", "copy",
                    s1
                ], "add_logo")
                cur = s1
                needs_reencode = True
            except Exception as e:
                print(f"[{uid}] Logo SKIP: {e}")
        else:
            print(f"[{uid}] No logo, skip")

        # 5 ── Thêm phụ đề tiếng Việt
        font_ok = os.path.exists(FONT_PATH) and os.path.getsize(FONT_PATH) > 100
        if caption and font_ok:
            create_srt(caption, dur, sub)
            # Tính cỡ chữ tự động theo độ phân giải
            font_size = max(int(h * 0.035), 16)
            try:
                run_ffmpeg([
                    "ffmpeg", "-y", "-i", cur,
                    "-vf",
                    f"subtitles={sub}:fontsdir=/app/assets:force_style='"
                    f"FontName=font,FontSize={font_size},"
                    "PrimaryColour=&H00FFFFFF&,"
                    "OutlineColour=&H00000000&,"
                    "BackColour=&H80000000&,"
                    "Outline=2,Shadow=1,"
                    "Bold=1,Alignment=2,"
                    f"MarginV={max(int(h*0.05), 30)}'",
                    "-c:v", "libx264", "-preset", "fast", "-crf", "18",
                    "-c:a", "copy",
                    s2
                ], "add_subtitle")
                cur = s2
            except Exception as e:
                print(f"[{uid}] Subtitle SKIP: {e}")
        else:
            print(f"[{uid}] No caption/font, skip subtitle")

        # 6 ── Thêm TTS voice tiếng Việt
        if caption:
            tts_ok = generate_tts(caption, tts, dur)
            if tts_ok and os.path.exists(tts):
                try:
                    run_ffmpeg([
                        "ffmpeg", "-y",
                        "-i", cur,       # video hiện tại
                        "-i", tts,       # TTS audio
                        "-filter_complex",
                        # Mix TTS với audio gốc: TTS 70%, gốc 30%
                        "[0:a]volume=0.3[orig];"
                        "[1:a]volume=0.7,apad=whole_dur="
                        f"{dur:.3f}[tts];"
                        "[orig][tts]amix=inputs=2:duration=first[aout]",
                        "-map", "0:v",   # video từ input 0
                        "-map", "[aout]", # audio mixed
                        "-c:v", "copy",  # giữ nguyên video
                        "-c:a", "aac", "-b:a", "128k",
                        "-shortest",
                        s3
                    ], "add_tts_voice")
                    cur = s3
                except Exception as e:
                    print(f"[{uid}] TTS mix SKIP: {e}")
        else:
            print(f"[{uid}] No caption for TTS, skip")

        # 7 ── Ghép Intro + Video + Outro
        has_intro = os.path.exists(INTRO_PATH) and os.path.getsize(INTRO_PATH) > 1000
        has_outro = os.path.exists(OUTRO_PATH) and os.path.getsize(OUTRO_PATH) > 1000

        if has_intro or has_outro:
            print(f"[{uid}] Concat intro/outro...")
            clist = f"/tmp/{uid}_concat.txt"
            with open(clist, "w") as f:
                if has_intro: f.write(f"file '{INTRO_PATH}'\n")
                f.write(f"file '{cur}'\n")
                if has_outro: f.write(f"file '{OUTRO_PATH}'\n")
            try:
                run_ffmpeg([
                    "ffmpeg", "-y",
                    "-f", "concat", "-safe", "0", "-i", clist,
                    # Re-encode để đồng nhất codec với intro/outro
                    "-c:v", "libx264", "-preset", "fast", "-crf", "18",
                    "-c:a", "aac", "-b:a", "128k",
                    final
                ], "concat_intro_outro")
            except Exception as e:
                print(f"[{uid}] Concat SKIP: {e}")
                shutil.copy(cur, final)
            finally:
                if os.path.exists(clist):
                    os.remove(clist)
        else:
            # Không có intro/outro → copy giữ nguyên
            if cur == src:
                # Chưa re-encode lần nào → copy nguyên file gốc
                shutil.copy(src, final)
            else:
                shutil.copy(cur, final)
            print(f"[{uid}] No intro/outro, copy as final")

        final_mb = os.path.getsize(final) / 1024 / 1024
        print(f"[{uid}] === DONE: {final_mb:.2f}MB ===")
        return final

    finally:
        for f in [src, s1, s2, s3, sub, tts]:
            if os.path.exists(f):
                os.remove(f)


# ── ENDPOINTS ─────────────────────────────────────────────────

@app.route("/", methods=["GET"])
def health():
    # Kiểm tra gTTS đã cài chưa
    try:
        import gtts
        tts_available = True
    except ImportError:
        tts_available = False

    return jsonify({
        "status":  "ok",
        "message": "FFmpeg server running",
        "assets": {
            "logo":  os.path.exists(LOGO_PATH),
            "intro": os.path.exists(INTRO_PATH),
            "outro": os.path.exists(OUTRO_PATH),
            "font":  os.path.exists(FONT_PATH),
        },
        "features": {
            "tts_voice":  tts_available,
            "subtitle":   os.path.exists(FONT_PATH),
            "logo":       os.path.exists(LOGO_PATH),
            "intro_outro": os.path.exists(INTRO_PATH) or os.path.exists(OUTRO_PATH),
        }
    })


@app.route("/process", methods=["POST"])
def handle_process():
    """
    INPUT:
      mp4_url  : Google Drive link hoặc URL MP4
      title    : Tiêu đề
      caption  : Caption tiếng Việt → làm phụ đề + TTS voice
      hashtag  : Hashtag

    OUTPUT:
      video_b64: Video đã xử lý (base64)
      size_mb  : Kích thước (MB)
    """
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
