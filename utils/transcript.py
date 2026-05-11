"""
utils/transcript.py

Handles:
- URL validation (is it a video? is it health-related?)
- Transcript extraction (YouTube Transcript API first, yt-dlp+Whisper fallback)
- No full video download required for YouTube URLs
"""

import re
import os
import json
import logging
import shutil
import tempfile
from urllib.parse import urlparse, parse_qs
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

# Health/food/medical topic keywords for relevance check
HEALTH_KEYWORDS = [
    "health", "diet", "nutrition", "food", "vitamin", "supplement", "medicine",
    "medical", "disease", "cancer", "heart", "brain", "gut", "weight", "fat",
    "protein", "carb", "carbohydrate", "sugar", "insulin", "diabetes", "obesity",
    "immune", "inflammation", "antioxidant", "probiotic", "fasting", "keto",
    "vegan", "vegetarian", "organic", "gluten", "cholesterol", "blood pressure",
    "fitness", "exercise", "mental health", "sleep", "stress", "hormone",
    "thyroid", "liver", "kidney", "gut health", "microbiome", "wellness",
    "longevity", "aging", "detox", "cleanse", "superfood", "plant-based",
    "omega", "fiber", "mineral", "calorie", "metabolism", "biohacking",
    "dr.", "doctor", "scientist", "researcher", "study", "clinical", "trial",
    "peer-reviewed", "pubmed", "journal", "evidence", "research","creatine", "intermittent fasting", "time-restricted eating", "mct oil", "bulletproof", "bone broth", "collagen", "resveratrol", "curcumin", "turmeric"
]

# Supported video platforms and their patterns
VIDEO_PATTERNS = {
    "youtube": [
        r"(?:https?://)?(?:www\.)?youtube\.com/watch\?v=([\w-]+)",
        r"(?:https?://)?(?:www\.)?youtu\.be/([\w-]+)",
        r"(?:https?://)?(?:www\.)?youtube\.com/embed/([\w-]+)",
        r"(?:https?://)?(?:www\.)?youtube\.com/shorts/([\w-]+)",
    ],
    "vimeo": [
        r"(?:https?://)?(?:www\.)?vimeo\.com/(\d+)",
    ],
}


def extract_youtube_id(url: str) -> Optional[str]:
    """Extract YouTube video ID from various YouTube URL formats."""
    for pattern in VIDEO_PATTERNS["youtube"]:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    return None


def is_video_url(url: str) -> Tuple[bool, str, Optional[str]]:
    """
    Check if a URL points to a video.

    Returns:
        (is_video, platform, video_id)
    """
    url = url.strip()

    # YouTube
    yt_id = extract_youtube_id(url)
    if yt_id:
        return True, "youtube", yt_id

    # Vimeo
    for pattern in VIDEO_PATTERNS["vimeo"]:
        match = re.search(pattern, url)
        if match:
            return True, "vimeo", match.group(1)

    # Generic video file extensions
    parsed = urlparse(url)
    path = parsed.path.lower()
    if any(path.endswith(ext) for ext in [".mp4", ".mov", ".avi", ".mkv", ".webm", ".m4v"]):
        return True, "direct", None

    return False, "unknown", None


def get_youtube_transcript(video_id: str) -> Tuple[str, dict]:
    """
    Fetch YouTube transcript using youtube-transcript-api.
    No video download required.

    Returns:
        (transcript_text, metadata)
    """
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
        from youtube_transcript_api.formatters import TextFormatter

        # Try to get transcript (English preferred, fallback to auto-generated)
        api = YouTubeTranscriptApi()
        if hasattr(api, "list_transcripts"):
            transcript_list = api.list_transcripts(video_id)
        else:
            transcript_list = api.list(video_id)

        transcript = None
        try:
            # Prefer manually created English transcripts
            transcript = transcript_list.find_manually_created_transcript(["en", "en-US", "en-GB"])
        except Exception:
            try:
                # Fall back to auto-generated
                transcript = transcript_list.find_generated_transcript(["en", "en-US", "en-GB"])
            except Exception:
                # Try any available language and translate
                for t in transcript_list:
                    transcript = t.translate("en")
                    break

        if not transcript:
            raise ValueError("No transcript available for this video")

        fetched = transcript.fetch()
        formatter = TextFormatter()
        text = formatter.format_transcript(fetched)

        # Basic metadata from transcript
        duration_seconds = 0
        if fetched:
            last_item = fetched[-1]
            last_start = getattr(last_item, "start", None)
            last_duration = getattr(last_item, "duration", None)
            if last_start is None or last_duration is None:
                last_start = last_item.get("start") if hasattr(last_item, "get") else None
                last_duration = last_item.get("duration") if hasattr(last_item, "get") else None
            if last_start is not None and last_duration is not None:
                duration_seconds = int(last_start + last_duration)

        metadata = {
            "video_id": video_id,
            "url": f"https://www.youtube.com/watch?v={video_id}",
            "language": getattr(transcript, "language", "en"),
            "is_generated": getattr(transcript, "is_generated", True),
            "duration_seconds": duration_seconds,
            "transcript_source": "youtube_captions",
        }

        return text, metadata

    except ImportError:
        raise RuntimeError("youtube-transcript-api not installed. Run: pip install youtube-transcript-api")
    except Exception as e:
        api_error = str(e)
        try:
            return _get_youtube_transcript_via_audio(video_id, api_error)
        except Exception as fallback_exc:
            raise RuntimeError(
                f"Could not fetch YouTube transcript: {api_error}. "
                f"Audio fallback also failed: {fallback_exc}"
            )


def _ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


def _download_youtube_audio(video_id: str, tmp_dir: str) -> tuple[str, dict]:
    try:
        from yt_dlp import YoutubeDL
    except ImportError:
        raise RuntimeError("yt-dlp not installed. Run: pip install yt-dlp")

    url = f"https://www.youtube.com/watch?v={video_id}"
    output_template = os.path.join(tmp_dir, "audio.%(ext)s")
    ydl_opts = {
        "format": "bestaudio/best",
        "outtmpl": output_template,
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "cachedir": False,
        "nocheckcertificate": True,
        "geo_bypass": True,
        "extractor_args": {"youtube": {"player_client": ["android", "tv_embedded", "web"]}},
        "http_headers": {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": "https://www.youtube.com/",
        },
        "source_address": "0.0.0.0",
    }

    if _ffmpeg_available():
        ydl_opts["postprocessors"] = [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "wav",
            "preferredquality": "192",
        }]
        ydl_opts["prefer_ffmpeg"] = True

    try:
        with YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
    except Exception as e:
        raise RuntimeError(
            "yt-dlp failed to download audio for this video. "
            "This may happen for restricted/geo-blocked content or if YouTube blocks the request. "
            f"Try a different video, or provide a video with captions enabled. Details: {e}"
        )

    if not info:
        raise RuntimeError("yt-dlp could not download audio for this video.")

    if _ffmpeg_available():
        audio_path = os.path.join(tmp_dir, "audio.wav")
    else:
        ext = info.get("ext", "m4a")
        audio_path = os.path.join(tmp_dir, f"audio.{ext}")
        if not os.path.exists(audio_path):
            audio_path = info.get("_filename", audio_path)

    if not os.path.exists(audio_path):
        raise RuntimeError(f"Audio file not found after download: {audio_path}")

    return audio_path, info


def _transcribe_audio_file(audio_path: str) -> tuple[str, dict]:
    # Try HappyScribe API first if key is available
    happyscribe_key = os.environ.get("HAPPYSCRIBE_API_KEY")
    if happyscribe_key:
        try:
            return _transcribe_audio_with_happyscribe(audio_path, happyscribe_key)
        except Exception as e:
            logger.warning(f"HappyScribe transcription failed: {e}. Falling back to local Whisper.")

    # Fallback to local Whisper
    try:
        import whisper
    except ImportError:
        raise RuntimeError("Whisper is not installed. Install it with pip install whisper")

    try:
        try:
            model_name = "tiny.en"
            model = whisper.load_model(model_name)
        except Exception:
            model_name = "tiny"
            model = whisper.load_model(model_name)

        result = model.transcribe(audio_path, language="en", verbose=False)
        text = result.get("text", "") if isinstance(result, dict) else str(result)
        return text.strip(), {"transcription_model": model_name}
    except Exception as e:
        raise RuntimeError(f"Whisper transcription failed: {e}")


def _transcribe_audio_with_happyscribe(audio_path: str, api_key: str) -> tuple[str, dict]:
    """
    Transcribe audio using HappyScribe API.
    Requires HAPPYSCRIBE_API_KEY environment variable.
    """
    import requests
    import time

    base_url = "https://www.happyscribe.com/api/v1"

    # Step 1: Upload the file
    with open(audio_path, "rb") as f:
        files = {"file": f}
        data = {"language": "en"}
        headers = {"Authorization": f"Bearer {api_key}"}

        upload_resp = requests.post(
            f"{base_url}/transcriptions",
            files=files,
            data=data,
            headers=headers,
            timeout=300  # 5 minutes for upload
        )

    if upload_resp.status_code != 201:
        raise RuntimeError(f"HappyScribe upload failed: {upload_resp.text}")

    transcription_data = upload_resp.json()
    transcription_id = transcription_data["id"]

    # Step 2: Poll for completion
    headers = {"Authorization": f"Bearer {api_key}"}
    max_attempts = 60  # 5 minutes max
    attempt = 0

    while attempt < max_attempts:
        time.sleep(5)  # Wait 5 seconds
        attempt += 1

        status_resp = requests.get(
            f"{base_url}/transcriptions/{transcription_id}",
            headers=headers,
            timeout=30
        )

        if status_resp.status_code != 200:
            raise RuntimeError(f"HappyScribe status check failed: {status_resp.text}")

        status_data = status_resp.json()
        state = status_data.get("state")

        if state == "completed":
            # Get the transcript text
            text = status_data.get("text", "").strip()
            return text, {"transcription_service": "happyscribe", "transcription_id": transcription_id}
        elif state in ("failed", "error"):
            raise RuntimeError(f"HappyScribe transcription failed: {status_data}")

    raise RuntimeError("HappyScribe transcription timed out")


def _get_youtube_transcript_via_audio(video_id: str, original_error: str = "") -> tuple[str, dict]:
    if not _ffmpeg_available():
        raise RuntimeError(
            "Audio transcription fallback requires ffmpeg. "
            "Install ffmpeg and try again, or use a YouTube video with captions enabled."
        )

    with tempfile.TemporaryDirectory() as tmp_dir:
        audio_path, info = _download_youtube_audio(video_id, tmp_dir)
        duration_seconds = int(info.get("duration", 0) or 0)
        if duration_seconds >= 60:
            minutes = duration_seconds // 60
            seconds = duration_seconds % 60
            raise RuntimeError(
                "This video is longer than 1 minute. "
                f"Detected duration: {minutes}m {seconds}s. "
                "Please submit a short clip under 1 minute."
            )

        text, transcript_meta = _transcribe_audio_file(audio_path)
        metadata = {
            "video_id": video_id,
            "url": f"https://www.youtube.com/watch?v={video_id}",
            "language": "en",
            "is_generated": False,
            "duration_seconds": duration_seconds,
            "transcript_source": "audio_fallback",
            **transcript_meta,
        }
        return text, metadata


def get_video_title_from_youtube(video_id: str) -> str:
    """Try to get video title via oEmbed (no API key needed)."""
    try:
        import requests
        url = f"https://www.youtube.com/oembed?url=https://www.youtube.com/watch?v={video_id}&format=json"
        resp = requests.get(url, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            return data.get("title", "")
    except Exception:
        pass
    return ""


def check_health_relevance(transcript: str, title: str = "") -> Tuple[bool, float]:
    """
    Heuristic check: is this video health/food/medical related?

    Returns:
        (is_health_related, confidence_score 0-1)
    """
    combined = (title + " " + transcript[:3000]).lower()
    words = re.findall(r'\b\w+\b', combined)
    total_words = len(words)

    if total_words == 0:
        return False, 0.0

    keyword_hits = sum(
        1 for kw in HEALTH_KEYWORDS
        if kw.lower() in combined
    )

    # Score based on keyword density
    score = min(keyword_hits / 10.0, 1.0)  # cap at 1.0

    is_health = keyword_hits >= 3  # at least 3 health keywords
    return is_health, round(score, 2)


def llm_check_health_relevance(transcript: str, title: str = "", api_key: str = None) -> Tuple[bool, float]:
    """
    Ask an LLM to decide if the transcript is health/food/medicine related.
    Used as fallback when the keyword heuristic scores below 0.5.

    Returns:
        (is_health_related, confidence_score 0-1)
    """
    import anthropic

    client = anthropic.Anthropic(api_key=api_key) if api_key else anthropic.Anthropic()

    sample = transcript[:2000].strip()
    prompt = (
        f"Video title: {title or 'Unknown'}\n\n"
        f"Transcript excerpt:\n{sample}\n\n"
        "Is this video primarily about health, medicine, nutrition, food, supplements, fitness, "
        "or any related wellness topic? Answer with ONLY a JSON object like:\n"
        '{"is_health_related": true, "confidence": 0.85, "reason": "one-sentence reason"}'
    )

    try:
        message = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=100,
            messages=[{"role": "user", "content": prompt}],
        )
        text = message.content[0].text.strip()
        if text.startswith("```"):
            text = text.split("```")[1].lstrip("json").strip()
        data = json.loads(text)
        is_health = bool(data.get("is_health_related", False))
        confidence = float(data.get("confidence", 0.5))
        return is_health, round(confidence, 2)
    except Exception as e:
        logger.warning(f"LLM health check failed: {e}")
        return False, 0.0


def get_transcript(url: str, api_key: str = None) -> dict:
    """
    Main entry point: validate URL, extract transcript.

    Returns dict with keys:
        - success: bool
        - error: str (if not success)
        - transcript: str
        - metadata: dict
        - platform: str
        - video_id: str
        - is_health_related: bool
        - health_confidence: float
    """
    # Step 1: Validate it's a video URL
    is_video, platform, video_id = is_video_url(url)

    if not is_video:
        return {
            "success": False,
            "error": (
                "I can only parse video links for now. "
                "Please provide a YouTube, Vimeo, or direct video URL (e.g. .mp4). "
                "The URL you provided does not appear to be a video."
            ),
        }

    # Step 2: Extract transcript based on platform
    transcript_text = ""
    metadata = {}
    title = ""

    if platform == "youtube":
        try:
            transcript_text, metadata = get_youtube_transcript(video_id)
            title = get_video_title_from_youtube(video_id)
            metadata["title"] = title

            duration_seconds = int(metadata.get("duration_seconds", 0))
            if duration_seconds >= 60:
                minutes = duration_seconds // 60
                seconds = duration_seconds % 60
                return {
                    "success": False,
                    "error": (
                        "This video is longer than 1 minute. "
                        f"Detected duration: {minutes}m {seconds}s. "
                        "Please submit a short clip under 1 minute."
                    ),
                }
        except RuntimeError as e:
            return {"success": False, "error": str(e)}

    elif platform == "vimeo":
        return {
            "success": False,
            "error": (
                "Vimeo support requires audio download. "
                "Please use a YouTube URL for the best experience. "
                "Vimeo auto-transcription is on the roadmap."
            ),
        }

    elif platform == "direct":
        return {
            "success": False,
            "error": (
                "Direct video file URLs require downloading the video for transcription. "
                "For best results, please use a YouTube URL — transcripts are fetched instantly without any download."
            ),
        }

    if not transcript_text or len(transcript_text.strip()) < 100:
        return {
            "success": False,
            "error": (
                "Could not extract a usable transcript from this video. "
                "The video may not have captions, or they may be disabled. "
                "Try a different YouTube video with captions enabled."
            ),
        }

    # Step 3: Check if health-related
    is_health, health_score = check_health_relevance(transcript_text, title)

    if health_score < 0.5:
        logger.info(f"Keyword score {health_score:.0%} below threshold — using LLM health check")
        is_health, health_score = llm_check_health_relevance(transcript_text, title, api_key=api_key)

    if not is_health:
        return {
            "success": False,
            "error": (
                "This video does not appear to be about health, food, or medicine. "
                f"(Detected health relevance score: {health_score:.0%}). "
                "Please submit a video about nutrition, diet, supplements, medical topics, or health advice."
            ),
        }

    return {
        "success": True,
        "transcript": transcript_text,
        "metadata": metadata,
        "platform": platform,
        "video_id": video_id,
        "title": title,
        "is_health_related": is_health,
        "health_confidence": health_score,
    }
