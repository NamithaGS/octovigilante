# HealthCheck AI 🔬

A multi-agent webapp that analyzes health/food/medical video claims and verifies them against top medical journals.

## What it does

1. **URL Validation** – Checks the link is a video (YouTube, Vimeo, etc.) and health-related
2. **Transcript Extraction** – Pulls audio transcript without downloading the video (YouTube Transcript API / Whisper via URL)
3. **Claim Extraction Agent** – Identifies the top 5 health/food/medical claims from the transcript
4. **Research Agents (parallel)** – One agent per claim searches PubMed, WHO, NHS, and other top journals
5. **Deep Research Agent** – A dedicated agent cross-references and synthesizes evidence
6. **Confidence Scoring Agent** – Aggregates all data and assigns a % confidence score to each claim
7. **Rich UI** – Accordion-style claim cards with evidence dropdowns

## Architecture

```
Frontend (HTML/CSS/JS)
        │
        ▼
Flask Backend (app.py)
        │
        ▼
Orchestrator (agents/orchestrator.py)
    ├── ClaimExtractorAgent
    ├── ResearchAgent (spawned per claim, parallel)
    ├── DeepResearchAgent
    └── ConfidenceScorerAgent
```

## Setup

### Prerequisites
- Python 3.10+
- Anthropic API key
- (Optional) YouTube Data API key for better URL validation

### Installation

```bash
pip install -r requirements.txt
```

### Environment Variables

Create a `.env` file:

```
ANTHROPIC_API_KEY=your_key_here
HAPPYSCRIBE_API_KEY=your_happyscribe_key_here  # Optional: for cloud transcription fallback
```

### Run

```bash
python app.py
```

Open http://localhost:5000

## Tech Stack

- **Backend**: Python, Flask
- **AI**: Anthropic Claude (claude-sonnet-4-20250514) via multi-agent orchestration
- **Transcript**: youtube-transcript-api (no video download needed), HappyScribe API (cloud fallback), yt-dlp + Whisper (local fallback)
- **Research**: Claude web_search tool + PubMed API
- **Frontend**: Vanilla HTML/CSS/JS with a polished dark-science UI

## Supported Video Sources
- YouTube (primary, transcript via API)
- Any public video URL (fallback: HappyScribe API, yt-dlp + Whisper for audio-to-text)

## Project Structure

```
healthcheck-ai/
├── app.py                  # Flask entry point
├── requirements.txt
├── .env.example
├── agents/
│   ├── __init__.py
│   ├── orchestrator.py     # Coordinates all agents
│   ├── claim_extractor.py  # Agent 1: extract claims from transcript
│   ├── research_agent.py   # Agent 2-N: research each claim
│   ├── deep_research.py    # Agent: deep cross-reference
│   └── confidence_scorer.py# Final agent: score confidence
├── utils/
│   ├── __init__.py
│   ├── transcript.py       # Video URL parsing + transcript fetching
│   └── pubmed.py           # PubMed API helper
└── frontend/
    ├── templates/
    │   └── index.html
    └── static/
        ├── style.css
        └── app.js
```
