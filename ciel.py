"""
CIEL Desktop - optimized voice assistant with a modern Tkinter UI.

Install:
    python -m pip install -r requirements.txt

Optional:
    Set ANTHROPIC_API_KEY in your environment for writing, deeper analysis,
    and general reasoning.

Run:
    python ciel.py
    python ciel.py --text

Notes:
- Tkinter is included with normal Python installations on Windows.
- The GUI never blocks on network/API calls or speech recognition.
- Voice recognition runs in a worker thread.
- Ciel's command engine is separated from the GUI.
"""

import ast
import datetime as dt
import json
import operator
import os
import platform
import queue
import random
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path
from urllib.parse import quote_plus

try:
    import requests
except ImportError:
    requests = None

try:
    import numpy as np
except ImportError:
    np = None

try:
    import sounddevice as sd
except ImportError:
    sd = None

try:
    import speech_recognition as sr
except ImportError:
    sr = None

try:
    import pyttsx3
except ImportError:
    pyttsx3 = None

try:
    import psutil
except ImportError:
    psutil = None

import tkinter as tk
from tkinter import filedialog, messagebox, ttk


# ============================================================
# Configuration
# ============================================================

APP_NAME = "CIEL"
APP_VERSION = "3.0"
WAKE_WORDS = {"ciel", "seal", "ceil", "kiel", "sealed"}

HOME = Path.home()
NOTES_FILE = HOME / "ciel_notes.txt"
WRITINGS_DIR = HOME / "Ciel_Writings"
CONFIG_FILE = HOME / ".ciel_config.json"

API_KEY = os.environ.get("ANTHROPIC_API_KEY", "").strip()
MODEL = os.environ.get("CIEL_MODEL", "claude-sonnet-5-5")

HEADERS = {"User-Agent": "Ciel-Assistant/3.0"}

SYSTEM_PROMPT = (
    "You are Ciel, a calm, precise, slightly formal AI assistant inspired by "
    "Raphael, the Wisdom King. Address the user as 'Master'. Begin factual "
    "answers with 'Answer.' Keep spoken replies concise, normally 1-3 sentences. "
    "Plain text only. No markdown, lists, emojis, or meta commentary."
)

WRITER_PROMPT = (
    "You are Ciel, a gifted writer inspired by Raphael, the Wisdom King. "
    "Write exactly what is requested with vivid, original, polished language. "
    "Plain text only. No markdown, no preface, no closing remarks unless requested."
)

TROUBLE_PROMPT = (
    "You are Ciel, a precise technical diagnostician inspired by Raphael, "
    "the Wisdom King. Identify likely causes, rank them by probability, then "
    "give practical fixes. Be concise and speakable. Plain text only. "
    "Address the user as 'Master'."
)

SUGGEST_PROMPT = (
    "You are Ciel, a wise advisor inspired by Raphael, the Wisdom King. "
    "Give exactly three concise suggestions, introduced as First, Second and Third. "
    "Then give one sentence beginning with Recommendation. Plain text only."
)

NEED_KEY = (
    "Notification. Writing and deep analysis need my full faculties, Master. "
    "Set ANTHROPIC_API_KEY and restart me to unlock them."
)

SITES = {
    "youtube": "https://www.youtube.com",
    "google": "https://www.google.com",
    "gmail": "https://mail.google.com",
    "facebook": "https://www.facebook.com",
    "messenger": "https://www.messenger.com",
    "github": "https://github.com",
    "chatgpt": "https://chat.openai.com",
    "claude": "https://claude.ai",
    "drive": "https://drive.google.com",
    "maps": "https://maps.google.com",
    "netflix": "https://www.netflix.com",
    "spotify": "https://open.spotify.com",
    "canva": "https://www.canva.com",
}

APPS = {
    "Windows": {
        "notepad": "notepad",
        "calculator": "calc",
        "paint": "mspaint",
        "word": "winword",
        "excel": "excel",
        "powerpoint": "powerpnt",
        "file explorer": "explorer",
        "explorer": "explorer",
        "settings": "ms-settings:",
        "task manager": "taskmgr",
        "command prompt": "cmd",
        "chrome": "chrome",
        "edge": "msedge",
        "vscode": "code",
        "visual studio code": "code",
    },
    "Darwin": {
        "notepad": "TextEdit",
        "calculator": "Calculator",
        "word": "Microsoft Word",
        "excel": "Microsoft Excel",
        "powerpoint": "Microsoft PowerPoint",
        "finder": "Finder",
        "settings": "System Settings",
        "chrome": "Google Chrome",
        "safari": "Safari",
        "vscode": "Visual Studio Code",
        "terminal": "Terminal",
    },
    "Linux": {
        "calculator": "gnome-calculator",
        "files": "nautilus",
        "terminal": "gnome-terminal",
        "chrome": "google-chrome",
        "firefox": "firefox",
        "vscode": "code",
        "text editor": "gedit",
    },
}


# ============================================================
# Helpers
# ============================================================

def trim(text, limit=420):
    text = re.sub(r"\s+", " ", str(text)).strip()
    if len(text) <= limit:
        return text
    cut = text[:limit]
    ends = [cut.rfind(". "), cut.rfind("! "), cut.rfind("? ")]
    end = max(ends)
    return cut[: end + 1] if end > 80 else cut.rstrip() + "..."


def now_text():
    return dt.datetime.now().strftime("%I:%M %p").lstrip("0")


def open_path(path):
    try:
        path = str(path)
        system = platform.system()
        if system == "Windows":
            os.startfile(path)  # type: ignore[attr-defined]
        elif system == "Darwin":
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])
        return True
    except Exception:
        return False


def save_text_file(title, text):
    WRITINGS_DIR.mkdir(parents=True, exist_ok=True)
    slug = re.sub(r"[^a-z0-9]+", "_", title.lower()).strip("_")[:45] or "writing"
    path = WRITINGS_DIR / f"{slug}_{dt.datetime.now():%Y%m%d_%H%M%S}.txt"
    path.write_text(text, encoding="utf-8")
    return path


# ============================================================
# Safe calculator
# ============================================================

OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Pow: operator.pow,
    ast.USub: operator.neg,
    ast.Mod: operator.mod,
}


def safe_eval(node):
    if isinstance(node, ast.Expression):
        return safe_eval(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in OPS:
        left = safe_eval(node.left)
        right = safe_eval(node.right)
        if isinstance(node.op, ast.Pow) and abs(right) > 100:
            raise ValueError("power too large")
        return OPS[type(node.op)](left, right)
    if isinstance(node, ast.UnaryOp) and type(node.op) in OPS:
        return OPS[type(node.op)](safe_eval(node.operand))
    raise ValueError("unsupported expression")


def calculate(expr):
    expr = (
        expr.lower()
        .replace("multiplied by", "*")
        .replace("divided by", "/")
        .replace("to the power of", "**")
        .replace("plus", "+")
        .replace("minus", "-")
        .replace("times", "*")
        .replace("over", "/")
        .replace("^", "**")
        .replace("mod", "%")
        .replace(" x ", " * ")
    )
    expr = re.sub(r"[^0-9+\-*/%.() ]", "", expr)
    if not expr.strip():
        return None
    try:
        result = safe_eval(ast.parse(expr.strip(), mode="eval"))
        result = round(result, 8)
        return f"Answer. The result is {result:g}."
    except Exception:
        return None


# ============================================================
# Voice
# ============================================================

class VoiceEngine:
    def __init__(self, event_callback=None):
        self.enabled = pyttsx3 is not None
        self.callback = event_callback
        self.lock = threading.Lock()
        self.engine = None

    def _notify(self, state):
        if self.callback:
            self.callback(state)

    def say(self, text):
        print(f"Ciel: {text}")
        if not self.enabled or not text:
            return

        with self.lock:
            self._notify("speaking")
            try:
                if self.engine is None:
                    self.engine = pyttsx3.init()
                    self.engine.setProperty("rate", 175)
                    voices = self.engine.getProperty("voices") or []
                    for voice in voices:
                        name = getattr(voice, "name", "").lower()
                        if any(x in name for x in ("female", "zira", "samantha")):
                            self.engine.setProperty("voice", voice.id)
                            break
                self.engine.say(text)
                self.engine.runAndWait()
            except Exception as exc:
                print(f"[voice error: {exc}]")
            finally:
                self._notify("idle")

    def stop(self):
        with self.lock:
            try:
                if self.engine:
                    self.engine.stop()
            except Exception:
                pass


# ============================================================
# Ciel engine
# ============================================================

class CielEngine:
    def __init__(self):
        self.history = []
        self.last_writing = {"request": "", "text": ""}
        self.last_suggestion = {"request": "", "reply": ""}
        self.timer_threads = []

    @property
    def api_key(self):
        return os.environ.get("ANTHROPIC_API_KEY", "").strip()

    def claude(self, messages, system, max_tokens=700, timeout=45):
        key = self.api_key
        if not key:
            return None

        try:
            response = requests.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": MODEL,
                    "max_tokens": max_tokens,
                    "system": system,
                    "messages": messages,
                },
                timeout=timeout,
            )
            response.raise_for_status()
            data = response.json()
            blocks = data.get("content", [])
            return "".join(
                block.get("text", "")
                for block in blocks
                if isinstance(block, dict)
            ).strip() or None
        except Exception as exc:
            print(f"[API error: {exc}]")
            return None

    def ask_brain(self, question):
        if self.api_key:
            self.history.append({"role": "user", "content": question})
            self.history = self.history[-8:]
            reply = self.claude(self.history, SYSTEM_PROMPT, 400)
            if reply:
                self.history.append({"role": "assistant", "content": reply})
                return reply
            self.history.pop()

        result = self.web_answer(question)
        if result:
            return result

        webbrowser.open(
            "https://www.google.com/search?q=" + quote_plus(question)
        )
        return (
            "Notification. I found no direct answer, so I opened the search "
            "results for you, Master."
        )

    def web_answer(self, query):
        try:
            response = requests.get(
                "https://api.duckduckgo.com/",
                params={
                    "q": query,
                    "format": "json",
                    "no_html": 1,
                    "skip_disambig": 1,
                },
                headers=HEADERS,
                timeout=7,
            )
            data = response.json()
            if data.get("AbstractText"):
                return "Answer. " + trim(data["AbstractText"])
            if data.get("Answer"):
                return "Answer. " + trim(data["Answer"])
        except Exception:
            pass

        try:
            response = requests.get(
                "https://en.wikipedia.org/w/api.php",
                params={
                    "action": "query",
                    "list": "search",
                    "srsearch": query,
                    "format": "json",
                    "srlimit": 1,
                },
                headers=HEADERS,
                timeout=7,
            )
            hits = response.json().get("query", {}).get("search", [])
            if hits:
                title = hits[0]["title"]
                summary = requests.get(
                    "https://en.wikipedia.org/api/rest_v1/page/summary/"
                    + quote_plus(title),
                    headers=HEADERS,
                    timeout=7,
                ).json()
                if summary.get("extract"):
                    return "Answer. " + trim(summary["extract"])
        except Exception:
            pass

        return None

    def weather(self, city=""):
        try:
            city = quote_plus(city.strip())
            url = f"https://wttr.in/{city}?format=%l:+%C,+%t,+feels+like+%f,+humidity+%h"
            response = requests.get(url, headers=HEADERS, timeout=8)
            response.raise_for_status()
            return "Report. " + response.text.strip()
        except Exception:
            return (
                "Report. I could not reach the weather service. "
                "Please check the connection, Master."
            )

    def open_target(self, name):
        name = name.strip().lower()
        if name in SITES:
            webbrowser.open(SITES[name])
            return f"Understood. Opening {name}."

        system = platform.system()
        app = APPS.get(system, {}).get(name)

        try:
            if system == "Windows":
                os.startfile(app or name)  # type: ignore[attr-defined]
            elif system == "Darwin":
                subprocess.Popen(["open", "-a", app or name])
            else:
                subprocess.Popen([app or name])
            return f"Understood. Opening {name}."
        except Exception:
            webbrowser.open(
                "https://www.google.com/search?q=" + quote_plus(name)
            )
            return f"I could not find {name}, so I searched the web instead."

    def play_youtube(self, query):
        webbrowser.open(
            "https://www.youtube.com/results?search_query=" + quote_plus(query)
        )
        return f"Understood. Searching YouTube for {query}."

    def take_note(self, text):
        NOTES_FILE.parent.mkdir(parents=True, exist_ok=True)
        with NOTES_FILE.open("a", encoding="utf-8") as file:
            file.write(
                f"[{dt.datetime.now():%Y-%m-%d %H:%M}] {text}\n"
            )
        return "Understood. The note has been recorded."

    def read_notes(self):
        if not NOTES_FILE.exists():
            return "Report. There are no notes yet, Master."
        lines = NOTES_FILE.read_text(encoding="utf-8").splitlines()[-5:]
        return "Report. Your latest notes: " + " ... ".join(lines)

    def set_timer(self, seconds, voice):
        def ring():
            time.sleep(seconds)
            voice.say("Notification. Master, your timer has finished.")

        thread = threading.Thread(target=ring, daemon=True)
        thread.start()
        self.timer_threads.append(thread)

        mins, secs = divmod(int(seconds), 60)
        parts = []
        if mins:
            parts.append(f"{mins} minute{'s' if mins != 1 else ''}")
        if secs:
            parts.append(f"{secs} second{'s' if secs != 1 else ''}")
        return "Understood. Timer set for " + " ".join(parts) + "."

    def parse_duration(self, text):
        total = 0
        for number, unit in re.findall(
            r"(\d+)\s*(hour|hours|minute|minutes|min|second|seconds|sec)",
            text.lower(),
        ):
            n = int(number)
            if unit.startswith("hour"):
                total += n * 3600
            elif unit.startswith("min"):
                total += n * 60
            else:
                total += n
        return total

    def system_status(self):
        if not psutil:
            return "Report. Install psutil to read system status, Master."

        cpu = psutil.cpu_percent(interval=0.35)
        ram = psutil.virtual_memory().percent
        result = (
            f"Report. CPU usage is {cpu:.0f} percent and memory usage is "
            f"{ram:.0f} percent"
        )

        battery = (
            psutil.sensors_battery()
            if hasattr(psutil, "sensors_battery")
            else None
        )
        if battery:
            result += (
                f", battery is {battery.percent:.0f} percent"
                f"{' and charging' if battery.power_plugged else ''}"
            )
        return result + "."

    def internet_ok(self):
        try:
            socket.create_connection(("1.1.1.1", 443), timeout=3).close()
            return True
        except OSError:
            return False

    def diagnose_laptop(self):
        if not psutil:
            return "Report. Install psutil so I can inspect the system, Master."

        cpu = psutil.cpu_percent(interval=0.7)
        ram = psutil.virtual_memory().percent
        disk = shutil.disk_usage(HOME)
        disk_pct = disk.used / disk.total * 100
        free_gb = disk.free / 1e9
        uptime_days = (time.time() - psutil.boot_time()) / 86400
        battery = (
            psutil.sensors_battery()
            if hasattr(psutil, "sensors_battery")
            else None
        )
        online = self.internet_ok()

        problems = []
        if cpu > 85:
            problems.append(
                f"CPU usage is high at {cpu:.0f} percent. Close heavy applications."
            )
        if ram > 85:
            problems.append(
                f"memory usage is high at {ram:.0f} percent. Close unused apps and tabs."
            )
        if disk_pct > 90 or free_gb < 10:
            problems.append(
                f"the drive is nearly full with only {free_gb:.0f} gigabytes free."
            )
        if uptime_days > 7:
            problems.append(
                f"the laptop has been running for {uptime_days:.0f} days. Restart it."
            )
        if battery and not battery.power_plugged and battery.percent < 20:
            problems.append(
                f"battery is low at {battery.percent:.0f} percent."
            )
        if not online:
            problems.append("internet access is unavailable.")

        facts = (
            f"CPU {cpu:.0f}%, RAM {ram:.0f}%, disk {disk_pct:.0f}% used, "
            f"{free_gb:.0f} GB free, uptime {uptime_days:.1f} days, "
            f"internet {'OK' if online else 'DOWN'}"
        )

        if self.api_key:
            out = self.claude(
                [{
                    "role": "user",
                    "content": (
                        f"Laptop metrics: {facts}. Give the most important "
                        "findings and fixes in 3 short sentences."
                    ),
                }],
                TROUBLE_PROMPT,
                400,
            )
            if out:
                return out

        if not problems:
            return "Answer. No major problems detected, Master. All systems look healthy."
        return "Report. " + " ".join(problems)

    def check_network(self):
        try:
            socket.gethostbyname("google.com")
            dns_ok = True
        except OSError:
            dns_ok = False

        def tcp_ms(host):
            try:
                start = time.perf_counter()
                socket.create_connection((host, 443), timeout=4).close()
                return (time.perf_counter() - start) * 1000
            except OSError:
                return None

        latency = tcp_ms("1.1.1.1")
        if latency is None:
            return (
                "Report. There is no internet connection, Master. "
                "Check Wi-Fi and restart the router if necessary."
            )

        if not dns_ok:
            return (
                "Report. The connection works but DNS lookup fails. "
                "Try restarting the router or changing DNS."
            )

        speed = None
        try:
            start = time.perf_counter()
            response = requests.get(
                "https://speed.cloudflare.com/__down?bytes=1500000",
                timeout=15,
            )
            elapsed = max(time.perf_counter() - start, 0.001)
            speed = len(response.content) * 8 / elapsed / 1e6
        except Exception:
            pass

        suffix = (
            f" Download speed is about {speed:.0f} megabits per second."
            if speed else ""
        )

        if latency > 300 or (speed is not None and speed < 2):
            return (
                f"Report. Your connection is weak. Latency is {latency:.0f} "
                f"milliseconds.{suffix} Move closer to the router and reduce "
                "background downloads."
            )

        return (
            f"Answer. Your connection is healthy, Master. Latency is "
            f"{latency:.0f} milliseconds.{suffix}"
        )

    def clipboard_get(self):
        try:
            import tkinter
            root = tkinter.Tk()
            root.withdraw()
            text = root.clipboard_get()
            root.destroy()
            return text
        except Exception:
            return ""

    def clipboard_set(self, text):
        try:
            import tkinter
            root = tkinter.Tk()
            root.withdraw()
            root.clipboard_clear()
            root.clipboard_append(text)
            root.update()
            root.destroy()
            return True
        except Exception:
            return False

    def basic_text_check(self, text):
        issues = []

        for match in re.finditer(r"\b(\w+)\s+\1\b", text, re.I):
            issues.append(f"repeated word '{match.group(1)}'")

        if re.search(r" {2,}", text):
            issues.append("extra spaces")

        if re.search(r"(?<![\w'])i(?![\w'.])", text):
            issues.append("a lowercase 'i'")

        if text[:1].islower():
            issues.append("it starts with a lowercase letter")

        if text.rstrip()[-1:] not in ".!?\"')":
            issues.append("missing ending punctuation")

        if not issues:
            return "Report. My basic check found nothing obvious."
        return "Report. I found: " + "; ".join(issues[:6]) + "."

    def analyze_clipboard(self, command):
        text = self.clipboard_get().strip()
        if not text:
            return (
                "Report. The clipboard is empty. Copy the text, code, "
                "or error message first, Master."
            )

        if re.search(
            r"proofread|grammar|spell|correct|edit|fix my (writing|text)",
            command,
        ):
            if not self.api_key:
                return self.basic_text_check(text)

            fixed = self.claude(
                [{
                    "role": "user",
                    "content": (
                        "Proofread this text. Fix spelling, grammar and punctuation "
                        "while keeping the author's voice. Return only corrected text.\n\n"
                        + text
                    ),
                }],
                WRITER_PROMPT,
                1800,
            )
            if not fixed:
                return "Report. I could not reach my writing faculty, Master."

            path = save_text_file("proofread", fixed)
            copied = self.clipboard_set(fixed)
            return (
                "Understood. The corrected text is saved"
                + (" and placed on your clipboard." if copied else ".")
                + f" File: {path}"
            )

        if not self.api_key:
            return NEED_KEY

        prompt = (
            "Review this text, code, or error. Identify concrete problems and "
            "give practical fixes, ranked by importance.\n\n" + text
        )
        out = self.claude([{"role": "user", "content": prompt}], TROUBLE_PROMPT, 900)
        return out or "Report. I could not reach my analysis faculty, Master."

    def write_creative(self, request):
        if not self.api_key:
            return NEED_KEY

        text = self.claude(
            [{"role": "user", "content": request}],
            WRITER_PROMPT,
            1800,
        )
        if not text:
            return "Report. I could not reach my writing faculty, Master."

        self.last_writing = {"request": request, "text": text}
        path = save_text_file(request, text)
        return f"{text}\n\nSaved to: {path}"

    def revise_writing(self, instruction):
        if not self.last_writing["text"]:
            return "There is nothing to revise yet. Ask me to write something first, Master."
        if not self.api_key:
            return NEED_KEY

        messages = [
            {"role": "user", "content": self.last_writing["request"]},
            {"role": "assistant", "content": self.last_writing["text"]},
            {"role": "user", "content": instruction},
        ]

        text = self.claude(messages, WRITER_PROMPT, 1800)
        if not text:
            return "Report. I could not reach my writing faculty, Master."

        self.last_writing["text"] = text
        path = save_text_file(self.last_writing["request"], text)
        return f"{text}\n\nSaved to: {path}"

    def offline_suggestion(self, request):
        groups = {
            "food": [
                "rice with fried egg and vegetables",
                "chicken adobo",
                "pancit",
                "sinigang",
                "a tuna sandwich with fruit",
                "vegetable omelette",
                "tortang talong with rice",
            ],
            "watch": [
                "a documentary on something you have never studied",
                "a classic anime series",
                "a feel-good comedy",
                "a mystery thriller",
                "a short science playlist",
                "a fantasy anime with strong world-building",
            ],
            "music": [
                "a lo-fi focus playlist",
                "instrumental film scores",
                "an old favorite playlist",
                "acoustic covers",
                "calm piano music",
                "an upbeat pop mix",
            ],
            "study": [
                "a 25-minute focused session followed by a 5-minute break",
                "active recall",
                "teach the topic aloud",
                "make a one-page summary",
                "practice with past questions",
                "study the hardest topic first",
            ],
            "do": [
                "take a 10-minute walk",
                "tidy your desk for five minutes",
                "write three goals for tomorrow",
                "learn one new thing for 15 minutes",
                "stretch and drink water",
                "sketch, write, or build something small",
            ],
        }

        r = request.lower()
        if re.search(r"eat|cook|food|dinner|lunch|breakfast|snack|meal|hungry", r):
            key = "food"
        elif re.search(r"watch|movie|anime|show|series|film", r):
            key = "watch"
        elif re.search(r"listen|music|song|playlist", r):
            key = "music"
        elif re.search(r"study|review|exam|learn|memorize", r):
            key = "study"
        else:
            key = "do"

        first, second, third = random.sample(groups[key], 3)
        return (
            f"Suggestion. First, {first}. Second, {second}. Third, {third}. "
            "For more tailored ideas, set the API key, Master."
        )

    def suggest(self, request, more=False):
        if not self.api_key:
            return self.offline_suggestion(request)

        if more and self.last_suggestion["reply"]:
            messages = [
                {
                    "role": "user",
                    "content": self.last_suggestion["request"],
                },
                {
                    "role": "assistant",
                    "content": self.last_suggestion["reply"],
                },
                {
                    "role": "user",
                    "content": "Give three different suggestions.",
                },
            ]
            base = self.last_suggestion["request"]
        else:
            messages = [{
                "role": "user",
                "content": (
                    f"It is {dt.datetime.now():%A %I:%M %p}. {request}"
                ),
            }]
            base = request

        out = self.claude(messages, SUGGEST_PROMPT, 500)
        if not out:
            return self.offline_suggestion(base)

        self.last_suggestion = {"request": base, "reply": out}
        return out

    def handle(self, command, voice=None, dictation_callback=None):
        c = command.strip()
        low = c.lower()
        low = re.sub(
            r"^(please|can you|could you|would you)\s+",
            "",
            low,
        )

        if low in {
            "goodbye",
            "go to sleep",
            "shut down ciel",
            "exit ciel",
            "quit",
        }:
            return "__STOP__"

        if re.fullmatch(r"(hello|hi|hey)", low):
            return "Greetings, Master. Ciel is ready."

        if "who are you" in low or "your name" in low:
            return (
                "I am Ciel, your assistant. I analyze, search, "
                "and carry out tasks on your command, Master."
            )

        # Writing
        if re.match(
            r"(write|compose|make|create|draft|give|come up with)\b",
            low,
        ) and re.search(
            r"\b(poem|poetry|haiku|sonnet|limerick|story|essay|letter|"
            r"speech|song|lyrics|caption|email|paragraph|script|slogan|"
            r"message|toast|prayer)\b",
            low,
        ):
            return self.write_creative(c)

        if self.last_writing["text"] and re.match(
            r"(make it|make that|rewrite|revise|change it|shorten|"
            r"lengthen|continue|add (a|more)|translate (it|that)|"
            r"now make|improve (it|that))",
            low,
        ):
            return self.revise_writing(c)

        if self.last_writing["text"] and re.fullmatch(
            r"read (it|that|the (poem|story|essay|letter))( again| to me| aloud)?",
            low,
        ):
            return self.last_writing["text"]

        # Suggestions
        if self.last_suggestion["reply"] and re.fullmatch(
            r"(another|more|more ideas|more options|more suggestions|"
            r"something else|give me more|different ones|different options|"
            r"any other)",
            low,
        ):
            return self.suggest("", more=True)

        if re.search(
            r"\b(suggest|suggestions?|recommend|recommendations?|ideas? for|"
            r"any ideas|i'?m bored|what should i (do|eat|watch|play|listen|"
            r"study|cook|wear|buy|read|name|call|make))\b",
            low,
        ):
            return self.suggest(c)

        # Diagnostics
        if (
            re.search(
                r"(diagnose|scan|inspect|health ?check|check up).{0,25}"
                r"\b(laptop|computer|pc|system)\b",
                low,
            )
            or re.search(
                r"\b(laptop|computer|pc)\b.*\b(slow|lagging|freezing|"
                r"hot|overheating)\b",
                low,
            )
            or re.search(r"what'?s wrong with my (laptop|computer|pc)", low)
        ):
            return self.diagnose_laptop()

        if (
            re.search(
                r"(wi-?fi|internet|network|connection).{0,25}"
                r"(slow|not working|problem|down|check|test)",
                low,
            )
            or re.search(
                r"(check|test) (my )?(wi-?fi|internet|connection|network)",
                low,
            )
            or "speed test" in low
        ):
            return self.check_network()

        if "clipboard" in low or "what i copied" in low:
            return self.analyze_clipboard(low)

        if re.match(
            r"(analy[sz]e|identify|troubleshoot|diagnose|what'?s wrong "
            r"with|what is wrong with|help me fix|how do i fix|"
            r"i have an? (problem|issue)|solve)\b",
            low,
        ):
            if not self.api_key:
                webbrowser.open(
                    "https://www.google.com/search?q="
                    + quote_plus("how to fix " + c)
                )
                return (
                    "Notification. I opened a search for the fix, Master. "
                    "Set the API key for deeper diagnosis."
                )
            out = self.claude(
                [{"role": "user", "content": c}],
                TROUBLE_PROMPT,
                600,
            )
            return out or "Report. I could not reach my analysis faculty, Master."

        # Dictation
        if re.search(r"\b(start|begin)\s+dictat|\bdictation\b|\bdictate\b", low):
            if not dictation_callback:
                return "Dictation is unavailable in this mode, Master."
            return dictation_callback()

        # Time/date
        if "what time" in low or low == "time":
            return f"Answer. It is {now_text()}, Master."

        if (
            "what's the date" in low
            or "what is the date" in low
            or "today's date" in low
            or "what day" in low
        ):
            return dt.datetime.now().strftime("Answer. Today is %A, %B %d, %Y.")

        # Weather
        if "weather" in low or "temperature" in low:
            match = re.search(r"(?:in|at|for)\s+([a-z .]+)$", low)
            return self.weather(match.group(1) if match else "")

        # Open apps/sites
        if low.startswith(("open ", "launch ", "start ")):
            return self.open_target(low.split(" ", 1)[1])

        # YouTube
        match = re.match(r"(?:play|watch)\s+(.+?)(?:\s+on youtube)?$", low)
        if match:
            return self.play_youtube(match.group(1))

        # Timer
        if "timer" in low or "remind me in" in low:
            seconds = self.parse_duration(low)
            if seconds and voice:
                return self.set_timer(seconds, voice)
            return "Please state the timer duration, Master."

        # Notes
        match = re.match(
            r"(?:take a note|note down|remember|write down)\s*(?:that)?\s*(.+)",
            low,
        )
        if match:
            return self.take_note(match.group(1))

        if "read my notes" in low or "read notes" in low:
            return self.read_notes()

        # System
        if any(
            x in low
            for x in (
                "system status",
                "battery",
                "cpu",
                "memory usage",
                "how's my laptop",
            )
        ):
            return self.system_status()

        # Calculator
        match = re.match(
            r"(?:calculate|compute|what is|what's|how much is)\s+(.+)$",
            low,
        )
        if match:
            result = calculate(match.group(1))
            if result:
                return result

        # Search
        match = re.match(
            r"(?:search(?: for| the web for)?|google|look up)\s+(.+)",
            low,
        )
        if match:
            query = match.group(1)
            result = self.web_answer(query)
            if result:
                return result
            webbrowser.open(
                "https://www.google.com/search?q=" + quote_plus(query)
            )
            return "Notification. Search results are open, Master."

        # Screenshot
        if "screenshot" in low:
            try:
                import pyautogui
                path = HOME / f"ciel_{int(time.time())}.png"
                pyautogui.screenshot(str(path))
                return f"Understood. Screenshot saved to {path}."
            except Exception:
                return "Install pyautogui to enable screenshots, Master."

        return self.ask_brain(c)


# ============================================================
# GUI
# ============================================================

class CielApp:
    BG = "#080B14"
    PANEL = "#101625"
    PANEL_2 = "#141C2E"
    BORDER = "#24304A"
    TEXT = "#EAF0FF"
    MUTED = "#8994AA"
    ACCENT = "#7C8CFF"
    ACCENT_2 = "#4EE6C1"
    USER = "#1B2440"

    def __init__(self, root):
        self.root = root
        self.root.title(f"CIEL — Wisdom Assistant v{APP_VERSION}")
        self.root.geometry("1120x760")
        self.root.minsize(900, 620)
        self.root.configure(bg=self.BG)

        self.engine = CielEngine()
        self.events = queue.Queue()
        self.command_queue = queue.Queue()
        self.voice = VoiceEngine(self.on_voice_state)

        self.running = True
        self.listening = False
        self.wake_mode = True
        self.voice_thread = None
        self.animation_phase = 0

        self.setup_style()
        self.build_ui()
        self.refresh_clock()
        self.refresh_system_cards()
        self.animate_orb()
        self.process_events()

        self.root.protocol("WM_DELETE_WINDOW", self.close)

        if (
            "--text" not in sys.argv
            and sr is not None
            and sd is not None
            and np is not None
        ):
            self.start_voice_thread()
        else:
            self.set_status("TEXT MODE", "idle")

        self.add_message(
            "Ciel",
            "Greetings, Master. I am online and ready.",
            "ciel",
        )

    def setup_style(self):
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        style.configure(
            "Ciel.Horizontal.TProgressbar",
            troughcolor=self.PANEL_2,
            background=self.ACCENT_2,
            bordercolor=self.PANEL_2,
            lightcolor=self.ACCENT_2,
            darkcolor=self.ACCENT_2,
        )

    def build_ui(self):
        self.root.grid_columnconfigure(1, weight=1)
        self.root.grid_rowconfigure(0, weight=1)

        self.build_sidebar()
        self.build_main()
        self.build_bottom()

    def build_sidebar(self):
        sidebar = tk.Frame(
            self.root,
            bg=self.PANEL,
            width=250,
            highlightbackground=self.BORDER,
            highlightthickness=1,
        )
        sidebar.grid(row=0, column=0, rowspan=2, sticky="nsew")
        sidebar.grid_propagate(False)

        logo = tk.Frame(sidebar, bg=self.PANEL)
        logo.pack(fill="x", padx=22, pady=(24, 12))

        tk.Label(
            logo,
            text="C I E L",
            font=("Segoe UI", 24, "bold"),
            fg=self.TEXT,
            bg=self.PANEL,
        ).pack(anchor="w")

        tk.Label(
            logo,
            text="WISDOM ASSISTANT",
            font=("Segoe UI", 8, "bold"),
            fg=self.ACCENT,
            bg=self.PANEL,
        ).pack(anchor="w")

        self.side_status = tk.Label(
            sidebar,
            text="●  SYSTEM ONLINE",
            font=("Segoe UI", 9, "bold"),
            fg=self.ACCENT_2,
            bg=self.PANEL,
        )
        self.side_status.pack(anchor="w", padx=23, pady=(8, 24))

        self.quick_button(sidebar, "◈  Weather", "What's the weather today?")
        self.quick_button(sidebar, "⌁  Diagnose PC", "Diagnose my laptop")
        self.quick_button(sidebar, "⌁  Check Wi-Fi", "Check my Wi-Fi")
        self.quick_button(sidebar, "✎  Write", "Write a short poem about tonight")
        self.quick_button(sidebar, "▣  Notes", "Read my notes")
        self.quick_button(sidebar, "⌕  Search", "Search for ")

        tk.Label(
            sidebar,
            text="SYSTEM",
            font=("Segoe UI", 8, "bold"),
            fg=self.MUTED,
            bg=self.PANEL,
        ).pack(anchor="w", padx=23, pady=(26, 8))

        self.side_api = tk.Label(
            sidebar,
            text="●  SMART BRAIN: "
            + ("AVAILABLE" if self.engine.api_key else "OFFLINE"),
            font=("Segoe UI", 8),
            fg=self.ACCENT_2 if self.engine.api_key else self.MUTED,
            bg=self.PANEL,
        )
        self.side_api.pack(anchor="w", padx=23, pady=4)

        tk.Button(
            sidebar,
            text="Open Ciel_Writings",
            command=lambda: open_path(WRITINGS_DIR),
            font=("Segoe UI", 9),
            fg=self.TEXT,
            bg=self.PANEL_2,
            activebackground=self.BORDER,
            activeforeground=self.TEXT,
            relief="flat",
            bd=0,
            cursor="hand2",
            padx=12,
            pady=9,
        ).pack(fill="x", padx=18, pady=(18, 6))

        tk.Button(
            sidebar,
            text="Open Notes",
            command=lambda: open_path(NOTES_FILE),
            font=("Segoe UI", 9),
            fg=self.TEXT,
            bg=self.PANEL_2,
            activebackground=self.BORDER,
            activeforeground=self.TEXT,
            relief="flat",
            bd=0,
            cursor="hand2",
            padx=12,
            pady=9,
        ).pack(fill="x", padx=18)

        tk.Label(
            sidebar,
            text=f"v{APP_VERSION}  •  {platform.system()}",
            font=("Segoe UI", 8),
            fg=self.MUTED,
            bg=self.PANEL,
        ).pack(side="bottom", anchor="w", padx=23, pady=18)

    def quick_button(self, parent, text, command):
        button = tk.Button(
            parent,
            text=text,
            command=lambda c=command: self.submit_command(c),
            font=("Segoe UI", 10),
            anchor="w",
            fg=self.TEXT,
            bg=self.PANEL,
            activebackground=self.PANEL_2,
            activeforeground=self.TEXT,
            relief="flat",
            bd=0,
            cursor="hand2",
            padx=14,
            pady=9,
        )
        button.pack(fill="x", padx=12, pady=1)

    def build_main(self):
        main = tk.Frame(self.root, bg=self.BG)
        main.grid(row=0, column=1, sticky="nsew")
        main.grid_columnconfigure(0, weight=1)
        main.grid_rowconfigure(2, weight=1)

        header = tk.Frame(main, bg=self.BG)
        header.grid(row=0, column=0, sticky="ew", padx=28, pady=(22, 10))

        left = tk.Frame(header, bg=self.BG)
        left.pack(side="left")

        self.status_title = tk.Label(
            left,
            text="READY, MASTER",
            font=("Segoe UI", 17, "bold"),
            fg=self.TEXT,
            bg=self.BG,
        )
        self.status_title.pack(anchor="w")

        self.status_detail = tk.Label(
            left,
            text="Say “Ciel” to wake me.",
            font=("Segoe UI", 9),
            fg=self.MUTED,
            bg=self.BG,
        )
        self.status_detail.pack(anchor="w", pady=(2, 0))

        self.clock_label = tk.Label(
            header,
            text="",
            font=("Segoe UI", 15, "bold"),
            fg=self.TEXT,
            bg=self.BG,
        )
        self.clock_label.pack(side="right", anchor="n")

        orb_panel = tk.Frame(
            main,
            bg=self.PANEL,
            highlightbackground=self.BORDER,
            highlightthickness=1,
        )
        orb_panel.grid(row=1, column=0, sticky="ew", padx=28, pady=8)

        self.orb_canvas = tk.Canvas(
            orb_panel,
            height=155,
            bg=self.PANEL,
            highlightthickness=0,
        )
        self.orb_canvas.pack(fill="both", expand=True)

        self.orb_canvas.bind("<Configure>", self.draw_orb)

        chat_panel = tk.Frame(
            main,
            bg=self.PANEL,
            highlightbackground=self.BORDER,
            highlightthickness=1,
        )
        chat_panel.grid(row=2, column=0, sticky="nsew", padx=28, pady=8)
        chat_panel.grid_columnconfigure(0, weight=1)
        chat_panel.grid_rowconfigure(0, weight=1)

        self.chat = tk.Text(
            chat_panel,
            wrap="word",
            bg=self.PANEL,
            fg=self.TEXT,
            insertbackground=self.TEXT,
            selectbackground=self.ACCENT,
            relief="flat",
            bd=0,
            font=("Segoe UI", 10),
            padx=18,
            pady=15,
            state="disabled",
        )
        self.chat.grid(row=0, column=0, sticky="nsew")

        scroll = ttk.Scrollbar(
            chat_panel,
            orient="vertical",
            command=self.chat.yview,
        )
        scroll.grid(row=0, column=1, sticky="ns")
        self.chat.configure(yscrollcommand=scroll.set)

        self.chat.tag_configure(
            "ciel_name",
            foreground=self.ACCENT_2,
            font=("Segoe UI", 9, "bold"),
            spacing3=3,
        )
        self.chat.tag_configure(
            "user_name",
            foreground=self.ACCENT,
            font=("Segoe UI", 9, "bold"),
            spacing3=3,
        )
        self.chat.tag_configure(
            "body",
            foreground=self.TEXT,
            font=("Segoe UI", 10),
            spacing3=10,
        )
        self.chat.tag_configure(
            "time",
            foreground=self.MUTED,
            font=("Segoe UI", 8),
        )

    def build_bottom(self):
        bottom = tk.Frame(self.root, bg=self.BG)
        bottom.grid(row=1, column=1, sticky="ew", padx=28, pady=(0, 18))
        bottom.grid_columnconfigure(0, weight=1)

        entry_frame = tk.Frame(
            bottom,
            bg=self.PANEL_2,
            highlightbackground=self.BORDER,
            highlightthickness=1,
        )
        entry_frame.grid(row=0, column=0, sticky="ew")
        entry_frame.grid_columnconfigure(0, weight=1)

        self.entry = tk.Entry(
            entry_frame,
            bg=self.PANEL_2,
            fg=self.TEXT,
            insertbackground=self.TEXT,
            relief="flat",
            bd=0,
            font=("Segoe UI", 11),
        )
        self.entry.grid(row=0, column=0, sticky="ew", padx=(15, 8), pady=12)
        self.entry.bind("<Return>", lambda _: self.submit_typed())
        self.entry.insert(0, "Type a command to Ciel...")

        self.entry.bind("<FocusIn>", self.clear_placeholder)
        self.entry.bind("<FocusOut>", self.restore_placeholder)

        self.mic_button = tk.Button(
            entry_frame,
            text="●",
            command=self.toggle_listening,
            font=("Segoe UI", 16, "bold"),
            fg=self.TEXT,
            bg=self.ACCENT,
            activebackground=self.ACCENT,
            activeforeground=self.TEXT,
            relief="flat",
            bd=0,
            cursor="hand2",
            width=3,
        )
        self.mic_button.grid(row=0, column=1, padx=4, pady=5)

        tk.Button(
            entry_frame,
            text="➤",
            command=self.submit_typed,
            font=("Segoe UI", 13, "bold"),
            fg=self.BG,
            bg=self.ACCENT_2,
            activebackground=self.ACCENT_2,
            activeforeground=self.BG,
            relief="flat",
            bd=0,
            cursor="hand2",
            width=3,
        ).grid(row=0, column=2, padx=(2, 6), pady=5)

        cards = tk.Frame(bottom, bg=self.BG)
        cards.grid(row=1, column=0, sticky="ew", pady=(8, 0))
        for i in range(4):
            cards.grid_columnconfigure(i, weight=1)

        self.card_labels = {}
        for i, name in enumerate(("CPU", "RAM", "BATTERY", "NETWORK")):
            card = tk.Frame(
                cards,
                bg=self.PANEL,
                highlightbackground=self.BORDER,
                highlightthickness=1,
            )
            card.grid(row=0, column=i, sticky="ew", padx=(0 if i == 0 else 5, 5))

            tk.Label(
                card,
                text=name,
                font=("Segoe UI", 7, "bold"),
                fg=self.MUTED,
                bg=self.PANEL,
            ).pack(anchor="w", padx=10, pady=(7, 0))

            value = tk.Label(
                card,
                text="—",
                font=("Segoe UI", 10, "bold"),
                fg=self.TEXT,
                bg=self.PANEL,
            )
            value.pack(anchor="w", padx=10, pady=(1, 7))
            self.card_labels[name] = value

    # --------------------------------------------------------
    # UI state
    # --------------------------------------------------------

    def clear_placeholder(self, _=None):
        if self.entry.get() == "Type a command to Ciel...":
            self.entry.delete(0, "end")

    def restore_placeholder(self, _=None):
        if not self.entry.get().strip():
            self.entry.insert(0, "Type a command to Ciel...")

    def set_status(self, status, state="idle"):
        titles = {
            "idle": "READY, MASTER",
            "listening": "LISTENING",
            "thinking": "THINKING",
            "speaking": "SPEAKING",
            "error": "SYSTEM NOTICE",
            "text": "TEXT MODE",
        }
        details = {
            "idle": "Say “Ciel” to wake me.",
            "listening": "I am listening for your command.",
            "thinking": "Processing your request...",
            "speaking": "Delivering response.",
            "error": "Something needs attention.",
            "text": "Type a command below.",
        }

        self.status_title.config(text=titles.get(state, status))
        self.status_detail.config(text=details.get(state, status))

        if state == "listening":
            self.status_title.config(fg=self.ACCENT_2)
            self.mic_button.config(bg=self.ACCENT_2)
        elif state == "thinking":
            self.status_title.config(fg=self.ACCENT)
            self.mic_button.config(bg=self.ACCENT)
        elif state == "speaking":
            self.status_title.config(fg=self.ACCENT)
            self.mic_button.config(bg=self.ACCENT)
        else:
            self.status_title.config(fg=self.TEXT)
            self.mic_button.config(bg=self.ACCENT)

    def on_voice_state(self, state):
        self.events.put(("state", state))

    def add_message(self, speaker, text, kind="ciel"):
        self.chat.config(state="normal")

        tag = "ciel_name" if kind == "ciel" else "user_name"
        self.chat.insert("end", f"{speaker}  ", tag)
        self.chat.insert("end", f"{now_text()}\n", "time")
        self.chat.insert("end", f"{text}\n\n", "body")

        self.chat.config(state="disabled")
        self.chat.see("end")

    # --------------------------------------------------------
    # Command execution
    # --------------------------------------------------------

    def submit_typed(self):
        text = self.entry.get().strip()
        if not text or text == "Type a command to Ciel...":
            return

        self.entry.delete(0, "end")
        self.add_message("You", text, "user")
        self.run_command(text)

    def submit_command(self, command):
        self.add_message("You", command, "user")
        self.run_command(command)

    def run_command(self, command):
        self.set_status("thinking", "thinking")

        def worker():
            try:
                result = self.engine.handle(
                    command,
                    voice=self.voice,
                )
                self.events.put(("response", result))
            except Exception as exc:
                print(f"[command error] {exc}")
                self.events.put((
                    "response",
                    "Report. An internal error occurred while processing that command."
                ))

        threading.Thread(target=worker, daemon=True).start()

    def process_events(self):
        try:
            while True:
                kind, value = self.events.get_nowait()

                if kind == "state":
                    if value == "speaking":
                        self.set_status("speaking", "speaking")
                    elif value == "idle" and not self.listening:
                        self.set_status("ready", "idle")

                elif kind == "response":
                    if value == "__STOP__":
                        self.add_message(
                            "Ciel",
                            "Understood. Entering standby. Goodbye, Master.",
                            "ciel",
                        )
                        self.root.after(700, self.close)
                    else:
                        self.add_message("Ciel", value, "ciel")
                        self.set_status("ready", "idle")

                        if self.voice.enabled:
                            spoken = trim(value.replace("\n", " "), 700)
                            threading.Thread(
                                target=self.voice.say,
                                args=(spoken,),
                                daemon=True,
                            ).start()
        except queue.Empty:
            pass

        if self.running:
            self.root.after(80, self.process_events)

    # --------------------------------------------------------
    # Voice recognition
    # --------------------------------------------------------

    def start_voice_thread(self):
        if self.voice_thread and self.voice_thread.is_alive():
            return

        self.voice_thread = threading.Thread(
            target=self.voice_loop,
            daemon=True,
        )
        self.voice_thread.start()

    def voice_loop(self):
        """Voice recognition without PyAudio. Uses sounddevice for recording."""
        if sr is None or sd is None or np is None:
            self.events.put((
                "response",
                "Voice mode is unavailable. Install the requirements with: "
                "python -m pip install -r requirements.txt"
            ))
            return

        recognizer = sr.Recognizer()
        recognizer.dynamic_energy_threshold = True
        recognizer.pause_threshold = 0.7
        sample_rate = 16000

        def record(seconds):
            frames = int(sample_rate * seconds)
            data = sd.rec(
                frames,
                samplerate=sample_rate,
                channels=1,
                dtype="int16",
            )
            sd.wait()
            pcm = np.asarray(data, dtype=np.int16).reshape(-1).tobytes()
            return sr.AudioData(pcm, sample_rate, 2)

        try:
            sd.query_devices(kind="input")
        except Exception as exc:
            self.events.put((
                "response",
                "Report. I could not access the microphone. Check Windows "
                "microphone permissions and your default input device."
            ))
            print(f"[microphone error] {exc}")
            return

        self.events.put(("voice_state", "listening"))

        while self.running:
            try:
                audio = record(4.0)

                try:
                    heard = recognizer.recognize_google(audio).strip()
                except sr.UnknownValueError:
                    continue
                except sr.RequestError as exc:
                    print(f"[speech recognition error] {exc}")
                    self.events.put((
                        "response",
                        "Report. Speech recognition is unavailable. Check your "
                        "internet connection."
                    ))
                    time.sleep(2)
                    continue

                lower = heard.lower()
                wake = None
                for word in WAKE_WORDS:
                    if re.search(r"\b" + re.escape(word) + r"\b", lower):
                        wake = word
                        break

                if not wake:
                    continue

                rest = re.sub(
                    r"\b" + re.escape(wake) + r"\b",
                    "",
                    heard,
                    count=1,
                    flags=re.IGNORECASE,
                ).strip(" ,.!?")

                if not rest:
                    self.events.put((
                        "voice_state",
                        "listening",
                    ))
                    try:
                        audio = record(7.0)
                        rest = recognizer.recognize_google(audio).strip()
                    except (sr.UnknownValueError, sr.RequestError):
                        rest = ""
                    except Exception as exc:
                        print(f"[command recording error] {exc}")
                        rest = ""

                if rest:
                    self.events.put(("user_voice", rest))
                    self.run_command(rest)

            except Exception as exc:
                print(f"[voice loop] {exc}")
                time.sleep(0.5)

        self.events.put(("voice_state", "idle"))

    def toggle_listening(self):
        # Voice mode is always wake-word driven. This button gives visual feedback
        # and reminds the user how to activate it.
        if sr is None or sd is None or np is None:
            messagebox.showinfo(
                "Voice unavailable",
                "Voice mode needs SpeechRecognition, SoundDevice, and NumPy.\n\n"
                "Run: python -m pip install -r requirements.txt"
            )
            return

        self.set_status("listening", "listening")
        self.add_message(
            "Ciel",
            "Listening. Say your command now.",
            "ciel",
        )

    # --------------------------------------------------------
    # Monitoring / animation
    # --------------------------------------------------------

    def refresh_clock(self):
        self.clock_label.config(
            text=dt.datetime.now().strftime("%I:%M %p").lstrip("0")
        )
        self.root.after(1000, self.refresh_clock)

    def refresh_system_cards(self):
        try:
            if psutil:
                cpu = psutil.cpu_percent(interval=None)
                ram = psutil.virtual_memory().percent
                self.card_labels["CPU"].config(text=f"{cpu:.0f}%")
                self.card_labels["RAM"].config(text=f"{ram:.0f}%")

                battery = psutil.sensors_battery()
                if battery:
                    self.card_labels["BATTERY"].config(
                        text=f"{battery.percent:.0f}%"
                        + (" ⚡" if battery.power_plugged else "")
                    )
                else:
                    self.card_labels["BATTERY"].config(text="N/A")

                online = self.engine.internet_ok()
                self.card_labels["NETWORK"].config(
                    text="ONLINE" if online else "OFFLINE",
                    fg=self.ACCENT_2 if online else "#FF7185",
                )
        except Exception:
            pass

        if self.running:
            self.root.after(2500, self.refresh_system_cards)

    def animate_orb(self):
        self.animation_phase += 1
        self.draw_orb()
        if self.running:
            self.root.after(50, self.animate_orb)

    def draw_orb(self, _event=None):
        canvas = self.orb_canvas
        canvas.delete("all")

        width = max(canvas.winfo_width(), 400)
        height = max(canvas.winfo_height(), 150)
        cx = width // 2
        cy = height // 2

        phase = self.animation_phase / 8
        pulse = int((1 + __import__("math").sin(phase)) * 4)

        # Outer rings
        for radius, alpha_offset in (
            (62 + pulse, 0),
            (48 + pulse // 2, 1),
            (34, 2),
        ):
            canvas.create_oval(
                cx - radius,
                cy - radius,
                cx + radius,
                cy + radius,
                outline=self.ACCENT if alpha_offset == 0 else self.ACCENT_2,
                width=1 if alpha_offset else 2,
            )

        canvas.create_oval(
            cx - 24 - pulse // 2,
            cy - 24 - pulse // 2,
            cx + 24 + pulse // 2,
            cy + 24 + pulse // 2,
            fill=self.ACCENT,
            outline="",
        )

        canvas.create_oval(
            cx - 10,
            cy - 10,
            cx + 10,
            cy + 10,
            fill=self.ACCENT_2,
            outline="",
        )

        canvas.create_text(
            cx,
            cy + 88,
            text="C I E L  •  WISDOM CORE",
            fill=self.MUTED,
            font=("Segoe UI", 8, "bold"),
        )

    def close(self):
        self.running = False
        try:
            self.voice.stop()
        except Exception:
            pass
        self.root.destroy()


def main():
    root = tk.Tk()
    try:
        root.tk.call("tk", "scaling", 1.1)
    except Exception:
        pass

    app = CielApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
