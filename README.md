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

### Producer
The final step. Confirms all output files are saved, compiles a production summary (article path, script path, topic, word counts), and prepares content for distribution. YouTube upload support is stubbed and ready to be wired in.

---

## Workflows

The Executive Producer automatically selects the appropriate workflow based on the request:

| Workflow | Trigger phrases | Steps |
|----------|----------------|-------|
| `RESEARCH_ONLY` | "research", "find information about", "what do we know about" | Researcher |
| `ARTICLE` | "write an article", "write a story", "cover this story" | Researcher → Writer → Producer |
| `FULL_PRODUCTION` | "full production", "produce a segment", "news segment", "broadcast" | Researcher → Writer → Script Writer → Producer |
| `SCRIPT_ONLY` | "write a script", "turn this into a script" (with content provided) | Script Writer → Producer |

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
             └─► Producer        — file_operations_tool
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

### Installation

```bash
cd news-room-ai
pip install -r requirements.txt
```

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
