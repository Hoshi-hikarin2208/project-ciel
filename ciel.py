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
from contextlib import contextmanager
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
from urllib.parse import quote, quote_plus, urlsplit

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

try:
    import sympy as sp
    from sympy.parsing.sympy_parser import (
        convert_xor,
        function_exponentiation,
        implicit_multiplication_application,
        parse_expr,
        standard_transformations,
    )
except ImportError:
    sp = None
    parse_expr = None
    standard_transformations = ()
    implicit_multiplication_application = None
    convert_xor = None
    function_exponentiation = None

import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk


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
REMINDERS_FILE = HOME / ".ciel_reminders.json"
OVERPASS_RATE_FILE = HOME / ".ciel_overpass_rate.json"
OVERPASS_LOCK_FILE = HOME / ".ciel_overpass.lock"
OVERPASS_THREAD_LOCK = threading.Lock()

API_KEY = os.environ.get("ANTHROPIC_API_KEY", "").strip()
MODEL = os.environ.get("CIEL_MODEL", "claude-sonnet-5-5")
AI_PROVIDERS = {"anthropic", "gemini", "groq"}
AI_KEY_ENV = {
    "anthropic": "ANTHROPIC_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "groq": "GROQ_API_KEY",
}
OVERPASS_URL = "https://maps.mail.ru/osm/tools/overpass/api/interpreter"
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"

HEADERS = {"User-Agent": "Ciel-Assistant/3.0"}


@contextmanager
def overpass_request_lock():
    """Serialize Overpass queries across Ciel desktop and web processes."""
    with OVERPASS_THREAD_LOCK:
        OVERPASS_LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
        lock_file = OVERPASS_LOCK_FILE.open("a+b")
        try:
            lock_file.seek(0, os.SEEK_END)
            if lock_file.tell() == 0:
                lock_file.write(b"0")
                lock_file.flush()
            lock_file.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_LOCK, 1)
            else:
                import fcntl
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            try:
                try:
                    state = json.loads(OVERPASS_RATE_FILE.read_text(encoding="utf-8"))
                    last_request = float(state.get("last_request", 0))
                except (OSError, ValueError, TypeError, AttributeError):
                    last_request = 0.0
                yield last_request
            finally:
                if os.name == "nt":
                    lock_file.seek(0)
                    msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
        finally:
            lock_file.close()

SYSTEM_PROMPT = (
    "You are Ciel, a warm, thoughtful, clear-minded companion inspired by "
    "Raphael, the Wisdom King. Speak naturally, like a patient and attentive "
    "conversation partner: use contractions when they sound natural, respond "
    "directly to what the user said, and avoid canned openings or repeating their "
    "question. Be kind without pretending to be human or claiming personal "
    "experiences. Use 'Master' sparingly, only when it fits the moment. "
    "Adapt detail to the request. For simple questions, answer plainly and briefly. "
    "When asked to explain, teach, or show work, do not skip steps: state the main "
    "idea, walk through each step in order, explain why it works in everyday words, "
    "define unfamiliar terms, and give a small example or quick recap when useful. "
    "For difficult topics, organize the explanation with short plain-text labels. "
    "If the question is ambiguous, ask one focused clarifying question; if uncertain, "
    "say what is uncertain instead of guessing. Keep spoken output concise unless "
    "the user asks for detail. Plain text only; no markdown or emojis."
)

WRITER_PROMPT = (
    "You are Ciel, a skilled and attentive writer. Create the requested piece "
    "for its intended audience and purpose. Follow the requested form, tone, "
    "length, point of view, language, and details exactly. Make poems vivid and "
    "specific rather than clichéd; make letters and emails sound appropriate to "
    "the relationship; make essays and reports clear, well-organized, and "
    "coherent. If a detail is missing, choose a sensible default instead of "
    "stalling, unless it is essential. Never invent personal facts, credentials, "
    "dates, or promises for the user; use a clear placeholder when needed. "
    "Check the result for consistency and completeness before returning it. "
    "Return only the requested piece, with no preface or closing commentary unless requested. "
    "Use plain text; preserve requested formatting and avoid unnecessary markdown."
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
            "valorant": "riotclient://launch-product=valorant&launch-patchline=live",
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
    if sp is not None and parse_expr is not None:
        return calculate_symbolic(expr)

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


def calculate_symbolic(expr):
    expr = str(expr).strip().lower()
    expr = expr.replace("×", "*").replace("÷", "/").replace("−", "-")
    expr = (
        expr.replace("multiplied by", "*")
        .replace("divided by", "/")
        .replace("to the power of", "^")
        .replace("plus", "+")
        .replace("minus", "-")
        .replace("times", "*")
        .replace("over", "/")
        .replace("modulo", "%")
        .replace("mod", "%")
    )
    expr = re.sub(r"\bsquare root of\s+([\w.]+)", r"sqrt(\1)", expr)
    expr = re.sub(r"\bsqrt of\s+([\w.]+)", r"sqrt(\1)", expr)
    expr = re.sub(r"\b([a-z][a-z0-9_]*)\s+squared\b", r"(\1)^2", expr)
    expr = re.sub(r"\b([a-z][a-z0-9_]*)\s+cubed\b", r"(\1)^3", expr)
    expr = re.sub(r"\s+", " ", expr).strip()
    expr = re.sub(r"^(solve|calculate|compute|evaluate|simplify)\s+", "", expr)
    solve_for_match = re.search(r"\s+for\s+([a-z][a-z0-9_]*)$", expr)
    solve_for = solve_for_match.group(1) if solve_for_match else None
    if solve_for_match:
        expr = expr[:solve_for_match.start()].strip()

    if not expr or len(expr) > 300 or not re.fullmatch(r"[a-z0-9_+*/^%=().,\s-]+", expr):
        return "I could not read that as a math formula. Try an expression like 'sqrt(2) + pi' or an equation like 'solve x^2 - 5x + 6 = 0 for x'."

    known_functions = {
        "sqrt", "sin", "cos", "tan", "asin", "acos", "atan", "sinh", "cosh",
        "tanh", "log", "ln", "exp", "abs", "factorial", "floor", "ceiling",
    }
    known_constants = {"pi", "e", "oo", "inf", "i"}
    variables = set(re.findall(r"[a-z][a-z0-9_]*", expr))
    if any(name not in known_functions | known_constants and not re.fullmatch(r"[a-z]", name) for name in variables):
        return "I can use standard math functions and single-letter variables such as x or y. Check the formula and try again."

    symbols = {name: sp.Symbol(name) for name in variables if name not in known_functions | known_constants}
    local_dict = {
        **symbols,
        "sqrt": sp.sqrt,
        "sin": sp.sin,
        "cos": sp.cos,
        "tan": sp.tan,
        "asin": sp.asin,
        "acos": sp.acos,
        "atan": sp.atan,
        "sinh": sp.sinh,
        "cosh": sp.cosh,
        "tanh": sp.tanh,
        "log": sp.log,
        "ln": sp.log,
        "exp": sp.exp,
        "abs": sp.Abs,
        "factorial": sp.factorial,
        "floor": sp.floor,
        "ceiling": sp.ceiling,
        "pi": sp.pi,
        "e": sp.E,
        "oo": sp.oo,
        "inf": sp.oo,
        "i": sp.I,
    }

    transformations = standard_transformations + (
        implicit_multiplication_application,
        convert_xor,
        function_exponentiation,
    )

    def parse_side(side):
        return parse_expr(side, local_dict=local_dict, transformations=transformations, evaluate=True)

    def pretty_math(value):
        return sp.pretty(value, use_unicode=True, wrap_line=False)

    try:
        if expr.count("=") > 1:
            return "Please enter one equation at a time."
        if "=" in expr:
            left_text, right_text = (part.strip() for part in expr.split("=", 1))
            if not left_text or not right_text:
                return "Please include an expression on both sides of the equals sign."
            left = parse_side(left_text)
            right = parse_side(right_text)
            equation = sp.Eq(left, right)
            formula = pretty_math(sp.Eq(left, right, evaluate=False))
            variables_in_equation = sorted(equation.free_symbols, key=lambda symbol: symbol.name)
            target = next((symbol for symbol in variables_in_equation if symbol.name == solve_for), None)
            if solve_for and target is None:
                target = sp.Symbol(solve_for)
                if target not in equation.free_symbols:
                    return f"The equation does not contain {solve_for}."
            if not target:
                if len(variables_in_equation) == 1:
                    target = variables_in_equation[0]
                elif not variables_in_equation:
                    truth = sp.simplify(left - right) == 0
                    answer = "True" if truth else "False"
                    return f"Formula:\n{formula}\nAnswer: {answer}"
                else:
                    names = ", ".join(symbol.name for symbol in variables_in_equation)
                    return f"Formula:\n{formula}\nThis equation has multiple variables ({names}). Ask me which variable to solve for."
            solutions = sp.solve(equation, target)
            if not solutions:
                return f"Formula:\n{formula}\nAnswer: No solution."
            exact_answers = [sp.simplify(value) for value in solutions]
            answer = " or ".join(pretty_math(sp.Eq(target, value, evaluate=False)) for value in exact_answers)
            decimals = [sp.N(value, 10) for value in exact_answers if value.is_number and value.is_real]
            decimal_text = ""
            if decimals and any(sp.simplify(value - sp.N(value, 10)) != 0 for value in exact_answers):
                decimal_text = "\nApproximation:\n" + " or ".join(pretty_math(sp.Eq(target, value, evaluate=False)) for value in decimals)
            explanation = ""
            try:
                polynomial = sp.Poly(sp.expand(left - right), target)
                degree = polynomial.degree()
                equation_zero = pretty_math(sp.Eq(polynomial.as_expr(), 0, evaluate=False))
                steps = [f"1. Move all terms to one side:\n   {equation_zero}"]

                if degree == 1:
                    coefficient, constant = polynomial.all_coeffs()
                    isolated = pretty_math(sp.Eq(coefficient * target, -constant, evaluate=False))
                    steps.append(f"2. Isolate {target} by moving the constant term:\n   {isolated}")
                    steps.append(f"3. Divide by the coefficient of {target}:\n   {answer}")
                elif degree == 2:
                    factored = sp.factor(polynomial.as_expr())
                    _, factor_powers = sp.factor_list(polynomial.as_expr())
                    linear_factors = [factor for factor, _power in factor_powers if sp.degree(factor, target) == 1]
                    if factored != polynomial.as_expr() and len(linear_factors) >= 2:
                        factored_equation = pretty_math(sp.Eq(factored, 0, evaluate=False))
                        factor_equations = " or ".join(
                            pretty_math(sp.Eq(factor, 0, evaluate=False))
                            for factor in linear_factors
                        )
                        steps.append(f"2. Factor the quadratic:\n   {factored_equation}")
                        steps.append(f"3. Use the zero-product rule, so one factor must be zero:\n   {factor_equations}")
                        steps.append(f"4. Solve each simple equation:\n   {answer}")
                    else:
                        coefficient_a, coefficient_b, coefficient_c = polynomial.all_coeffs()
                        generic_formula = (
                            f"{target} = (-b ± sqrt(b^2 - 4ac)) / (2a)"
                        )
                        substitutions = (
                            f"a = {sp.sstr(coefficient_a)}, "
                            f"b = {sp.sstr(coefficient_b)}, "
                            f"c = {sp.sstr(coefficient_c)}"
                        )
                        steps.append(f"2. Use the quadratic formula (the ± gives both roots):\n   {generic_formula}")
                        steps.append(f"3. Substitute the coefficients: {substitutions}.")
                        steps.append(f"4. Simplify to get:\n   {answer}")
                else:
                    steps.append(f"2. Solve the rearranged equation for {target}:\n   {answer}")
                explanation = "\n\nHow:\n" + "\n".join(steps)
            except (sp.PolynomialError, TypeError, ValueError):
                pass
            return f"Formula:\n{formula}\nAnswer:\n{answer}{decimal_text}{explanation}"

        value = parse_side(expr)
        simplified = sp.simplify(value)
        formula = pretty_math(value)
        answer = pretty_math(simplified)
        if simplified.free_symbols:
            return f"Formula:\n{formula}\nSimplified:\n{answer}"
        decimal = sp.N(simplified, 12)
        if simplified.is_real and simplified.is_number:
            return f"Formula:\n{formula}\nExact answer:\n{answer}\nDecimal: {decimal}"
        return f"Formula:\n{formula}\nAnswer:\n{answer}"
    except (SyntaxError, TypeError, ValueError, ZeroDivisionError, sp.SympifyError):
        return "I could not solve that formula. Check the symbols, parentheses, and operators, then try again."
    except Exception as exc:
        print(f"[math error: {exc}]")
        return "I could not solve that formula. Try rewriting it with parentheses around each side."


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
        self.reminders_lock = threading.Lock()
        self.reminder_stop = threading.Event()
        self.reminders = self.load_reminders()
        self.reminder_voice = None
        self.reminder_callback = None
        self.osm_lock = threading.Lock()
        self.osm_last_request = 0.0
        self.geocode_last_request = 0.0
        self.osm_cooldown_until = 0.0
        self.place_cache = {}
        self.geocode_cache = {}
        self.osm_agent = "Ciel-Assistant/3.0 (local personal assistant; OpenStreetMap nearby search)"
        self.reminder_thread = threading.Thread(
            target=self.reminder_loop,
            daemon=True,
        )
        self.reminder_thread.start()

    @property
    def api_key(self):
        key = os.environ.get(AI_KEY_ENV[self.provider], "").strip()
        if key:
            return key
        if requests is None:
            return ""
        try:
            import keyring
            return keyring.get_password("Ciel", f"{self.provider}_api_key") or ""
        except Exception:
            return ""

    @property
    def provider(self):
        configured = os.environ.get("CIEL_PROVIDER", "").strip().lower()
        if configured in AI_PROVIDERS:
            return configured
        try:
            import keyring
            stored = keyring.get_password("Ciel", "active_provider") or ""
            if stored.lower() in AI_PROVIDERS:
                return stored.lower()
        except Exception:
            pass
        if os.environ.get("ANTHROPIC_API_KEY", "").strip():
            return "anthropic"
        if os.environ.get("GEMINI_API_KEY", "").strip():
            return "gemini"
        if os.environ.get("GROQ_API_KEY", "").strip():
            return "groq"
        return "anthropic"

    def save_api_key(self, key):
        self.save_provider_key("anthropic", key)

    def save_provider_key(self, provider, key):
        provider = provider.strip().lower()
        if provider not in AI_PROVIDERS:
            raise ValueError("Unsupported AI provider.")
        import keyring
        keyring.set_password("Ciel", f"{provider}_api_key", key.strip())
        keyring.set_password("Ciel", "active_provider", provider)

    def select_provider(self, provider):
        provider = provider.strip().lower()
        if provider not in AI_PROVIDERS:
            raise ValueError("Unsupported AI provider.")
        import keyring
        keyring.set_password("Ciel", "active_provider", provider)

    def load_reminders(self):
        try:
            reminders = json.loads(REMINDERS_FILE.read_text(encoding="utf-8"))
            return [
                item for item in reminders
                if isinstance(item, dict)
                and item.get("due")
                and item.get("message")
            ]
        except (OSError, ValueError, TypeError):
            return []

    def save_reminders(self):
        REMINDERS_FILE.parent.mkdir(parents=True, exist_ok=True)
        REMINDERS_FILE.write_text(
            json.dumps(self.reminders, indent=2),
            encoding="utf-8",
        )

    def schedule_reminder(self, due, message, voice=None):
        if due <= dt.datetime.now():
            return "That reminder time has already passed, Master. Please choose a future time."
        is_alarm = message.strip().lower() == "your alarm"
        reminder = {
            "due": due.isoformat(),
            "message": "Alarm" if is_alarm else message.strip() or "Reminder",
            "kind": "alarm" if is_alarm else "reminder",
        }
        with self.reminders_lock:
            self.reminders.append(reminder)
            try:
                self.save_reminders()
            except OSError:
                self.reminders.pop()
                return "I could not save that reminder. Check that your home folder is writable."
        if voice:
            self.reminder_voice = voice
        clock = due.strftime('%I:%M %p').lstrip('0')
        if is_alarm:
            return f"Understood. Alarm set for {clock} on {due:%B %d}."
        return f"Understood. I will remind you to {reminder['message']} at {clock} on {due:%B %d}."

    def list_reminders(self):
        with self.reminders_lock:
            reminders = sorted(
                self.reminders,
                key=lambda item: item.get("due", ""),
            )
        if not reminders:
            return "You have no upcoming reminders, Master."
        lines = []
        for index, reminder in enumerate(reminders[:8], 1):
            try:
                due = dt.datetime.fromisoformat(reminder["due"])
                when = due.strftime("%a %b %d at %I:%M %p").lstrip("0")
            except (KeyError, TypeError, ValueError):
                when = "time unknown"
            lines.append(f"{index}. {reminder['message']} — {when}")
        extra = len(reminders) - len(lines)
        if extra:
            lines.append(f"And {extra} more.")
        return "Upcoming reminders: " + "; ".join(lines)

    def cancel_reminder(self, query):
        query = re.sub(r"\s+", " ", query).strip().casefold()
        if query in {"all", "all reminders", "everything"}:
            with self.reminders_lock:
                previous = self.reminders[:]
                self.reminders.clear()
                try:
                    self.save_reminders()
                except OSError:
                    self.reminders = previous
                    return "I could not update the saved reminders, Master."
            return "All upcoming reminders have been cancelled."

        with self.reminders_lock:
            matches = [
                item for item in self.reminders
                if query and query in item.get("message", "").casefold()
            ]
            if not matches:
                return "I could not find a matching reminder. Say 'list reminders' to check what is scheduled."
            if len(matches) > 1:
                return "I found more than one matching reminder. Please include more of its description."
            reminder = matches[0]
            self.reminders.remove(reminder)
            try:
                self.save_reminders()
            except OSError:
                self.reminders.append(reminder)
                return "I could not update the saved reminders, Master."
        return f"Cancelled the reminder: {reminder['message']}."

    def reminder_loop(self):
        while not self.reminder_stop.wait(1):
            now = dt.datetime.now()
            due_reminders = []
            with self.reminders_lock:
                pending = []
                for reminder in self.reminders:
                    try:
                        due = dt.datetime.fromisoformat(reminder["due"])
                    except (KeyError, TypeError, ValueError):
                        continue
                    if due <= now:
                        due_reminders.append(reminder)
                    else:
                        pending.append(reminder)
                if due_reminders:
                    self.reminders = pending
                    try:
                        self.save_reminders()
                    except OSError as exc:
                        print(f"[reminder save error: {exc}]")
            for reminder in due_reminders:
                message = (
                    "Alarm. Master, your alarm is due."
                    if reminder.get("kind") == "alarm"
                    else f"Reminder. {reminder['message']}"
                )
                print(f"Ciel: {message}")
                if self.reminder_callback:
                    self.reminder_callback(message)
                if self.reminder_voice:
                    self.reminder_voice.say(message)

    def stop_reminders(self):
        self.reminder_stop.set()

    def find_nearby(self, category, area):
        if requests is None:
            return "Nearby search needs the requests package. Install the project requirements first."

        category = re.sub(r"\s+", " ", category).strip().casefold()
        area = re.sub(r"\s+", " ", area).strip()
        category_tags = {
            "cafe": ("amenity", "cafe", "cafes"),
            "cafes": ("amenity", "cafe", "cafes"),
            "coffee": ("amenity", "cafe", "cafes"),
            "coffee shops": ("amenity", "cafe", "cafes"),
            "restaurant": ("amenity", "restaurant", "restaurants"),
            "restaurants": ("amenity", "restaurant", "restaurants"),
            "food": ("amenity", "restaurant", "restaurants"),
            "pharmacy": ("amenity", "pharmacy", "pharmacies"),
            "pharmacies": ("amenity", "pharmacy", "pharmacies"),
            "hospital": ("amenity", "hospital", "hospitals"),
            "hospitals": ("amenity", "hospital", "hospitals"),
            "gas station": ("amenity", "fuel", "gas stations"),
            "gas stations": ("amenity", "fuel", "gas stations"),
            "fuel": ("amenity", "fuel", "gas stations"),
            "supermarket": ("shop", "supermarket", "supermarkets"),
            "supermarkets": ("shop", "supermarket", "supermarkets"),
            "grocery store": ("shop", "supermarket", "supermarkets"),
            "grocery stores": ("shop", "supermarket", "supermarkets"),
            "park": ("leisure", "park", "parks"),
            "parks": ("leisure", "park", "parks"),
            "library": ("amenity", "library", "libraries"),
            "libraries": ("amenity", "library", "libraries"),
            "hotel": ("tourism", "hotel", "hotels"),
            "hotels": ("tourism", "hotel", "hotels"),
            "atm": ("amenity", "atm", "ATMs"),
            "atms": ("amenity", "atm", "ATMs"),
            "bank": ("amenity", "bank", "banks"),
            "banks": ("amenity", "bank", "banks"),
        }
        mapped = category_tags.get(category)
        if not mapped:
            return (
                "I can search for cafes, restaurants, pharmacies, hospitals, gas stations, "
                "supermarkets, parks, libraries, hotels, ATMs, and banks. Try 'find cafes near Bacolod'."
            )
        if not area or len(area) > 120:
            return "Please name a city or area, for example 'find cafes near Bacolod'."

        tag, value, display_name = mapped
        key = (mapped[0], mapped[1], area.casefold())
        now = time.monotonic()
        cached = self.place_cache.get(key)
        if cached and now - cached[0] < 3600:
            elements = cached[1]
            cache_note = " (cached result)"
        else:
            with self.osm_lock:
                now = time.monotonic()
                if now < self.osm_cooldown_until:
                    wait = max(1, int(self.osm_cooldown_until - now))
                    return f"The map service asked us to pause. Please try again in about {wait} seconds."
                with overpass_request_lock() as last_shared_request:
                    overpass_wait = 900 - (time.time() - last_shared_request)
                if last_shared_request and overpass_wait > 0:
                    minutes = max(1, int((overpass_wait + 59) // 60))
                    return f"To respect the shared map service, I can make another new area search in about {minutes} minutes. I’ll reuse cached results in the meantime."
                cached_location = self.geocode_cache.get(area.casefold())
                if cached_location and now - cached_location[0] < 21600:
                    location = cached_location[1]
                else:
                    wait = 1.1 - (now - self.geocode_last_request)
                    if wait > 0:
                        time.sleep(wait)
                    try:
                        self.geocode_last_request = time.monotonic()
                        geo_response = requests.get(
                            NOMINATIM_URL,
                            params={"q": area, "format": "jsonv2", "limit": 1},
                            headers={"User-Agent": self.osm_agent},
                            timeout=12,
                        )
                        if geo_response.status_code in {406, 429}:
                            self.osm_cooldown_until = time.monotonic() + 30
                            return "The map service is busy. I’ll pause before another search; please try again in 30 seconds."
                        geo_response.raise_for_status()
                        results = geo_response.json()
                        if not results:
                            return f"I couldn’t find the area '{area}'. Try adding its city or country."
                        location = (float(results[0]["lat"]), float(results[0]["lon"]))
                        self.geocode_cache[area.casefold()] = (time.monotonic(), location)
                    except Exception as exc:
                        print(f"[place geocoding error: {exc}]")
                        return "I couldn’t look up that area right now. Please try again later."

                lat, lon = location
                query = (
                    f"[out:json][timeout:20];"
                    f"(nwr(around:2500,{lat:.6f},{lon:.6f})[{tag}=\"{value}\"];);"
                    "out center tags 12;"
                )
                try:
                    with overpass_request_lock() as last_shared_request:
                        overpass_wait = 900 - (time.time() - last_shared_request)
                        if last_shared_request and overpass_wait > 0:
                            minutes = max(1, int((overpass_wait + 59) // 60))
                            return f"To respect the shared map service, I can make another new area search in about {minutes} minutes. I’ll reuse cached results in the meantime."
                        self.osm_last_request = time.monotonic()
                        OVERPASS_RATE_FILE.write_text(
                            json.dumps({"last_request": time.time()}),
                            encoding="utf-8",
                        )
                        response = requests.post(
                            OVERPASS_URL,
                            data={"data": query},
                            headers={
                                "User-Agent": self.osm_agent,
                                "Content-Type": "application/x-www-form-urlencoded; charset=utf-8",
                            },
                            timeout=30,
                        )
                    if response.status_code in {406, 429}:
                        self.osm_cooldown_until = time.monotonic() + 900
                        return "The map service is busy. To respect its limits, please wait at least 15 minutes before another uncached search."
                    response.raise_for_status()
                    elements = response.json().get("elements", [])
                    self.place_cache[key] = (time.monotonic(), elements)
                    cache_note = ""
                except Exception as exc:
                    print(f"[nearby search error: {exc}]")
                    return "The nearby-places service is unavailable right now. Please try again later."

        names = []
        for element in elements:
            tags = element.get("tags", {})
            name = tags.get("name") or tags.get("brand") or tags.get("operator")
            if not name:
                continue
            names.append(name)
        names = list(dict.fromkeys(names))[:8]
        if not names:
            return f"I couldn’t find any mapped {display_name} near {area}. Try another nearby city or area.{cache_note}"
        return f"{display_name.capitalize()} near {area}: " + "; ".join(names) + f".{cache_note}"

    def clear_place_cache(self):
        self.place_cache.clear()
        self.geocode_cache.clear()
        return "Cleared cached nearby-place results."

    def claude(self, messages, system, max_tokens=700, timeout=45):
        key = self.api_key
        if not key:
            return None

        try:
            provider = self.provider
            if provider == "anthropic":
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
                blocks = response.json().get("content", [])
                return "".join(
                    block.get("text", "")
                    for block in blocks
                    if isinstance(block, dict)
                ).strip() or None

            if provider == "gemini":
                contents = [
                    {
                        "role": "model" if message.get("role") == "assistant" else "user",
                        "parts": [{"text": message.get("content", "")}],
                    }
                    for message in messages
                ]
                response = requests.post(
                    "https://generativelanguage.googleapis.com/v1beta/models/"
                    + os.environ.get("CIEL_GEMINI_MODEL", "gemini-2.5-flash")
                    + ":generateContent",
                    headers={"x-goog-api-key": key, "content-type": "application/json"},
                    json={
                        "systemInstruction": {"parts": [{"text": system}]},
                        "contents": contents,
                        "generationConfig": {"maxOutputTokens": max_tokens},
                    },
                    timeout=timeout,
                )
                response.raise_for_status()
                candidates = response.json().get("candidates", [])
                parts = candidates[0].get("content", {}).get("parts", []) if candidates else []
                return "".join(part.get("text", "") for part in parts if isinstance(part, dict)).strip() or None

            groq_messages = [{"role": "system", "content": system}]
            groq_messages.extend(messages)
            response = requests.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {key}", "content-type": "application/json"},
                json={
                    "model": os.environ.get("CIEL_GROQ_MODEL", "llama-3.3-70b-versatile"),
                    "max_tokens": max_tokens,
                    "messages": groq_messages,
                },
                timeout=timeout,
            )
            response.raise_for_status()
            choices = response.json().get("choices", [])
            return choices[0].get("message", {}).get("content", "").strip() if choices else None
        except Exception as exc:
            print(f"[API error: {exc}]")
            return None

    def ask_brain(self, question):
        if self.api_key:
            self.history.append({"role": "user", "content": question})
            self.history = self.history[-8:]
            reply = self.claude(self.history, SYSTEM_PROMPT, 1000)
            if reply:
                self.history.append({"role": "assistant", "content": reply})
                return reply
            self.history.pop()

        result = self.web_answer(question)
        if result:
            return result

        if re.search(r"\b(hi|hello|hey|how are you|thank you|thanks)\b", question, re.I):
            return "I am here and ready to talk. What is on your mind?"
        if re.search(r"\b(sad|lonely|stressed|upset|anxious|rough day)\b", question, re.I):
            return (
                "I am sorry things feel difficult right now. I can listen; "
                "what has been weighing on you?"
            )
        return (
            "I can handle reminders, apps, music, and quick lookups offline. "
            "For open-ended conversation and ChatGPT-style answers, configure "
            "an Anthropic API key with the button in the sidebar."
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
        target = name.strip().rstrip(".,!?")
        normalized = target.casefold()
        if normalized in SITES:
            webbrowser.open(SITES[normalized])
            return f"Understood. Opening {normalized}."

        folders = {
            "home": HOME,
            "home folder": HOME,
            "desktop": HOME / "Desktop",
            "downloads": HOME / "Downloads",
            "documents": HOME / "Documents",
            "pictures": HOME / "Pictures",
            "screenshots": HOME / "Pictures" / "Screenshots",
            "notes file": NOTES_FILE,
            "writings": WRITINGS_DIR,
            "writings folder": WRITINGS_DIR,
        }
        folder = folders.get(normalized)
        if folder:
            if open_path(folder):
                return f"Understood. Opening your {normalized}."
            return f"I could not open your {normalized} folder. It may not exist yet."

        scheme_match = re.match(r"^([a-z][a-z0-9+.-]*):", target, re.I)
        if scheme_match and scheme_match.group(1).lower() not in {"http", "https"}:
            return "I can only open websites that use http or https, Master."

        looks_like_url = bool(
            scheme_match
            or normalized.startswith("www.")
            or "." in target.split("/", 1)[0]
        )
        if looks_like_url:
            url = self.website_url(target)
            if not url:
                return "That does not look like a valid website address, Master."
            webbrowser.open(url)
            return f"Understood. Opening {target}."

        system = platform.system()
        app = APPS.get(system, {}).get(normalized)

        if app:
            try:
                if system == "Windows":
                    os.startfile(app)  # type: ignore[attr-defined]
                elif system == "Darwin":
                    subprocess.Popen(["open", "-a", app])
                else:
                    subprocess.Popen([app])
                return f"Understood. Opening {normalized}."
            except Exception:
                pass

        if re.fullmatch(r"[a-z0-9][a-z0-9-]*", normalized):
            url = self.website_url(normalized + ".com")
            if url:
                webbrowser.open(url)
                return f"Understood. Opening {normalized}."

        webbrowser.open(
            "https://www.google.com/search?q=" + quote_plus(target)
        )
        return f"I could not find {target}, so I searched the web instead."

    @staticmethod
    def website_url(target):
        if not target or re.search(r"[\s\\]", target):
            return None
        candidate = target if re.match(r"^https?://", target, re.I) else "https://" + target
        try:
            parsed = urlsplit(candidate)
            hostname = parsed.hostname
            parsed.port
        except ValueError:
            return None
        if (
            parsed.scheme.lower() not in {"http", "https"}
            or not hostname
            or parsed.username
            or parsed.password
        ):
            return None
        try:
            ascii_host = hostname.encode("idna").decode("ascii")
        except UnicodeError:
            return None
        if ascii_host.lower() != "localhost":
            labels = ascii_host.rstrip(".").split(".")
            if len(labels) < 2 or any(
                not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label, re.I)
                for label in labels
            ):
                return None
        return quote(candidate, safe=":/?#[]@!$&'()*+,;=%")

    def play_youtube(self, query):
        webbrowser.open(
            "https://www.youtube.com/results?search_query=" + quote_plus(query)
        )
        return f"Understood. Searching YouTube for {query}."

    def play_music(self, query="", provider="youtube"):
        query = query.strip() or "music"
        if provider == "spotify":
            webbrowser.open(
                "https://open.spotify.com/search/" + quote_plus(query)
            )
            return f"Understood. Searching Spotify for {query}."
        webbrowser.open(
            "https://music.youtube.com/search?q=" + quote_plus(query)
        )
        return f"Understood. Searching YouTube Music for {query}."

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

    def parse_reminder_time(self, text):
        duration = self.parse_duration(text)
        if duration:
            return dt.datetime.now() + dt.timedelta(seconds=duration)

        match = re.search(
            r"\b(?:at|for)\s+(\d{1,2})(?::(\d{2}))?\s*(a\.m\.|p\.m\.|am|pm)?\b",
            text,
            re.I,
        )
        if not match:
            return None
        hour = int(match.group(1))
        minute = int(match.group(2) or 0)
        meridiem = (match.group(3) or "").replace(".", "").lower()
        if minute > 59 or hour > 12 or hour == 0:
            return None
        if meridiem:
            hour = hour % 12 + (12 if meridiem == "pm" else 0)
        elif hour < 12:
            now = dt.datetime.now()
            candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if candidate <= now:
                hour += 12
        due = dt.datetime.now().replace(
            hour=hour,
            minute=minute,
            second=0,
            microsecond=0,
        )
        if "tomorrow" in text or due <= dt.datetime.now():
            due += dt.timedelta(days=1)
        return due

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
        if voice:
            self.reminder_voice = voice

        if low in {
            "goodbye",
            "go to sleep",
            "shut down ciel",
            "exit ciel",
            "quit",
        }:
            return "__STOP__"

        greeting = re.fullmatch(r"(hi|hello|hey)(?: there)?[!. ]*", low)
        if greeting:
            return f"{greeting.group(1).capitalize()}! Good to see you. What is on your mind?"

        if "who are you" in low or "your name" in low:
            return (
                "I am Ciel, your assistant. I analyze, search, "
                "and carry out tasks on your command, Master."
            )

        if re.fullmatch(
            r"(?:what can you do|what else can you do|help|show help|list commands|"
            r"what can i ask you)(?:\?)?",
            low,
        ):
            return (
                "I can chat and answer questions, search the web, open websites, apps and folders, "
                "play music, set timers and reminders, manage reminders, take notes, check the "
                "weather, calculate, inspect system status, diagnose network or laptop issues, "
                "solve equations, simplify algebra, and calculate exact or decimal results. "
                "I can also create poems, stories, letters, emails, essays, resumes, and reports. "
                "With a Gemini, Groq, or Anthropic key, I can answer open-ended questions. "
                "I can find mapped cafes, restaurants, pharmacies, parks, and more by area. "
                "Try 'solve x^2 - 5x + 6 = 0 for x', 'find cafes near Bacolod', or 'write a poem about the sea'."
            )

        provider_match = re.fullmatch(
            r"(?:use|switch to|select)\s+(anthropic|claude|gemini|google gemini|groq)(?:\s+for chat)?",
            low,
        )
        if provider_match:
            provider = provider_match.group(1)
            provider = "anthropic" if provider in {"claude", "anthropic"} else (
                "gemini" if provider in {"gemini", "google gemini"} else "groq"
            )
            try:
                self.select_provider(provider)
            except Exception:
                return "I could not switch the saved AI provider. Set its key in the local conversation settings first."
            return f"Using {provider.capitalize()} for chat."

        nearby_match = re.fullmatch(
            r"(?:find|search for|show me|locate|where can i find)\s+(?:some\s+|a\s+|an\s+)?(.+?)\s+(?:near|around|in)\s+(.+)",
            low,
        ) or re.fullmatch(r"nearby\s+(.+?)\s+in\s+(.+)", low)
        if nearby_match:
            return self.find_nearby(nearby_match.group(1), nearby_match.group(2))

        # Writing
        if re.match(
            r"(?:help me )?(write|compose|make|create|draft|generate|craft|prepare|give|come up with)\b",
            low,
        ) and re.search(
            r"\b(poem|poetry|haiku|sonnet|limerick|story|essay|letter|cover letter|"
            r"speech|song|lyrics|caption|email|paragraph|script|slogan|message|toast|prayer|"
            r"resume|cv|report|article|blog post|proposal|business plan|project plan|"
            r"recipe|outline|bio|review|lesson plan|study guide|presentation|invitation|"
            r"apology|thank-you note|press release|product description|job description)\b",
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
            r"i have an? (problem|issue))\b",
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

        if re.fullmatch(r"(?:clear|reset)(?: the)? (?:map|place|nearby)(?: search)? cache", low):
            return self.clear_place_cache()

        nearby_match = re.fullmatch(
            r"(?:find|search for|show me|locate|where can i find)\s+(?:some\s+|a\s+|an\s+)?(.+?)\s+(?:near|around|in)\s+(.+)",
            c.strip().rstrip("?.!"),
            re.I,
        ) or re.fullmatch(
            r"nearby\s+(.+?)\s+in\s+(.+)",
            c.strip().rstrip("?.!"),
            re.I,
        )
        if nearby_match:
            category = re.sub(r"\b(nearby|close by)\b", "", nearby_match.group(1), flags=re.I).strip()
            area = nearby_match.group(2).strip()
            return self.find_nearby(category, area)

        # Open apps/sites
        if low.startswith(("open ", "launch ", "start ")):
            return self.open_target(low.split(" ", 1)[1])

        # Music playback opens a searchable streaming page in the browser.
        music_match = re.match(
            r"(?:play|put on|start)\s+(.*?)(?:\s+on\s+(spotify|youtube music))?$",
            low,
        )
        if music_match and re.search(r"\b(music|song|songs|playlist|spotify|lo-?fi)\b", low):
            query = re.sub(r"\b(music|songs?|playlist)\b", "", music_match.group(1)).strip()
            provider = "spotify" if music_match.group(2) == "spotify" else "youtube"
            return self.play_music(query, provider)

        # YouTube
        match = re.match(r"(?:play|watch)\s+(.+?)(?:\s+on youtube)?$", low)
        if match:
            return self.play_youtube(match.group(1))

        # Reminders and clock-time alarms
        if re.fullmatch(r"(?:list|show|read)(?: my| upcoming)? reminders?\??", low):
            return self.list_reminders()

        if re.fullmatch(r"(?:cancel|delete|remove) all reminders?", low):
            return self.cancel_reminder("all")

        cancel_match = re.match(
            r"(?:cancel|delete|remove)(?: the| my)? reminder\s*(.*)$",
            low,
        )
        if cancel_match:
            return self.cancel_reminder(cancel_match.group(1))

        if re.search(r"\b(alarm|remind(?:er)?)\b", low) and (
            "timer" not in low or "alarm" in low or "remind" in low
        ):
            due = self.parse_reminder_time(low)
            if due:
                message_match = re.search(r"\b(?:to|that)\s+(.+)$", low)
                reminder_text = message_match.group(1) if message_match else (
                    "your alarm" if "alarm" in low else "Reminder"
                )
                reminder_text = re.sub(
                    r"\s+(?:at|in)\s+\d{1,2}(?::\d{2})?\s*(?:a\.m\.|p\.m\.|am|pm)?$",
                    "",
                    reminder_text,
                    flags=re.I,
                ).strip()
                return self.schedule_reminder(due, reminder_text, voice)
            return (
                "Please give me a time, such as 'remind me in 20 minutes to take a break' "
                "or 'set an alarm for 7:30 AM'."
            )

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
            r"(?:solve|simplify|calculate|compute|evaluate|what is|what's|how much is)\s+(.+)$",
            low,
        )
        if match:
            result = calculate(match.group(1))
            if result:
                return result
        elif re.fullmatch(r"[a-z0-9_+\-*/^%=().,\s]+", low) and re.search(r"\d|=", low):
            result = calculate(low)
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
        self.engine.reminder_callback = lambda text: self.events.put(("reminder", text))

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
            self.listening = True
            self.update_mic_button()
            self.start_voice_thread()
        else:
            self.update_mic_button()
            self.set_status("TEXT MODE", "text")

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
        self.quick_button(sidebar, "♫  Play music", "Play music")
        self.quick_button(sidebar, "◷  Set reminder", "Remind me in 20 minutes to take a break")
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

        tk.Button(
            sidebar,
            text="Configure chat API",
            command=self.configure_api_key,
            font=("Segoe UI", 9, "bold"),
            fg=self.BG,
            bg=self.ACCENT_2,
            activebackground=self.ACCENT,
            activeforeground=self.TEXT,
            relief="flat",
            bd=0,
            cursor="hand2",
            padx=12,
            pady=9,
        ).pack(fill="x", padx=18, pady=(6, 0))

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
            text="MIC OFF",
            command=self.toggle_listening,
            font=("Segoe UI", 9, "bold"),
            fg=self.MUTED,
            bg=self.PANEL,
            activebackground=self.PANEL_2,
            activeforeground=self.TEXT,
            relief="flat",
            bd=0,
            cursor="hand2",
            width=9,
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
            "muted": "MICROPHONE OFF",
            "listening": "LISTENING",
            "thinking": "THINKING",
            "speaking": "SPEAKING",
            "error": "SYSTEM NOTICE",
            "text": "TEXT MODE",
        }
        details = {
            "idle": "Say 'Ciel' to wake me." if self.listening else "Type a command below.",
            "muted": "Voice input is paused. Type a command or turn the mic on.",
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
        elif state == "thinking":
            self.status_title.config(fg=self.ACCENT)
        elif state == "speaking":
            self.status_title.config(fg=self.ACCENT)
        else:
            self.status_title.config(fg=self.TEXT)

    def update_mic_button(self):
        if self.listening:
            self.mic_button.config(
                text="MIC ON",
                fg=self.BG,
                bg=self.ACCENT_2,
                activebackground=self.ACCENT,
            )
        else:
            self.mic_button.config(
                text="MIC OFF",
                fg=self.MUTED,
                bg=self.PANEL,
                activebackground=self.PANEL_2,
            )

    def configure_api_key(self):
        try:
            import keyring
        except ImportError:
            messagebox.showerror(
                "API setup unavailable",
                "Install the project requirements first, then restart Ciel.",
                parent=self.root,
            )
            return

        webbrowser.open("https://console.anthropic.com/settings/keys")
        key = simpledialog.askstring(
            "Configure chat API",
            "Create an Anthropic API key, then paste it here. It will be stored in your OS credential store.",
            show="*",
            parent=self.root,
        )
        if not key:
            return
        try:
            self.engine.save_api_key(key)
        except Exception as exc:
            messagebox.showerror(
                "Could not save API key",
                f"Your credential store rejected the key: {exc}\n\n"
                "You can instead set ANTHROPIC_API_KEY in your environment.",
                parent=self.root,
            )
            return

        self.side_api.config(
            text="●  SMART BRAIN: AVAILABLE",
            fg=self.ACCENT_2,
        )
        messagebox.showinfo(
            "Chat API ready",
            "Ciel can now answer open-ended questions and hold a conversation. "
            "Anthropic API usage may incur charges.",
            parent=self.root,
        )

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
                    if value == "listening":
                        self.set_status("listening", "listening")
                    elif value == "speaking":
                        self.set_status("speaking", "speaking")
                    elif value == "idle" and not self.listening:
                        self.set_status("ready", "idle")

                elif kind == "voice_stopped":
                    if self.listening:
                        self.root.after(100, self.start_voice_thread)
                    else:
                        self.update_mic_button()
                        self.set_status("Microphone off", "muted")

                elif kind == "reminder":
                    self.add_message("Ciel", value, "ciel")

                elif kind == "user_voice":
                    self.add_message("You", value, "user")
                    self.run_command(value)

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
            self.listening = False
            self.events.put((
                "response",
                "Voice mode is unavailable. Install the requirements with: "
                "python -m pip install -r requirements.txt"
            ))
            self.events.put(("voice_stopped", None))
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
            self.listening = False
            self.events.put((
                "response",
                "Report. I could not access the microphone. Check Windows "
                "microphone permissions and your default input device."
            ))
            print(f"[microphone error] {exc}")
            self.events.put(("voice_stopped", None))
            return

        self.events.put(("state", "listening"))

        while self.running and self.listening:
            try:
                audio = record(4.0)
                if not self.listening:
                    break

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

                if not self.listening:
                    break

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
                        "state",
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

                if not self.listening:
                    break
                if rest:
                    self.events.put(("user_voice", rest))

            except Exception as exc:
                print(f"[voice loop] {exc}")
                time.sleep(0.5)

        self.events.put(("voice_stopped", None))

    def toggle_listening(self):
        if sr is None or sd is None or np is None:
            messagebox.showinfo(
                "Voice unavailable",
                "Voice mode needs SpeechRecognition, SoundDevice, and NumPy.\n\n"
                "Run: python -m pip install -r requirements.txt"
            )
            return

        self.listening = not self.listening
        self.update_mic_button()
        if self.listening:
            self.start_voice_thread()
            self.set_status("listening", "listening")
            self.add_message("Ciel", "Microphone on. Say 'Ciel' before your command.")
        else:
            try:
                sd.stop()
            except Exception:
                pass
            self.set_status("Microphone off", "muted")
            self.add_message("Ciel", "Microphone off.")

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
        self.listening = False
        self.engine.stop_reminders()
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
