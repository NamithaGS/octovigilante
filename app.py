"""
app.py — HealthCheck AI Flask Backend

Endpoints:
  POST /api/analyze      → Start analysis (returns job_id, streams SSE progress)
  GET  /api/stream/<id>  → SSE stream of progress events
  GET  /api/result/<id>  → Get final result once complete
  GET  /                 → Serve frontend
"""

import json
import logging
import os
import queue
import threading
import time
import uuid

from flask import Flask, Response, jsonify, render_template, request, stream_with_context
from flask_cors import CORS
from dotenv import load_dotenv

load_dotenv()

from utils.transcript import get_transcript
from agents.orchestrator import HealthClaimOrchestrator

# ─── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# ─── Flask App ────────────────────────────────────────────────────────────────
app = Flask(
    __name__,
    template_folder="frontend/templates",
    static_folder="frontend/static",
)
CORS(app)

# In-memory job store (use Redis for production)
jobs: dict[str, dict] = {}
job_queues: dict[str, queue.Queue] = {}

# ─── Routes ──────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/analyze", methods=["POST"])
def analyze():
    """
    Start analysis pipeline for a video URL.
    Returns immediately with a job_id; client streams progress via /api/stream/<job_id>.
    """
    body = request.get_json(silent=True) or {}
    url = (body.get("url") or "").strip()

    if not url:
        return jsonify({"error": "Please provide a video URL."}), 400

    job_id = str(uuid.uuid4())
    q = queue.Queue()
    job_queues[job_id] = q
    jobs[job_id] = {"status": "running", "result": None, "error": None, "created_at": time.time()}

    # Run pipeline in background thread
    thread = threading.Thread(
        target=_run_pipeline,
        args=(job_id, url, q),
        daemon=True,
    )
    thread.start()

    return jsonify({"job_id": job_id}), 202


@app.route("/api/stream/<job_id>")
def stream(job_id: str):
    """
    SSE endpoint: streams progress events for a job.
    """
    if job_id not in job_queues:
        return jsonify({"error": "Job not found."}), 404

    def event_generator():
        q = job_queues[job_id]
        while True:
            try:
                event = q.get(timeout=60)  # 60s timeout
                if event is None:
                    # Sentinel: pipeline done
                    break
                yield f"data: {json.dumps(event)}\n\n"
                if event.get("stage") in ("complete", "error"):
                    break
            except queue.Empty:
                # Send keepalive
                yield f"data: {json.dumps({'stage': 'keepalive', 'message': 'Still working...'})}\n\n"

    return Response(
        stream_with_context(event_generator()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@app.route("/api/result/<job_id>")
def get_result(job_id: str):
    """
    Get the final result of a completed job.
    """
    job = jobs.get(job_id)
    if not job:
        return jsonify({"error": "Job not found."}), 404

    if job["status"] == "running":
        return jsonify({"status": "running"}), 202

    if job["status"] == "error":
        return jsonify({"status": "error", "error": job["error"]}), 400

    return jsonify({"status": "complete", "result": job["result"]})


# ─── Pipeline Runner ──────────────────────────────────────────────────────────

def _run_pipeline(job_id: str, url: str, q: queue.Queue):
    """
    Background thread: runs the full analysis pipeline and pushes events to queue.
    """
    def emit(event: dict):
        q.put(event)

    try:
        # Step 1: Validate URL and extract transcript
        emit({
            "stage": "validating",
            "message": "🔗 Validating URL and extracting transcript...",
            "progress_pct": 2,
        })

        api_key = os.environ.get("ANTHROPIC_API_KEY")
        transcript_result = get_transcript(url, api_key=api_key)

        if not transcript_result.get("success"):
            error_msg = transcript_result.get("error", "Unknown error")
            emit({"stage": "error", "message": error_msg, "progress_pct": 0})
            jobs[job_id] = {"status": "error", "error": error_msg, "result": None}
            return

        emit({
            "stage": "transcript_ready",
            "message": f"✅ Transcript extracted ({len(transcript_result['transcript'])} characters)",
            "progress_pct": 10,
            "data": {
                "title": transcript_result.get("title", ""),
                "platform": transcript_result.get("platform", ""),
                "health_confidence": transcript_result.get("health_confidence", 0),
            },
        })

        # Step 2: Run orchestrator
        orchestrator = HealthClaimOrchestrator(api_key=api_key, max_claims=5, max_workers=3)

        result = orchestrator.run(
            transcript=transcript_result["transcript"],
            metadata={
                **transcript_result.get("metadata", {}),
                "title": transcript_result.get("title", ""),
                "url": url,
            },
            progress_callback=emit,
        )

        # Store final result
        jobs[job_id] = {"status": "complete", "result": result, "error": None}

    except Exception as e:
        logger.exception(f"Pipeline error for job {job_id}: {e}")
        error_msg = str(e)
        emit({
            "stage": "error",
            "message": f"Analysis failed: {error_msg}",
            "progress_pct": 0,
        })
        jobs[job_id] = {"status": "error", "error": error_msg, "result": None}
    finally:
        # Cleanup queue after 10 minutes
        def cleanup():
            time.sleep(600)
            job_queues.pop(job_id, None)

        threading.Thread(target=cleanup, daemon=True).start()
        q.put(None)  # Sentinel to close SSE stream


# ─── Entry Point ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("FLASK_DEBUG", "false").lower() == "true"

    if not os.environ.get("ANTHROPIC_API_KEY"):
        logger.warning("⚠️  ANTHROPIC_API_KEY not set! Add it to .env file.")

    logger.info(f"🚀 HealthCheck AI starting on http://localhost:{port}")
    app.run(host="0.0.0.0", port=port, debug=debug, threaded=True)
