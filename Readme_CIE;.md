CIEL 3.1

Python 3.14-friendly desktop voice assistant.

Install

Open PowerShell in this folder and run:

python -m pip install --upgrade pip
python -m pip install -r requirements.txt

PyAudio is NOT required by this version. Microphone capture uses sounddevice + NumPy, then SpeechRecognition handles transcription.

Run

python ciel.py

Text-only mode:

python ciel.py --text

Browser companion

Double-click run_ciel_web.bat, or run:

python ciel_web.py

Ciel opens its local conversation page at http://127.0.0.1:8765. Open Conversation
and API settings in the page to choose Anthropic, Gemini, or Groq and save a
provider-specific key. Ciel stores keys in the Windows credential store; prompts
are sent to the selected provider and may incur charges. The web server listens
only on this computer.

Desktop API setup

In the desktop app, click "Configure chat API" to save an Anthropic key to the
Windows credential store. For any provider, environment variables are also
supported: ANTHROPIC_API_KEY, GEMINI_API_KEY, or GROQ_API_KEY, with optional
CIEL_PROVIDER set to anthropic, gemini, or groq. Never share keys; provider API
use may incur charges.

Supported chat providers include Anthropic, Google Gemini, and Groq. Choose a
provider in the browser conversation settings, then paste a newly rotated key;
the active provider and keys are stored locally in the OS credential store.
Chat messages are sent to the selected provider and may be subject to its billing
and data policies. Rotate and revoke any key accidentally pasted into chat.

Nearby OpenStreetMap search

Try "find cafes near Bacolod" or "nearby pharmacies in Cebu City". Ciel uses
public Nominatim and Overpass endpoints with a descriptive User-Agent, sequential
requests, cached lookups, and at most one uncached Overpass search per 15 minutes
(under 100 uncached map queries per day even if you use both desktop and web modes).
The search is best-effort and may be unavailable when public servers are busy.

Writing and explanations

With an API key configured, try "write a poem about the sea", "draft a formal
cover letter for a software internship", "create a study guide about cell
division", or "explain derivatives step by step". Ciel saves generated writing
in the Ciel_Writings folder. For resumes, applications, or letters, include your
real details; Ciel will use placeholders rather than inventing credentials.

Reminders and controls

Try "remind me in 20 minutes to take a break" or "set an alarm for 7:30 PM".
Reminders are stored locally and fire while Ciel is running. For reminders to
fire after Ciel is closed, use Windows Task Scheduler; this app does not install
background Windows services.

Use "open Valorant" to start Riot Client (Valorant must already be installed).
Use "play lo-fi music" or "play jazz on Spotify" to open a music search.
The MIC ON / MIC OFF button controls wake-word listening; say "Ciel" followed
by your command while the microphone is on.

If Windows blocks the microphone

Windows Settings -> Privacy & security -> Microphone -> enable microphone access and allow desktop apps to access the microphone.

Main files

ciel.py — main application

requirements.txt — required Python packages

run_ciel.bat — install dependencies and launch Ciel
