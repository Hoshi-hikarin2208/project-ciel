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

Optional Claude brain

Set ANTHROPIC_API_KEY in your Windows environment, then restart Ciel.

If Windows blocks the microphone

Windows Settings -> Privacy & security -> Microphone -> enable microphone access and allow desktop apps to access the microphone.

Main files

ciel.py — main application

requirements.txt — required Python packages

run_ciel.bat — install dependencies and launch Ciel