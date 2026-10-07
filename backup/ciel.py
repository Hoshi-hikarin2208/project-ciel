"""
CIEL - Voice assistant in the style of Raphael, the Wisdom King.

Say "Ciel" to wake her, then give a command (or say it all at once:
"Ciel, what's the weather in Cebu").

NEW in v2 (writing + problem solving; the writing/analysis features need the API key below):
    "Ciel, write a poem about the rain"      -> poems, stories, essays, letters, speeches, lyrics
    "make it shorter" / "rewrite it sadder"  -> revises the last piece; "read it again" reads it out
    "start dictation"                        -> speak freely, she writes it down ("stop dictation" to end)
    "diagnose my laptop"                     -> checks CPU, RAM, disk, battery, uptime, internet
    "check my wifi"                          -> DNS, latency and download-speed test with a verdict
    "proofread my clipboard"                 -> fixes copied text; "find problems in my clipboard"
                                                reviews copied code, errors or writing
    "identify the problem: my printer prints blank pages" -> ranked causes and fixes
    "suggest something to watch" / "what should I eat" / "I'm bored" / "recommend a study method"
    "give me ideas for my project"           -> three options plus her top pick; say "another" for more
                                                (works offline for food, shows, music, study and activities)
    On startup she also offers a time-of-day suggestion.
    Everything she writes is saved in the Ciel_Writings folder in your home directory.

Setup:
    pip install SpeechRecognition pyttsx3 requests pyaudio psutil

Optional smarter brain (answers anything, with Raphael's tone):
    set ANTHROPIC_API_KEY=your_key        (Windows)
    export ANTHROPIC_API_KEY=your_key     (Mac/Linux)

Run:
    python ciel.py            # voice mode
    python ciel.py --text     # type commands instead (good for testing)
"""

import ast
import datetime
import operator
import os
import platform
import random
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from urllib.parse import quote_plus

import requests

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

WAKE_WORDS = ("ciel", "seal", "ceil", "kiel", "sealed")  # common mis-hearings of "Ciel"
NOTES_FILE = os.path.join(os.path.expanduser("~"), "ciel_notes.txt")
API_KEY = os.environ.get("ANTHROPIC_API_KEY")
MODEL = "claude-sonnet-5-5"
HEADERS = {"User-Agent": "Ciel-Assistant/1.0"}

SYSTEM_PROMPT = (
    "You are Ciel, a calm, precise, slightly formal AI assistant in the style of "
    "Raphael, the Wisdom King. Address the user as 'Master'. Begin factual answers "
    "with 'Answer.' Keep replies to 1-3 short sentences, since they are spoken aloud. "
    "No markdown, no lists, no emojis."
)

WRITINGS_DIR = os.path.join(os.path.expanduser("~"), "Ciel_Writings")

WRITER_PROMPT = (
    "You are Ciel, a gifted writer with the precision of Raphael, the Wisdom King. "
    "Write exactly what is requested with vivid, original, polished language. "
    "If a poem has no stated form, choose the one that fits best. "
    "Plain text only: no markdown, no title unless asked, no preface, no closing remarks."
)

TROUBLE_PROMPT = (
    "You are Ciel, a precise technical diagnostician in the style of Raphael, the Wisdom King. "
    "Identify the most likely cause or causes, ranked by probability, then give the fix steps. "
    "Be concise and speakable: plain text, no markdown, no emojis. If information is missing, "
    "name the single most useful thing to check. Address the user as 'Master'."
)

NEED_KEY = (
    "Notification. Writing and deep analysis need my full faculties, Master. "
    "Set the ANTHROPIC_API_KEY environment variable and restart me to unlock them."
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
        "notepad": "notepad", "calculator": "calc", "paint": "mspaint",
        "word": "winword", "excel": "excel", "powerpoint": "powerpnt",
        "file explorer": "explorer", "explorer": "explorer", "settings": "ms-settings:",
        "task manager": "taskmgr", "command prompt": "cmd", "chrome": "chrome",
        "edge": "msedge", "vscode": "code", "visual studio code": "code",
    },
    "Darwin": {
        "notepad": "TextEdit", "calculator": "Calculator", "word": "Microsoft Word",
        "excel": "Microsoft Excel", "powerpoint": "Microsoft PowerPoint",
        "finder": "Finder", "settings": "System Settings", "chrome": "Google Chrome",
        "safari": "Safari", "vscode": "Visual Studio Code", "terminal": "Terminal",
    },
    "Linux": {
        "calculator": "gnome-calculator", "files": "nautilus", "terminal": "gnome-terminal",
        "chrome": "google-chrome", "firefox": "firefox", "vscode": "code",
        "text editor": "gedit",
    },
}


# ---------------------------------------------------------------- voice out
class Voice:
    def __init__(self, enabled=True):
        self.enabled = enabled and pyttsx3 is not None
        self.lock = threading.Lock()

    def say(self, text):
        print(f"Ciel: {text}")
        if not self.enabled:
            return
        with self.lock:
            try:
                engine = pyttsx3.init()  # fresh engine each time avoids hangs on some systems
                engine.setProperty("rate", 175)
                voices = engine.getProperty("voices")
                for v in voices:  # prefer a female voice if one exists
                    if "female" in v.name.lower() or "zira" in v.name.lower() or "samantha" in v.name.lower():
                        engine.setProperty("voice", v.id)
                        break
                engine.say(text)
                engine.runAndWait()
                engine.stop()
            except Exception as e:
                print(f"[voice error: {e}]")


# ---------------------------------------------------------------- skills
def tell_time():
    return datetime.datetime.now().strftime("Answer. It is %I:%M %p, Master.").replace(" 0", " ")


def tell_date():
    return datetime.datetime.now().strftime("Answer. Today is %A, %B %d, %Y.")


def weather(city=""):
    try:
        city = quote_plus(city.strip())
        r = requests.get(
            f"https://wttr.in/{city}?format=%l:+%C,+%t,+feels+like+%f,+humidity+%h",
            headers=HEADERS, timeout=8,
        )
        r.raise_for_status()
        return "Report. " + r.text.strip()
    except Exception:
        return "Report. I could not reach the weather service. Please check the connection, Master."


def web_answer(query):
    """Quick factual answer without an API key: DuckDuckGo instant answer, then Wikipedia."""
    try:
        r = requests.get(
            "https://api.duckduckgo.com/",
            params={"q": query, "format": "json", "no_html": 1, "skip_disambig": 1},
            headers=HEADERS, timeout=8,
        )
        data = r.json()
        if data.get("AbstractText"):
            return "Answer. " + trim(data["AbstractText"])
        if data.get("Answer"):
            return "Answer. " + str(data["Answer"])
    except Exception:
        pass
    try:
        r = requests.get(
            "https://en.wikipedia.org/w/api.php",
            params={"action": "query", "list": "search", "srsearch": query,
                    "format": "json", "srlimit": 1},
            headers=HEADERS, timeout=8,
        )
        hits = r.json()["query"]["search"]
        if hits:
            title = hits[0]["title"]
            s = requests.get(
                f"https://en.wikipedia.org/api/rest_v1/page/summary/{quote_plus(title)}",
                headers=HEADERS, timeout=8,
            ).json()
            if s.get("extract"):
                return "Answer. " + trim(s["extract"])
    except Exception:
        pass
    return None


def trim(text, limit=320):
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= limit:
        return text
    cut = text[:limit]
    end = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "))
    return cut[: end + 1] if end > 80 else cut.rstrip() + "..."


history = []


def ask_brain(question):
    """Use Claude if an API key is set; otherwise fall back to web lookup."""
    if API_KEY:
        history.append({"role": "user", "content": question})
        del history[:-8]
        try:
            r = requests.post(
                "https://api.anthropic.com/v1/messages",
                headers={"x-api-key": API_KEY, "anthropic-version": "2023-06-01",
                         "content-type": "application/json"},
                json={"model": MODEL, "max_tokens": 300, "system": SYSTEM_PROMPT,
                      "messages": history},
                timeout=30,
            )
            r.raise_for_status()
            reply = "".join(b.get("text", "") for b in r.json()["content"]).strip()
            history.append({"role": "assistant", "content": reply})
            return reply
        except Exception as e:
            history.pop()
            print(f"[brain error: {e}]")
    result = web_answer(question)
    if result:
        return result
    webbrowser.open(f"https://www.google.com/search?q={quote_plus(question)}")
    return "Notification. I found no direct answer, so I opened the search results for you, Master."


def open_target(name):
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
        webbrowser.open(f"https://www.google.com/search?q={quote_plus(name)}")
        return f"I could not find an app called {name}, so I searched the web instead."


def play_youtube(query):
    webbrowser.open(f"https://www.youtube.com/results?search_query={quote_plus(query)}")
    return f"Understood. Searching YouTube for {query}."


def take_note(text):
    with open(NOTES_FILE, "a", encoding="utf-8") as f:
        f.write(f"[{datetime.datetime.now():%Y-%m-%d %H:%M}] {text}\n")
    return "Understood. The note has been recorded."


def read_notes():
    if not os.path.exists(NOTES_FILE):
        return "Report. There are no notes yet, Master."
    with open(NOTES_FILE, encoding="utf-8") as f:
        lines = f.read().strip().splitlines()[-5:]
    return "Report. Your latest notes: " + " ... ".join(lines) if lines else "There are no notes."


def set_timer(seconds, voice):
    def ring():
        time.sleep(seconds)
        voice.say("Notification. Master, your timer has finished.")
    threading.Thread(target=ring, daemon=True).start()
    mins, secs = divmod(int(seconds), 60)
    parts = (f"{mins} minute{'s' if mins != 1 else ''}" if mins else "") + \
            (f" {secs} seconds" if secs else "")
    return f"Understood. Timer set for {parts.strip()}."


OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
       ast.Div: operator.truediv, ast.Pow: operator.pow, ast.USub: operator.neg,
       ast.Mod: operator.mod}


def safe_eval(node):
    if isinstance(node, ast.Expression):
        return safe_eval(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in OPS:
        return OPS[type(node.op)](safe_eval(node.left), safe_eval(node.right))
    if isinstance(node, ast.UnaryOp) and type(node.op) in OPS:
        return OPS[type(node.op)](safe_eval(node.operand))
    raise ValueError("unsupported")


def calculate(expr):
    expr = (expr.lower().replace("plus", "+").replace("minus", "-")
            .replace("times", "*").replace("multiplied by", "*").replace("x", "*")
            .replace("divided by", "/").replace("over", "/").replace("to the power of", "**")
            .replace("^", "**").replace("mod", "%"))
    expr = re.sub(r"[^0-9+\-*/%.() ]", "", expr)
    try:
        result = safe_eval(ast.parse(expr.strip(), mode="eval"))
        result = round(result, 6)
        return f"Answer. The result is {result:g}."
    except Exception:
        return None


def system_status():
    if not psutil:
        return "Report. Install psutil so I can read system status, Master."
    cpu = psutil.cpu_percent(interval=0.5)
    ram = psutil.virtual_memory().percent
    msg = f"Report. CPU usage is {cpu:.0f} percent, memory usage is {ram:.0f} percent"
    bat = psutil.sensors_battery() if hasattr(psutil, "sensors_battery") else None
    if bat:
        msg += f", battery is at {bat.percent:.0f} percent{' and charging' if bat.power_plugged else ''}"
    return msg + "."


def parse_duration(text):
    total = 0
    for num, unit in re.findall(r"(\d+)\s*(hour|minute|min|second|sec)", text):
        n = int(num)
        total += n * (3600 if unit == "hour" else 60 if unit.startswith("min") else 1)
    return total


# ---------------------------------------------------------------- router
class Stop(Exception):
    pass


# ---------------------------------------------------------------- writing & problem solving
last_writing = {"request": "", "text": ""}


def claude_once(messages, system, max_tokens=800):
    """Single Claude call. Returns the text, or None if there is no key or the call fails."""
    if not API_KEY:
        return None
    try:
        r = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={"x-api-key": API_KEY, "anthropic-version": "2023-06-01",
                     "content-type": "application/json"},
            json={"model": MODEL, "max_tokens": max_tokens, "system": system,
                  "messages": messages},
            timeout=60,
        )
        r.raise_for_status()
        return "".join(b.get("text", "") for b in r.json()["content"]).strip()
    except Exception as e:
        print(f"[brain error: {e}]")
        return None


def save_writing(title, text):
    os.makedirs(WRITINGS_DIR, exist_ok=True)
    slug = re.sub(r"[^a-z0-9]+", "_", title.lower()).strip("_")[:40] or "writing"
    path = os.path.join(WRITINGS_DIR, f"{slug}_{datetime.datetime.now():%Y%m%d_%H%M%S}.txt")
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path


def open_path(path):
    try:
        system = platform.system()
        if system == "Windows":
            os.startfile(path)  # type: ignore[attr-defined]
        elif system == "Darwin":
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])
    except Exception:
        pass


def speak_or_file(title, text, limit=900):
    """Print + save the full text. Speak it all if short, otherwise speak the opening and open the file."""
    path = save_writing(title, text)
    print("\n" + text + f"\n[saved: {path}]\n")
    if len(text) <= limit:
        return text
    open_path(path)
    return trim(text, 500) + " The full text is on your screen, Master."


def write_creative(request):
    if not API_KEY:
        return NEED_KEY
    text = claude_once([{"role": "user", "content": request}], WRITER_PROMPT, 1800)
    if not text:
        return "Report. I could not reach my writing faculty. Please check the connection, Master."
    last_writing.update(request=request, text=text)
    return speak_or_file(request, text)


def revise_writing(instruction):
    if not last_writing["text"]:
        return "There is nothing to revise yet. Ask me to write something first, Master."
    if not API_KEY:
        return NEED_KEY
    msgs = [
        {"role": "user", "content": last_writing["request"]},
        {"role": "assistant", "content": last_writing["text"]},
        {"role": "user", "content": instruction},
    ]
    text = claude_once(msgs, WRITER_PROMPT, 1800)
    if not text:
        return "Report. I could not reach my writing faculty. Please check the connection, Master."
    last_writing["text"] = text
    return speak_or_file(last_writing["request"], text)


def dictate(voice, listen_fn):
    """Take free-form dictation until the user says 'stop dictation'."""
    voice.say("Dictation started. Say 'stop dictation' when you are finished, Master.")
    lines = []
    while True:
        t = listen_fn()
        if not t:
            continue
        if "stop dictation" in t.lower() or "end dictation" in t.lower():
            break
        lines.append(t)
        print(f"  + {t}")
    if not lines:
        return "Dictation ended. Nothing was recorded."
    raw = " ".join(lines)
    polished = claude_once(
        [{"role": "user", "content": "Add correct punctuation, capitalization and paragraph "
          "breaks to this dictated text. Do not change the wording. Return only the text.\n\n" + raw}],
        WRITER_PROMPT, 2000,
    )
    path = save_writing("dictation", polished or raw)
    open_path(path)
    return "Understood. Your dictation has been written down and opened on your screen."


def get_clipboard():
    try:
        import tkinter
        root = tkinter.Tk()
        root.withdraw()
        text = root.clipboard_get()
        root.destroy()
        return text
    except Exception:
        return ""


def set_clipboard(text):
    try:
        import tkinter
        root = tkinter.Tk()
        root.withdraw()
        root.clipboard_clear()
        root.clipboard_append(text)
        root.update()  # keeps the text on the clipboard after the window closes (Windows/macOS)
        root.destroy()
        return True
    except Exception:
        return False


def basic_text_check(text):
    """Offline mini-proofreader used when there is no API key."""
    issues = []
    for m in re.finditer(r"\b(\w+)\s+\1\b", text, re.I):
        issues.append(f"repeated word '{m.group(1)}'")
    if re.search(r" {2,}", text):
        issues.append("extra spaces")
    if re.search(r"(?<![\w'])i(?![\w'.])", text):
        issues.append("a lowercase 'i' that should be capitalized")
    if text[:1].islower():
        issues.append("it starts with a lowercase letter")
    if text.rstrip()[-1:] not in ".!?\"')":
        issues.append("there is no ending punctuation")
    if not issues:
        return "Report. My basic check found nothing. Set the API key for a full proofread."
    return "Report. My basic check found: " + "; ".join(issues[:6]) + "."


def analyze_clipboard(c):
    text = get_clipboard().strip()
    if not text:
        return "Report. The clipboard is empty. Copy the text, code, or error message first, Master."
    if re.search(r"proofread|grammar|spell|correct|edit|fix my (writing|text)", c):
        if not API_KEY:
            return basic_text_check(text)
        fixed = claude_once(
            [{"role": "user", "content": "Proofread and correct this text. Fix spelling, grammar "
              "and punctuation but keep the author's voice. Return only the corrected text.\n\n" + text}],
            WRITER_PROMPT, 2000,
        )
        if not fixed:
            return "Report. I could not reach my writing faculty, Master."
        path = save_writing("proofread", fixed)
        print("\n" + fixed + f"\n[saved: {path}]\n")
        copied = set_clipboard(fixed)
        return "Understood. The corrected text is saved" + (" and placed on your clipboard." if copied else ".")
    if not API_KEY:
        return NEED_KEY
    if re.search(r"summar|explain|what does", c):
        prompt = "Explain what this says or does in plain language, briefly.\n\n" + text
        system = TROUBLE_PROMPT
    else:
        prompt = ("Review the following text, code, or error message. Identify the concrete problems "
                  "(errors, bugs, logic or factual issues, unclear or weak parts), ranked by importance, "
                  "and say how to fix each one.\n\n" + text)
        system = TROUBLE_PROMPT
    out = claude_once([{"role": "user", "content": prompt}], system, 900)
    if not out:
        return "Report. I could not reach my analysis faculty, Master."
    return speak_or_file("clipboard_analysis", out, limit=600)


def analyze_problem(cmd):
    problem = re.sub(
        r"^(?:please\s+)?(?:analy[sz]e|identify|diagnose|troubleshoot|find|solve|"
        r"what'?s wrong with|what is wrong with|help me fix|how do i fix|i have an? (?:problem|issue))"
        r"\s*(?:the\s+|this\s+|my\s+)?(?:problem|issue|error|bug)?\s*(?:with|in|:|-)?\s*",
        "", cmd.strip(), flags=re.I,
    ).strip()
    if not problem:  # nothing described aloud, so use whatever they copied
        return analyze_clipboard("identify problems")
    if not API_KEY:
        webbrowser.open(f"https://www.google.com/search?q={quote_plus('how to fix ' + problem)}")
        return ("Notification. Without my full faculties I opened a search for the fix, Master. "
                "Set the API key for real diagnosis.")
    out = claude_once([{"role": "user", "content": problem}], TROUBLE_PROMPT, 500)
    if not out:
        return "Report. I could not reach my analysis faculty, Master."
    return speak_or_file("problem", out, limit=600)


SUGGEST_PROMPT = (
    "You are Ciel, a wise and precise advisor in the style of Raphael, the Wisdom King. "
    "Give exactly three concise suggestions that fit the request and the time of day given, "
    "introduced as 'First', 'Second' and 'Third', then one short sentence beginning "
    "'Recommendation.' naming your top pick and why. Plain text only: no markdown, no bullet "
    "symbols, no emojis. Address the user as 'Master'."
)

OFFLINE_SUGGESTIONS = {
    "food": ["rice with fried egg and vegetables", "chicken adobo", "pancit canton", "sinigang",
             "a tuna sandwich with fruit", "vegetable omelette", "a banana and peanut butter snack",
             "tortang talong with rice"],
    "watch": ["a documentary on something you have never studied", "a classic anime series",
              "a feel-good comedy movie", "a mystery thriller", "a short science explainer playlist",
              "a fantasy anime with strong world-building"],
    "music": ["a lo-fi focus playlist", "instrumental film scores", "your favorite old playlist",
              "acoustic covers", "calm piano music", "an upbeat pop mix"],
    "study": ["a 25-minute focused session followed by a 5-minute break", "active recall: close the notes and write what you remember",
              "teach the topic aloud as if to a classmate", "make a one-page summary sheet",
              "practice with past questions", "study the hardest topic first while fresh"],
    "do": ["take a 10-minute walk", "tidy your desk for five minutes", "write down three goals for tomorrow",
           "learn one new thing for 15 minutes", "stretch and drink water", "call or message a friend",
           "sketch, write, or build something small", "review your notes"],
}

last_suggestion = {"request": "", "reply": ""}


def time_context():
    now = datetime.datetime.now()
    part = ("morning" if 5 <= now.hour < 12 else "afternoon" if now.hour < 17
            else "evening" if now.hour < 21 else "night")
    return f"It is {now:%A} {part}, {now:%I:%M %p}."


def offline_suggestion(request):
    r = request.lower()
    if re.search(r"eat|cook|food|dinner|lunch|breakfast|snack|meal|hungry", r):
        key = "food"
    elif re.search(r"watch|movie|anime|show|series|film", r):
        key = "watch"
    elif re.search(r"listen|music|song|playlist", r):
        key = "music"
    elif re.search(r"study|review|exam|learn|reviewer|memorize", r):
        key = "study"
    elif re.search(r"bored|what should i do|something to do|do today|do now|free time", r):
        key = "do"
    else:  # a specific topic the built-in lists cannot cover
        webbrowser.open(f"https://www.google.com/search?q={quote_plus(request)}")
        return ("Notification. That topic is beyond my built-in ideas, so I opened a search, Master. "
                "Set the API key for tailored suggestions.")
    first, second, third = random.sample(OFFLINE_SUGGESTIONS[key], 3)
    return (f"Suggestion. First, {first}. Second, {second}. Third, {third}. "
            "For more tailored ideas, set the API key, Master.")


def suggest(request, more=False):
    if not API_KEY:
        return offline_suggestion(request)
    if more and last_suggestion["reply"]:
        msgs = [
            {"role": "user", "content": f"{time_context()} {last_suggestion['request']}"},
            {"role": "assistant", "content": last_suggestion["reply"]},
            {"role": "user", "content": "Give me three different suggestions."},
        ]
        base_request = last_suggestion["request"]
    else:
        msgs = [{"role": "user", "content": f"{time_context()} {request}"}]
        base_request = request
    out = claude_once(msgs, SUGGEST_PROMPT, 500)
    if not out:
        return offline_suggestion(base_request)
    last_suggestion.update(request=base_request, reply=out)
    return out


def startup_suggestion():
    h = datetime.datetime.now().hour
    if 5 <= h < 12:
        tips = ["start with your hardest task while your mind is fresh",
                "plan your top three tasks for today", "drink water and stretch before you begin"]
        greet = "Good morning"
    elif 12 <= h < 17:
        tips = ["take a short break, then do one focused 25-minute session",
                "review what you have finished so far today", "eat something light to keep your energy up"]
        greet = "Good afternoon"
    elif 17 <= h < 21:
        tips = ["review today's progress and prepare tomorrow's list",
                "wrap up one unfinished task before resting", "step away from the screen for a short walk"]
        greet = "Good evening"
    else:
        tips = ["finish up and get some rest, since sleep helps memory and focus",
                "save your work and plan tomorrow in two minutes", "reduce screen brightness and wind down"]
        greet = "It is late"
    return f"{greet}, Master. Suggestion: {random.choice(tips)}. Say 'suggest something' any time."


def internet_ok():
    try:
        socket.create_connection(("1.1.1.1", 443), timeout=3).close()
        return True
    except OSError:
        return False


def diagnose_laptop():
    if not psutil:
        return "Report. Install psutil so I can inspect the system, Master."
    cores = psutil.cpu_count() or 1
    procs = list(psutil.process_iter(["name", "memory_percent"]))
    for p in procs:
        try:
            p.cpu_percent(None)  # prime the counters
        except Exception:
            pass
    cpu = psutil.cpu_percent(interval=1.5)
    cpu_top = []
    for p in procs:
        try:
            name = p.info.get("name") or "?"
            if name != "System Idle Process":
                cpu_top.append((p.cpu_percent(None) / cores, name))
        except Exception:
            pass
    cpu_top.sort(reverse=True)
    mem_top = sorted(((p.info.get("memory_percent") or 0, p.info.get("name") or "?") for p in procs),
                     reverse=True)
    ram = psutil.virtual_memory().percent
    disk = shutil.disk_usage(os.path.expanduser("~"))
    disk_pct, free_gb = disk.used / disk.total * 100, disk.free / 1e9
    uptime_days = (time.time() - psutil.boot_time()) / 86400
    bat = psutil.sensors_battery() if hasattr(psutil, "sensors_battery") else None
    online = internet_ok()

    top_cpu = cpu_top[0][1] if cpu_top else "unknown"
    top_mem = mem_top[0][1] if mem_top else "unknown"
    facts = (
        f"CPU {cpu:.0f}% (busiest: {', '.join(n for _, n in cpu_top[:3])}); "
        f"RAM {ram:.0f}% (largest: {', '.join(n for _, n in mem_top[:3])}); "
        f"disk {disk_pct:.0f}% used, {free_gb:.0f} GB free; uptime {uptime_days:.1f} days; "
        + (f"battery {bat.percent:.0f}% {'charging' if bat.power_plugged else 'on battery'}; " if bat else "")
        + f"internet {'OK' if online else 'DOWN'}"
    )
    print("\n--- Laptop diagnosis ---\n" + facts + "\n")

    problems = []
    if cpu > 85:
        problems.append(f"the processor is overloaded at {cpu:.0f} percent, mostly by {top_cpu}. Close or restart it")
    if ram > 85:
        problems.append(f"memory is nearly full at {ram:.0f} percent, led by {top_mem}. Close heavy apps and browser tabs")
    if disk_pct > 90 or free_gb < 10:
        problems.append(f"the drive is almost full with {free_gb:.0f} gigabytes free. Empty the recycle bin and run disk cleanup")
    if uptime_days > 7:
        problems.append(f"it has run {uptime_days:.0f} days without a restart. Restart the laptop")
    if bat and not bat.power_plugged and bat.percent < 20:
        problems.append(f"the battery is low at {bat.percent:.0f} percent. Plug in the charger")
    if not online:
        problems.append("the internet is unreachable. Check the WiFi connection or restart the router")

    if API_KEY:
        out = claude_once(
            [{"role": "user", "content": "Laptop metrics: " + facts + ". Identify any problems and give "
              "the top fixes in 3 to 4 short sentences. If everything looks healthy, say so."}],
            TROUBLE_PROMPT, 400,
        )
        if out:
            return out
    if not problems:
        return "Answer. No problems detected, Master. All systems are within normal limits."
    return f"Report. I found {len(problems)} problem{'s' if len(problems) != 1 else ''}. " + \
           ". ".join(p[0].upper() + p[1:] for p in problems) + "."


def check_network():
    try:
        socket.gethostbyname("google.com")
        dns_ok = True
    except OSError:
        dns_ok = False

    def tcp_ms(host):
        try:
            start = time.time()
            socket.create_connection((host, 443), timeout=4).close()
            return (time.time() - start) * 1000
        except OSError:
            return None

    ip_ms = tcp_ms("1.1.1.1")
    if ip_ms is None:
        return ("Report. There is no internet connection, Master. Check that WiFi is connected, "
                "then restart the router if it still fails.")
    if not dns_ok:
        return ("Report. The connection works but name lookup fails. This is a DNS problem. "
                "Restart the router or switch your DNS to 1.1.1.1 or 8.8.8.8, Master.")
    mbps = None
    try:
        start = time.time()
        data = requests.get("https://speed.cloudflare.com/__down?bytes=3000000", timeout=20).content
        mbps = len(data) * 8 / (time.time() - start) / 1e6
    except Exception:
        pass
    speed = f" Download speed is about {mbps:.0f} megabits per second." if mbps else ""
    if ip_ms > 300 or (mbps is not None and mbps < 2):
        return (f"Report. Your connection is weak. Latency is {ip_ms:.0f} milliseconds.{speed} "
                "Move closer to the router, close streaming or downloads, or restart the router.")
    return f"Answer. Your connection is healthy, Master. Latency is {ip_ms:.0f} milliseconds.{speed}"


def handle(cmd, voice, listen_fn=None):
    c = cmd.lower().strip()
    c = re.sub(r"^(please|can you|could you|would you)\s+", "", c)

    if len(c.split()) <= 4 and any(p in c for p in ("goodbye", "go to sleep", "shut down ciel", "exit ciel", "quit")):
        raise Stop
    if re.search(r"\b(hello|hi|hey)\b", c) and len(c.split()) <= 3:
        return "Greetings, Master. Ciel is ready."
    if "who are you" in c or "your name" in c:
        return "I am Ciel, your assistant. I analyze, search, and carry out tasks on your command, Master."
    # --- writing ---
    if re.match(r"(write|compose|make|create|draft|give|come up with)\b", c) and re.search(
            r"\b(poem|poetry|haiku|sonnet|limerick|story|essay|letter|speech|song|lyrics|caption|"
            r"email|paragraph|script|slogan|speech|message|toast|prayer)\b", c):
        return write_creative(cmd)
    if last_writing["text"] and re.match(
            r"(make it|make that|rewrite|revise|change it|shorten|lengthen|continue|add (a|more)|"
            r"translate (it|that)|now make|improve (it|that))", c):
        return revise_writing(cmd)
    if last_writing["text"] and re.match(r"read (it|that|the (poem|story|essay|letter))( again| to me| aloud)?$", c):
        return last_writing["text"]
    if re.search(r"\b(start|begin)\s+dictat|\bdictation\b|\bdictate\b", c):
        if listen_fn:
            return dictate(voice, listen_fn)
        return "Dictation is not available right now, Master."

    # --- suggestions ---
    if last_suggestion["reply"] and re.match(
            r"(another|more( ideas| options| suggestions)?|something else|give me more|different (ones|options)|"
            r"any other)", c):
        return suggest("", more=True)
    if re.search(r"\b(suggest|suggestions?|recommend|recommendations?|ideas? for|any ideas|i'?m bored|"
                 r"what should i (do|eat|watch|play|listen|study|cook|wear|buy|read|name|call|make))\b", c):
        return suggest(cmd)

    # --- problem solving ---
    if re.search(r"(diagnose|scan|inspect|health ?check|check up).{0,20}\b(laptop|computer|pc|system)\b|"
                 r"why is my (laptop|computer|pc)|\b(laptop|computer|pc) (is |keeps )?"
                 r"(slow|lagging|freezing|hot|overheating)|what'?s wrong with my (laptop|computer|pc)", c):
        return diagnose_laptop()
    if re.search(r"(wi-?fi|internet|network|connection).{0,25}(slow|not working|problem|down|check|test)|"
                 r"(check|test) (my )?(wi-?fi|internet|connection|network)|speed test", c):
        return check_network()
    if "clipboard" in c or "what i copied" in c or "what i just copied" in c:
        return analyze_clipboard(c)
    if re.match(r"(please )?(analy[sz]e|identify|troubleshoot|diagnose|what'?s wrong with|what is wrong with|"
                r"help me fix|how do i fix|i have an? (problem|issue)|solve)\b", c):
        return analyze_problem(cmd)

    if "what time" in c or c == "time":
        return tell_time()
    if "what's the date" in c or "what is the date" in c or "today's date" in c or "what day" in c:
        return tell_date()
    if "weather" in c or "temperature" in c:
        m = re.search(r"(?:in|at|for)\s+([a-z .]+)$", c)
        return weather(m.group(1) if m else "")
    if c.startswith(("open ", "launch ", "start ")):
        return open_target(c.split(" ", 1)[1])
    m = re.match(r"(?:play|watch)\s+(.+?)(?:\s+on youtube)?$", c)
    if m:
        return play_youtube(m.group(1))
    if "timer" in c or "remind me in" in c:
        secs = parse_duration(c)
        if secs:
            return set_timer(secs, voice)
        return "Please state the duration, Master."
    m = re.match(r"(?:take a note|note down|remember|write down)\s*(?:that)?\s*(.+)", c)
    if m:
        return take_note(m.group(1))
    if "read my notes" in c or "read notes" in c:
        return read_notes()
    if any(w in c for w in ("system status", "battery", "cpu", "memory usage", "how's my laptop")):
        return system_status()
    m = re.match(r"(?:calculate|compute|what is|what's|how much is)\s+([\d\s+\-*/x^%().]+|.*\b(?:plus|minus|times|divided by|multiplied by|over)\b.*)$", c)
    if m:
        res = calculate(m.group(1))
        if res:
            return res
    m = re.match(r"(?:search(?: for| the web for)?|google|look up)\s+(.+)", c)
    if m:
        q = m.group(1)
        return web_answer(q) or (webbrowser.open(f"https://www.google.com/search?q={quote_plus(q)}")
                                 and "Notification. Search results are open.")
    if "screenshot" in c:
        try:
            import pyautogui  # optional
            path = os.path.join(os.path.expanduser("~"), f"ciel_{int(time.time())}.png")
            pyautogui.screenshot(path)
            return "Understood. Screenshot saved to your home folder."
        except Exception:
            return "Install pyautogui to enable screenshots, Master."

    return ask_brain(cmd)


# ---------------------------------------------------------------- listening
def strip_wake(text):
    """Return (woke, remainder) if text contains a wake word."""
    words = text.lower().split()
    for i, w in enumerate(words):
        if w.strip(",.!?") in WAKE_WORDS:
            return True, " ".join(words[i + 1:]).strip(" ,.")
    return False, ""


def listen(recognizer, mic, timeout, limit):
    with mic as source:
        try:
            audio = recognizer.listen(source, timeout=timeout, phrase_time_limit=limit)
        except sr.WaitTimeoutError:
            return ""
    try:
        return recognizer.recognize_google(audio)
    except sr.UnknownValueError:
        return ""
    except sr.RequestError:
        print("[speech service unreachable - check WiFi]")
        return ""


def run_voice(voice):
    r = sr.Recognizer()
    r.dynamic_energy_threshold = True
    r.pause_threshold = 0.7
    mic = sr.Microphone()
    voice.say("Calibrating to ambient noise.")
    with mic as source:
        r.adjust_for_ambient_noise(source, duration=1.2)
    voice.say("Ciel online. Say my name when you need me, Master.")
    voice.say(startup_suggestion())

    while True:
        heard = listen(r, mic, timeout=None, limit=5)
        if not heard:
            continue
        print(f"(heard: {heard})")
        woke, rest = strip_wake(heard)
        if not woke:
            continue
        if not rest:
            voice.say("Yes, Master?")
            rest = listen(r, mic, timeout=6, limit=10)
            print(f"(command: {rest})")
            if not rest:
                voice.say("I did not receive a command. Returning to standby.")
                continue
        try:
            voice.say(handle(rest, voice, lambda: listen(r, mic, 8, 15)))
        except Stop:
            voice.say("Understood. Entering standby. Goodbye, Master.")
            return
        except Exception as e:
            print(f"[error: {e}]")
            voice.say("Report. An error occurred while processing that command.")


def run_text(voice):
    voice.say("Ciel online in text mode. Type 'quit' to exit.")
    voice.say(startup_suggestion())
    while True:
        try:
            cmd = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            return
        if not cmd:
            continue
        _, rest = strip_wake(cmd)
        try:
            voice.say(handle(rest or cmd, voice, lambda: input("  dictate> ").strip()))
        except Stop:
            voice.say("Goodbye, Master.")
            return


def main():
    text_mode = "--text" in sys.argv
    voice = Voice(enabled=True)
    if text_mode or sr is None:
        if sr is None and not text_mode:
            print("SpeechRecognition not installed - falling back to text mode.")
        run_text(voice)
    else:
        try:
            run_voice(voice)
        except OSError as e:
            print(f"Microphone problem: {e}\nFalling back to text mode.")
            run_text(voice)


if __name__ == "__main__":
    main()
