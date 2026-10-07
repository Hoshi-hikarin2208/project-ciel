"""Serve the Ciel browser interface on the local machine only."""

import json
import threading
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
import webbrowser

from ciel import CielEngine


HOST = "127.0.0.1"
PORT = 8765
PAGE = Path(__file__).with_name("ciel.html")
EVENTS = deque(maxlen=100)
EVENT_LOCK = threading.Lock()
EVENT_ID = 0
ENGINE_LOCK = threading.Lock()


def publish_event(event):
    global EVENT_ID
    with EVENT_LOCK:
        EVENT_ID += 1
        EVENTS.append({"id": EVENT_ID, **event})


class BrowserVoice:
    def say(self, text):
        publish_event({"type": "speech", "text": str(text)})


class CielWebHandler(BaseHTTPRequestHandler):
    server_version = "CielLocal/1.0"
    engine = None
    voice = BrowserVoice()

    def log_message(self, format_string, *args):
        print(f"[web] {self.address_string()} {format_string % args}")

    def send_json(self, status, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def send_page(self):
        try:
            body = PAGE.read_bytes()
        except OSError:
            self.send_error(500, "ciel.html could not be read")
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; connect-src 'self'; img-src 'self' data:; "
            "style-src 'self' 'unsafe-inline'; "
            "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
            "object-src 'none'; base-uri 'none'; frame-ancestors 'none'",
        )
        self.end_headers()
        self.wfile.write(body)

    def origin_is_local(self):
        origin = self.headers.get("Origin")
        if not origin:
            return True
        try:
            parsed = urlsplit(origin)
            return (
                parsed.scheme == "http"
                and parsed.hostname in {"127.0.0.1", "localhost"}
                and parsed.port == self.server.server_port
            )
        except ValueError:
            return False

    def do_GET(self):
        parsed = urlsplit(self.path)
        if parsed.path in {"/", "/ciel.html"}:
            self.send_page()
            return

        if parsed.path == "/api/status":
            with self.engine.reminders_lock:
                reminders = sorted(
                    self.engine.reminders,
                    key=lambda item: item.get("due", ""),
                )
            with EVENT_LOCK:
                event_cursor = EVENT_ID
            self.send_json(200, {
                "api_ready": bool(self.engine.api_key),
                "reminders": reminders[:8],
                "event_cursor": event_cursor,
            })
            return

        if parsed.path == "/api/events":
            query = parse_qs(parsed.query)
            try:
                after = int(query.get("after", ["0"])[0])
            except ValueError:
                self.send_json(400, {"error": "Invalid event cursor."})
                return
            with EVENT_LOCK:
                events = [event for event in EVENTS if event["id"] > after]
            self.send_json(200, {"events": events})
            return

        self.send_error(404)

    def do_POST(self):
        if not self.origin_is_local():
            self.send_json(403, {"error": "Requests must come from the local Ciel page."})
            return
        if self.path == "/api/configure-key" and not self.headers.get("Origin"):
            self.send_json(403, {"error": "Open this setting from the local Ciel page."})
            return

        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self.send_json(400, {"error": "Invalid request length."})
            return
        if length < 1 or length > 8192:
            self.send_json(413, {"error": "Request must contain at most 8 KB."})
            return

        try:
            payload = json.loads(self.rfile.read(length))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self.send_json(400, {"error": "Request body must be valid JSON."})
            return
        if not isinstance(payload, dict):
            self.send_json(400, {"error": "Request body must be a JSON object."})
            return

        if self.path == "/api/chat":
            command = payload.get("message", "")
            if not isinstance(command, str) or not command.strip():
                self.send_json(400, {"error": "Enter a message first."})
                return
            if len(command) > 4000:
                self.send_json(413, {"error": "Messages are limited to 4,000 characters."})
                return
            try:
                with ENGINE_LOCK:
                    reply = self.engine.handle(command.strip(), voice=self.voice)
                if reply == "__STOP__":
                    reply = "The browser session stays open. Close this tab when you are finished."
                self.send_json(200, {"reply": str(reply), "time": time.strftime("%I:%M %p").lstrip("0")})
            except Exception as exc:
                print(f"[web command error] {exc}")
                self.send_json(500, {"error": "Ciel could not process that request."})
            return

        if self.path == "/api/new-chat":
            with ENGINE_LOCK:
                self.engine.history.clear()
                self.engine.last_writing = {"request": "", "text": ""}
                self.engine.last_suggestion = {"request": "", "reply": ""}
            self.send_json(200, {"ok": True})
            return

        if self.path == "/api/configure-key":
            key = payload.get("api_key", "")
            if not isinstance(key, str) or not 20 <= len(key.strip()) <= 300:
                self.send_json(400, {"error": "Enter a valid Anthropic API key."})
                return
            try:
                self.engine.save_api_key(key.strip())
            except Exception as exc:
                print(f"[credential store error] {exc}")
                self.send_json(500, {
                    "error": "Windows could not save the key to its credential store. Install the requirements and try again."
                })
                return
            self.send_json(200, {"ok": True, "api_ready": True})
            return

        self.send_error(404)


def main():
    engine = CielEngine()
    CielWebHandler.engine = engine
    engine.reminder_callback = lambda text: publish_event({"type": "reminder", "text": text})
    server = ThreadingHTTPServer((HOST, PORT), CielWebHandler)
    server.daemon_threads = True
    url = f"http://{HOST}:{server.server_port}"
    print(f"Ciel web is ready at {url}")
    print("Only this computer can connect. Press Ctrl+C to stop.")
    webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping Ciel web.")
    finally:
        engine.stop_reminders()
        server.server_close()


if __name__ == "__main__":
    main()