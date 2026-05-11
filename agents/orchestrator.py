"""
agents/orchestrator.py

Orchestrates the full multi-agent pipeline:
1. ClaimExtractorAgent  → extract claims from transcript
2. ResearchAgents       → parallel research per claim (ThreadPoolExecutor)
3. ConfidenceScorerAgent → final scoring

Emits Server-Sent Events (SSE) for real-time progress updates.
"""

import json
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Generator, Callable

from agents.claim_extractor import extract_claims
from agents.research_agent import research_claim
from agents.confidence_scorer import score_claims

logger = logging.getLogger(__name__)


class HealthClaimOrchestrator:
    """
    Coordinates all agents in the health claim verification pipeline.
    """

    def __init__(self, api_key: str = None, max_claims: int = 5, max_workers: int = 3):
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        self.max_claims = max_claims
        self.max_workers = max_workers  # Parallel research agents

    def run(
        self,
        transcript: str,
        metadata: dict,
        progress_callback: Callable[[dict], None] = None,
    ) -> dict:
        """
        Run the full pipeline.

        Args:
            transcript: Video transcript text
            metadata: Video metadata (title, url, etc.)
            progress_callback: Optional function called with progress updates

        Returns:
            Final analysis result dict
        """

        def emit(stage: str, message: str, data: dict = None, pct: int = 0):
            if progress_callback:
                progress_callback({
                    "stage": stage,
                    "message": message,
                    "data": data or {},
                    "progress_pct": pct,
                })
            logger.info(f"[{stage}] {message}")

        title = metadata.get("title", "")
        start_time = time.time()

        # ─── Stage 1: Extract Claims ───────────────────────────────────────────
        emit("extracting", "🎬 Analyzing video transcript and extracting health claims...", pct=5)

        try:
            extraction = extract_claims(
                transcript=transcript,
                title=title,
                max_claims=self.max_claims,
                api_key=self.api_key,
            )
        except Exception as e:
            logger.error(f"Claim extraction failed: {e}")
            raise RuntimeError(f"Failed to extract claims: {e}")

        claims = extraction.get("claims", [])
        video_summary = extraction.get("video_summary", "")
        total_found = extraction.get("total_claims_found", len(claims))

        if not claims:
            raise ValueError("No health claims found in this video transcript.")

        emit(
            "claims_extracted",
            f"✅ Found {len(claims)} claims (out of {total_found} identified)",
            data={"claims": claims, "video_summary": video_summary},
            pct=20,
        )

        # ─── Stage 2: Parallel Research Agents ────────────────────────────────
        emit(
            "researching",
            f"🔬 Spawning {len(claims)} research agents (running in parallel)...",
            pct=25,
        )

        research_results = []
        completed_count = 0

        def research_with_progress(claim):
            result = research_claim(claim=claim, api_key=self.api_key)
            return result

        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            future_to_claim = {
                executor.submit(research_with_progress, claim): claim
                for claim in claims
            }

            for future in as_completed(future_to_claim):
                claim = future_to_claim[future]
                completed_count += 1
                try:
                    result = future.result()
                    research_results.append(result)
                    pct = 25 + int((completed_count / len(claims)) * 40)
                    emit(
                        "research_progress",
                        f"🔍 Researched claim {completed_count}/{len(claims)}: {claim.get('claim', '')[:60]}...",
                        data={"claim_id": claim.get("id"), "result": result},
                        pct=pct,
                    )
                except Exception as e:
                    logger.error(f"Research failed for claim {claim.get('id')}: {e}")
                    # Add empty result so pipeline continues
                    research_results.append({
                        "claim_id": claim.get("id"),
                        "claim_text": claim.get("claim", ""),
                        "supporting_evidence": [],
                        "contradicting_evidence": [],
                        "pubmed_articles": [],
                        "research_summary": f"Research failed: {str(e)}",
                        "consensus_direction": "insufficient_evidence",
                        "notable_caveats": [],
                    })

        # Sort results to match claims order
        research_results.sort(key=lambda r: r.get("claim_id", 0))

        emit("research_complete", "✅ All research agents completed", pct=65)

        # ─── Stage 3: Confidence Scoring ──────────────────────────────────────
        emit(
            "scoring",
            "📊 Confidence scoring agent: calculating final verdicts...",
            pct=70,
        )

        try:
            final_scores = score_claims(
                claims=claims,
                research_results=research_results,
                title=title,
                video_summary=video_summary,
                api_key=self.api_key,
            )
        except Exception as e:
            logger.error(f"Scoring failed: {e}")
            raise RuntimeError(f"Failed to score claims: {e}")

        emit(
            "scoring_complete",
            "✅ All claims scored",
            data={"scores": final_scores},
            pct=95,
        )

        # ─── Assemble Final Result ─────────────────────────────────────────────
        elapsed = round(time.time() - start_time, 1)

        # Merge everything into a unified result per claim
        unified_claims = []
        for claim in claims:
            cid = claim.get("id")
            research = next(
                (r for r in research_results if r.get("claim_id") == cid),
                {}
            )
            scored = next(
                (s for s in final_scores.get("scored_claims", []) if s.get("claim_id") == cid),
                {}
            )

            unified_claims.append({
                "id": cid,
                "claim": claim.get("claim", ""),
                "quote": claim.get("quote", ""),
                "category": claim.get("category", ""),
                "assertion_strength": claim.get("assertion_strength", "moderate"),
                # Scores
                "confidence_score": scored.get("confidence_score", 50),
                "verdict": scored.get("verdict", "Needs Context"),
                "verdict_summary": scored.get("verdict_summary", ""),
                "evidence_grade": scored.get("evidence_grade", "C"),
                "recommended_action": scored.get("recommended_action", "Consult doctor first"),
                "key_supporting_points": scored.get("key_supporting_points", []),
                "key_contradicting_points": scored.get("key_contradicting_points", []),
                # Evidence
                "supporting_evidence": research.get("supporting_evidence", []),
                "contradicting_evidence": research.get("contradicting_evidence", []),
                "pubmed_articles": research.get("pubmed_articles", []),
                "research_summary": research.get("research_summary", ""),
                "consensus_direction": research.get("consensus_direction", "insufficient_evidence"),
                "notable_caveats": research.get("notable_caveats", []),
                # Citations
                "citations": scored.get("citations", []),
            })

        result = {
            "success": True,
            "title": title,
            "video_url": metadata.get("url", ""),
            "video_summary": video_summary,
            "claims": unified_claims,
            "overall_video_verdict": final_scores.get("overall_video_verdict", ""),
            "video_trustworthiness_score": final_scores.get("video_trustworthiness_score", 50),
            "top_recommendation": final_scores.get("top_recommendation", ""),
            "elapsed_seconds": elapsed,
            "total_claims_found": total_found,
            "claims_analyzed": len(claims),
        }

        emit(
            "complete",
            f"🎉 Analysis complete in {elapsed}s! {len(claims)} claims verified.",
            data=result,
            pct=100,
        )

        return result
