# Persona Studio

Chat, search, and create images while roleplaying as anyone — anime characters, real people, or your own originals. Personas stay **in character** instead of sounding like an AI.

## Features

- **In-character chat** — replies as the person/character, not as an assistant
- **Web research** — auto-research personalities, quotes, and backgrounds online
- **Reference photo upload** — upload images so likeness and vibe match who you want
- **Image generation & editing** — create new art or edit photos (real people use edit mode with references)
- **Image search** — find reference images online and download them

## Quick Start

### 1. Install dependencies

```powershell
cd C:\Users\Christian\.grok\bin\persona-studio
pip install -r requirements.txt
```

### 2. Set up API access

**Option A — Already signed into Grok CLI (easiest):**

If you ran `grok` and signed in, the app reads your credentials from `~/.grok/auth.json` automatically.

**Option B — API key:**

```powershell
$env:XAI_API_KEY = "xai-your-key-here"
```

Get a key at [console.x.ai](https://console.x.ai).

### 3. Run the app

**Background (recommended — no CMD window to keep open):**

```powershell
# One-time: auto-start at every Windows login + desktop shortcut + tray icon
install-background.bat

# Or start manually in the background now:
python daemon.py start
# Or double-click start-hidden.vbs (completely silent)
```

Open **http://127.0.0.1:7860** in your browser. A purple **tray icon** lets you Open / Quit.

**Foreground (dev / debugging):**

```powershell
python app.py
```

Or double-click `run.bat` (starts background, then you can close the window).

**Stop:** `python daemon.py stop` or double-click `stop.bat` or tray → Quit.

## How to Use

### Setup a persona

1. Go to **Setup Persona**
2. Enter the character name and pick **anime**, **real**, or **fictional**
3. Fill in appearance, personality, and how they talk (optional but helps a lot)
4. Click **Research Online** to pull info from the web
5. Upload **reference photos** for likeness
6. Click **Create New Persona** or **Save Profile**

### Chat

1. Open **Chat**
2. Load your persona and start talking
3. Enable **Search web for context** if you want live facts woven into replies

### Images

1. Open **Images**
2. Describe what you want and click **Generate**
3. For real people: upload a reference photo first
4. Use **Edit Image** to restyle or change an existing picture
5. Use **Find Images Online** to grab references

## Tips

- **Anime / fictional**: works great with text descriptions; references optional
- **Real people**: upload a clear face photo for best image results
- **Better roleplay**: add speech style notes ("uses slang", "formal", "sarcastic")
- **Stay in character**: the app blocks common AI phrases, but good profile notes help most

## Files

| Path | Purpose |
|------|---------|
| `app.py` | Main UI |
| `persona.py` | Persona profiles & prompts |
| `chat.py` | In-character chat |
| `images.py` | Image gen/edit via xAI |
| `search.py` | Web & image search |
| `data/personas/` | Saved persona JSON files |
| `data/uploads/` | Your uploaded references |
| `data/generated/` | Generated/edited images |

## Requirements

- Python 3.10+
- xAI API access (Grok sign-in or `XAI_API_KEY`)
- Internet connection for search and images