# Newsroom AI

An AI-powered broadcast newsroom that researches topics, writes news articles, produces broadcast anchor scripts, and manages final output — all orchestrated by an Executive Producer agent.

Designed to run as a standalone backend service called by [Jarvis](../jarvis-assistant-ai) or any other client via a simple HTTP API.

---

## Agent Roles

### Executive Producer
The orchestrator. Receives every production request, determines the appropriate workflow, and delegates to the team in sequence — passing each agent's output to the next. The EP is the only agent Jarvis talks to directly.

### Researcher
Gathers source material using real-time web search (Tavily). Searches for multiple angles — latest developments, background context, key figures, and statistics. Compiles a sourced research brief with URLs that the Writer uses as the basis for the article.

### Writer
Receives the research brief and writes a polished news article in broadcast style — inverted pyramid structure, active voice, short sentences, 400–600 words. Saves the article to `./output/articles/` in Markdown format.

### Script Writer
Converts the written article into a spoken broadcast anchor script. Formats it for on-air delivery: natural spoken English, breath-pause markers, phonetic pronunciations for difficult names, and `[GRAPHIC: ...]` cues for supporting visuals. Target read time: 60–90 seconds. Saves the script to `./output/scripts/`.

### Anchor
Takes the broadcast script and submits it to the HeyGen API to generate an AI news anchor video. Cleans the script for spoken delivery (removes graphic cues, converts pause markers), submits to HeyGen, polls for completion, and returns the video URL and ID.

### Video Editor
Downloads the completed anchor video from HeyGen, extracts all `[GRAPHIC: ...]` cues from the script as a production notes list, and assembles a `video_package.json` in `./output/media/` containing the video file path, thumbnail URL, graphic cues, and suggested YouTube metadata (title, description, tags).

### Producer
Confirms all output files are saved and compiles a final production summary — article path, script path, video path, topic, and word counts.

### Publisher
Uploads the finished MP4 to YouTube using the video package metadata. Sets the title, description, tags, category, and privacy status (defaults to `unlisted`). Sets the thumbnail from the HeyGen-generated preview image. Returns the final YouTube URL.

---

## Workflows

The Executive Producer automatically selects the appropriate workflow based on the request:

| Workflow | Trigger phrases | Steps |
|----------|----------------|-------|
| `RESEARCH_ONLY` | "research", "find information about", "what do we know about" | Researcher |
| `ARTICLE` | "write an article", "write a story", "cover this story" | Researcher → Writer → Producer |
| `FULL_PRODUCTION` | "full production", "produce a segment", "news segment", "broadcast" | Researcher → Writer → Script Writer → Producer |
| `BROADCAST_VIDEO` | "video", "youtube", "record", "generate video", "publish" | Researcher → Writer → Script Writer → Anchor → Video Editor → Producer → Publisher |
| `SCRIPT_ONLY` | "script only", "write a script", "turn this into a script" (with content provided) | Script Writer → Producer |
| `VIDEO_FROM_SCRIPT` | "video from script", "record this script", "generate video from script" | Anchor → Video Editor → Producer → Publisher |

Each step receives the full output of all prior steps as context, so the Writer always has the research, and the Script Writer always has the article.

---

## Architecture

```
Jarvis (or any HTTP client)
 └─► POST /produce
       └─► Executive Producer (orchestrator)
             ├─► Researcher      — web_research_tool, image_search_tool, file_operations_tool
             ├─► Writer          — file_operations_tool
             ├─► Script Writer   — file_operations_tool
             ├─► Anchor          — heygen_tool (generate + poll)
             ├─► Video Editor    — video_tools (download, extract cues, package)
             ├─► Producer        — file_operations_tool
             └─► Publisher       — youtube_tool (upload + thumbnail)
```

Output files are saved to:
```
output/
  articles/   — finished news articles (.md)
  scripts/    — broadcast anchor scripts (.md)
  media/      — reserved for future media assets
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
2. Call `GET https://api.heygen.com/v2/avatars` to list available avatars and pick one
3. Call `GET https://api.heygen.com/v2/voices` to list voices and pick one
4. Add the IDs to your `.env` as `HEYGEN_AVATAR_ID` and `HEYGEN_VOICE_ID`

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
OPENAI_API_KEY="sk-..."
TAVILY_API_KEY="tvly-..."

HOST=0.0.0.0
PORT=8091
DEBUG=True
LOG_LEVEL=INFO

ARTICLES_DIR=./output/articles
SCRIPTS_DIR=./output/scripts
MEDIA_DIR=./output/media

# YouTube (optional — for producer upload feature)
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
| `/produce` | POST | Run a production (returns when complete) |
| `/produce/stream` | POST | Run a production via SSE (streams status updates) |
| `/docs` | GET | Swagger UI |

### Request format

```json
{
  "request": "Produce a full news segment on the situation in the Strait of Hormuz",
  "client_datetime": "Friday, April 4, 2026, 12:00 PM PDT"
}
```

### Response format

```json
{
  "success": true,
  "response": "**Production Complete — FULL_PRODUCTION**\nTopic: ...\n\n**Researcher:**\n...\n\n**Writer:**\n...",
  "workflow": "FULL_PRODUCTION",
  "topic": "Strait of Hormuz shipping situation",
  "agent": "executive_producer"
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
- *"Jarvis, write a news article about the drone strike near Dubai"*

Jarvis detects newsroom intent and forwards the request to the Executive Producer at `http://localhost:8091/produce`. The newsroom backend must be running for this to work.

To call the API directly:

```bash
curl -X POST http://localhost:8091/produce \
  -H "Content-Type: application/json" \
  -d '{"request": "Write a news article about the Strait of Hormuz shipping situation"}'
```
