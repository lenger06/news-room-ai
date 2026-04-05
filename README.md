# Newsroom AI

An AI-powered broadcast newsroom that researches topics, fact-checks articles, writes news content, produces broadcast anchor scripts, generates AI anchor videos via HeyGen, and publishes to YouTube — all orchestrated by an Executive Producer agent.

Designed to run as a standalone backend service called by [Jarvis](https://github.com/lenger06/jarvis-assistant-ai) or any other client via a simple HTTP API.

---

## Agent Roles

### Executive Producer
The orchestrator. Receives every production request, determines the appropriate workflow, selects an anchor from the roster, and delegates to the team in sequence. Saves a full production log to `./output/production_logs/` at the end of every run.

### Researcher
Gathers source material using real-time web search (Tavily). Searches for multiple angles — latest developments, background context, key figures, and statistics. Compiles a sourced research brief with URLs that the Writer uses as the basis for the article.

### Writer
Receives the research brief and writes a polished news article in broadcast style — inverted pyramid structure, active voice, short sentences, 400–600 words. Includes a branded dateline (e.g. "WASHINGTON — Defy Logic News"). Saves the article to `./output/articles/` in Markdown format.

### Fact Checker
Reads the draft article and verifies key factual claims using web search. Produces a Fact Check Report with three sections — **Verified**, **Unverified**, and **Corrections Needed** — and issues one of three verdicts:
- `CLEAR TO PUBLISH` — all significant claims verified
- `PUBLISH WITH NOTES` — minor unverified items, no outright errors
- `HOLD FOR CORRECTIONS` — factual errors found, must be fixed before publishing

The full report and verdict are passed to all downstream agents as context.

### Script Writer
Converts the verified article into a spoken broadcast anchor script. Formats it for on-air delivery: natural spoken English, breath-pause markers, phonetic pronunciations for difficult names, and `[GRAPHIC: ...]` cues for supporting visuals. Uses the selected anchor's name in the sign-off (e.g. "I'm Alex Morgan, Defy Logic News."). Target read time: 60–90 seconds. Saves the script to `./output/scripts/`.

### Anchor
Takes the broadcast script, cleans it for spoken delivery (removes graphic cues, converts pause markers), and submits it to the HeyGen API using the anchor's specific avatar and voice IDs. Polls for completion natively in Python (every 30 seconds, up to 10 minutes) — does not rely on the LLM to manage polling. Returns the video URL and thumbnail URL when complete.

### Video Editor
Downloads the completed anchor video from HeyGen, extracts all `[GRAPHIC: ...]` cues from the script as production notes, and assembles a `video_package.json` in `./output/media/` containing the video file path, thumbnail URL, graphic cues, and suggested YouTube metadata.

### Producer
Confirms all output files are saved and compiles a final production summary — article path, script path, video path, topic, and word counts.

### Publisher
Reads `video_package.json`, uploads the finished MP4 to YouTube with branded title and description, and sets the HeyGen thumbnail. Uploads exactly once — upload logic runs in native Python, not via the LLM. Returns the final YouTube URL.

---

## Anchor Roster

Anchors are defined in `config/anchors.py`. Each anchor has a name, HeyGen avatar ID, voice ID, and a brief bio that informs the script writer's tone.

To add an anchor, add an entry to the `ANCHORS` list in `config/anchors.py`:

```python
Anchor(
    name="Jordan Lee",
    avatar_id="<avatar_id from GET /v2/avatars>",
    voice_id="<voice_id from GET /v2/voices>",
    bio="Warm and conversational. Strong on human interest stories.",
)
```

To request a specific anchor in a production, name them naturally:
- *"Produce a broadcast video on the Fed rate decision — have Rick Johnson read it"*
- *"Generate a news video with Darlene Smith"*

If no anchor is specified, the Executive Producer picks one at random.

---

## Workflows

The Executive Producer automatically selects the appropriate workflow based on the request:

| Workflow | Trigger phrases | Steps |
|----------|----------------|-------|
| `RESEARCH_ONLY` | "research", "find information about", "what do we know about" | Researcher |
| `ARTICLE` | "write an article", "write a story", "cover this story" | Researcher → Writer → Fact Checker → Producer |
| `FULL_PRODUCTION` | "full production", "produce a segment", "news segment", "broadcast" | Researcher → Writer → Fact Checker → Script Writer → Producer |
| `BROADCAST_VIDEO` | "video", "youtube", "record", "generate video", "publish" | Researcher → Writer → Fact Checker → Script Writer → Anchor → Video Editor → Producer → Publisher |
| `SCRIPT_ONLY` | "script only", "write a script", "turn this into a script" (with content provided) | Script Writer → Producer |
| `VIDEO_FROM_SCRIPT` | "video from script", "record this script", "generate video from script" | Anchor → Video Editor → Producer → Publisher |

Each step receives the full output of all prior steps as context.

---

## Architecture

```
Jarvis (or any HTTP client)
 └─► POST /produce/async
       └─► Executive Producer (orchestrator)
             ├─► Researcher      — web_research_tool, image_search_tool, file_operations_tool
             ├─► Writer          — file_operations_tool
             ├─► Fact Checker    — web_research_tool
             ├─► Script Writer   — file_operations_tool
             ├─► Anchor          — HeyGen API (generate + native async poll)
             ├─► Video Editor    — video_tools (download, extract cues, package)
             ├─► Producer        — file_operations_tool
             └─► Publisher       — YouTube API (upload once + thumbnail)
```

Output files are saved to:
```
output/
  articles/         — finished news articles (.md)
  scripts/          — broadcast anchor scripts (.md)
  media/            — anchor videos (.mp4) and video_package.json
  production_logs/  — full production logs with all agent outputs (.md)
```

---

## Setup

### Requirements

- Python 3.10+
- OpenAI API key
- Tavily API key
- HeyGen API key *(for Anchor agent — video generation)*
- Google Cloud project with YouTube Data API v3 enabled *(for Publisher agent)*

### Installation

```bash
cd news-room-ai
pip install -r requirements.txt
```

### HeyGen Setup

1. Sign up at [heygen.com](https://heygen.com) and get your API key from **Settings → API**
2. Add anchors to `config/anchors.py` with avatar and voice IDs:
   - Call `GET https://api.heygen.com/v2/avatars` with your API key to list available avatars
   - Call `GET https://api.heygen.com/v2/voices` to list voices

### YouTube Setup

1. Go to [Google Cloud Console](https://console.cloud.google.com) → **APIs & Services → Library**
2. Enable **YouTube Data API v3**
3. Go to **Credentials → Create Credentials → OAuth 2.0 Client ID** (Desktop app)
4. Download the JSON file and save it to `credentials/youtube_client_secrets.json`
5. On first run the Publisher agent will open a browser to authorize — token saved to `credentials/youtube_token.pickle`

### Environment Variables

Copy `.env.example` to `.env` and fill in your keys:

```bash
cp .env.example .env
```

```env
NEWSROOM_NAME="Defy Logic News"

OPENAI_API_KEY="sk-..."
TAVILY_API_KEY="tvly-..."

HEYGEN_API_KEY="sk_..."

HOST=0.0.0.0
PORT=8091
DEBUG=True
LOG_LEVEL=INFO

ARTICLES_DIR=./output/articles
SCRIPTS_DIR=./output/scripts
MEDIA_DIR=./output/media
LOGS_DIR=./output/production_logs

YOUTUBE_CLIENT_SECRETS_PATH=credentials/youtube_client_secrets.json
```

---

## Running

```bash
cd news-room-ai
python main.py
```

Server starts at `http://0.0.0.0:8091`.

---

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/` | GET | Service info and available workflows |
| `/health` | GET | Agent status |
| `/produce` | POST | Run a production synchronously (blocks until complete) |
| `/produce/async` | POST | Start a production in the background — returns `job_id` immediately |
| `/job/{job_id}` | GET | Poll for the status and result of an async production job |
| `/produce/stream` | POST | Run a production via SSE (streams status updates) |
| `/docs` | GET | Swagger UI |

### Request format

```json
{
  "request": "Produce a full news segment on the situation in the Strait of Hormuz",
  "client_datetime": "Friday, April 4, 2026, 12:00 PM PDT"
}
```

### Synchronous response format (`/produce`)

```json
{
  "success": true,
  "response": "**Production Complete — FULL_PRODUCTION**\nTopic: ...",
  "workflow": "FULL_PRODUCTION",
  "topic": "Strait of Hormuz shipping situation",
  "agent": "executive_producer"
}
```

### Async response format (`/produce/async`)

```json
{ "job_id": "e4b130b8-a012-4722-9562-388a9ab7aa4b", "status": "started" }
```

### Job status format (`/job/{job_id}`)

```json
{
  "status": "running | complete | error",
  "result": "**Production Complete...**",
  "workflow": "BROADCAST_VIDEO",
  "topic": "Strait of Hormuz",
  "error": null
}
```

### SSE event types (`/produce/stream`)

| Type | Description |
|------|-------------|
| `status` | Progress update (e.g. "Production started...") |
| `result` | Final production summary |
| `done` | Stream complete |
| `error` | Error message |

---

## Calling from Jarvis

Jarvis routes news production requests automatically. Just talk to Jarvis naturally:

- *"Jarvis, produce a full news segment on the Iran situation"*
- *"Jarvis, research the latest on shipping through the Strait of Hormuz"*
- *"Jarvis, generate a news video about the drone strike near Dubai — have Darlene Smith read it"*
- *"Jarvis, schedule a daily news segment at 6am on the latest White House announcements"*

Jarvis detects newsroom intent and forwards the request to the Executive Producer at `http://localhost:8091/produce/async`. Jarvis responds immediately confirming production has started, then notifies you when the video is published. The newsroom backend must be running for this to work.

To call the API directly:

```bash
curl -X POST http://localhost:8091/produce/async \
  -H "Content-Type: application/json" \
  -d '{"request": "Write a news article about the Strait of Hormuz shipping situation"}'
```
