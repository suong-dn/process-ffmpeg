from flask import Flask, request, jsonify
import subprocess, requests, os, uuid
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

app = Flask(__name__)

@app.route('/process', methods=['POST'])
def process_video():
    data = request.json
    mp4_url   = data['mp4_url']
    title     = data['title']
    make_webhook = data['callback_webhook']  # Make webhook nhận kết quả

    uid = str(uuid.uuid4())[:8]
    input_file  = f"/tmp/{uid}_input.mp4"
    output_file = f"/tmp/{uid}_output.mp4"

    # 1. Download video
    r = requests.get(mp4_url, stream=True)
    with open(input_file, 'wb') as f:
        for chunk in r.iter_content(chunk_size=8192):
            f.write(chunk)

    # 2. FFmpeg xử lý — thêm intro 2 giây, scale 1080x1920, watermark text
    subprocess.run([
        'ffmpeg', '-i', input_file,
        '-vf', 'scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2',
        '-c:v', 'libx264', '-preset', 'fast', '-crf', '23',
        '-c:a', 'aac', '-b:a', '128k',
        '-y', output_file
    ], check=True)

    # 3. Upload lên Google Drive
    creds = service_account.Credentials.from_service_account_file(
        'service_account.json',
        scopes=['https://www.googleapis.com/auth/drive']
    )
    drive = build('drive', 'v3', credentials=creds)
    media = MediaFileUpload(output_file, mimetype='video/mp4')
    file = drive.files().create(
        body={'name': f"{title}.mp4", 'parents': ['YOUR_FOLDER_ID']},
        media_body=media, fields='id,webViewLink'
    ).execute()

    # 4. Gọi webhook Make để tiếp tục workflow
    requests.post(make_webhook, json={
        'drive_file_id': file['id'],
        'drive_link':    file['webViewLink'],
        'title':         title
    })

    # Dọn file tạm
    os.remove(input_file)
    os.remove(output_file)
    return jsonify({'status': 'ok', 'file_id': file['id']})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8080)