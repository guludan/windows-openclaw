"""本地 STT 服务（faster-whisper），兼容 jianchang512/stt API"""
import argparse
import json
import os
import tempfile
import time
import uuid
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import parse_qs

from faster_whisper import WhisperModel


MODEL_DIR = os.path.expanduser("~/.cache/whisper/base")
model = None


LOCAL_MODEL = r"D:\whisper-model\models--Systran--faster-whisper-base\snapshots"

def load_model():
    global model
    if model is None:
        print("Loading whisper model (base) from local ...")
        # 用本地路径，跳过在线验证
        if os.path.isdir(LOCAL_MODEL):
            model = WhisperModel(
                LOCAL_MODEL,
                device="cuda" if __import__("torch").cuda.is_available() else "cpu",
                compute_type="float16",
                local_files_only=True,
            )
        else:
            model = WhisperModel(
                "base",
                device="cuda" if __import__("torch").cuda.is_available() else "cpu",
                compute_type="float16",
            )
        print("Model loaded.")
    return model


def transcribe(audio_path, language="zh"):
    m = load_model()
    segments, info = m.transcribe(audio_path, language=language, beam_size=5)
    text = "".join(seg.text for seg in segments).strip()
    return text


class STTHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/" or self.path == "/health":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"status": "ok"}).encode())
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length) if content_length else b""

        #兼容 multipart/form-data 和直接音频body
        audio_data = None
        if b"Content-Disposition" in body:
            # multipart - extract file data
            parts = body.split(b"\r\n\r\n")
            for i, part in enumerate(parts):
                if b"Content-Disposition" in part and b"filename" in part:
                    # next part is the file content
                    if i + 1 < len(parts):
                        file_content = parts[i + 1].split(b"\r\n--")[0]
                        audio_data = file_content
                        break
        else:
            audio_data = body

        if not audio_data:
            self.send_response(400)
            self.end_headers()
            self.wfile.write(json.dumps({"error": "no audio data"}).encode())
            return

        # detect language from query param
        qs = parse_qs(self.path.split("?")[1]) if "?" in self.path else {}
        language = qs.get("language", ["zh"])[0]

        # save to temp file
        ext = ".wav"
        if audio_data[:4] == b"fLaC":
            ext = ".flac"
        elif audio_data[:4] == b"\x1a\x45\xdf\xa3":
            ext = ".m4a"

        tmp = tempfile.NamedTemporaryFile(suffix=ext, delete=False)
        try:
            tmp.write(audio_data)
            tmp.close()
            text = transcribe(tmp.name, language=language)
            result = {"code": 0, "text": text}
        except Exception as e:
            result = {"code": 1, "error": str(e)}
        finally:
            os.unlink(tmp.name)

        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.end_headers()
        self.wfile.write(json.dumps(result, ensure_ascii=False).encode("utf-8"))

    def log_message(self, format, *args):
        print(f"[STT] {args[0]}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=9977)
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args()

    # preload model
    load_model()

    server = HTTPServer((args.host, args.port), STTHandler)
    print(f"STT server running on http://{args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
        server.server_close()


if __name__ == "__main__":
    main()
