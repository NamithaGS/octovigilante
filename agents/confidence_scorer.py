"""
agents/confidence_scorer.py

Final Agent: Takes all gathered data points and produces a final confidence
score (0-100%) for each claim, plus a structured verdict.

This is the synthesis agent that combines:
- Research agent findings (per claim)
- Evidence quality weighting
- Consensus direction
"""

import json
import logging
import anthropic

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are a medical evidence evaluator who produces final verdicts on health claims.

You score claims using a rigorous evidence-based framework:
- 90-100%: Overwhelming scientific consensus, replicated RCTs, major guideline endorsement
- 70-89%: Strong evidence, multiple high-quality studies, generally accepted
- 50-69%: Moderate evidence, some quality studies but with limitations or conflicting results
- 30-49%: Weak or mixed evidence, primarily observational studies, conflicting findings
- 10-29%: Little to no credible evidence, contradicted by better studies
- 0-9%: Scientifically unsupported or demonstrably false

Consider:
1. Number and quality of supporting vs. contradicting studies
2. Study designs (RCT > meta-analysis > cohort > observational)
3. Sample sizes and statistical significance
4. Whether major health bodies (WHO, FDA, NHS, AHA) endorse the claim
5. Whether the claim oversimplifies or cherry-picks evidence

Return ONLY valid JSON."""

SCORING_PROMPT = """Score each health claim based on all gathered evidence.

VIDEO TITLE: {title}
VIDEO SUMMARY: {video_summary}

CLAIMS WITH ALL EVIDENCE:
{evidence_summary}

For each claim, produce a final confidence score and verdict.

Return this JSON:
{{
  "scored_claims": [
    {{
      "claim_id": 1,
      "claim": "The claim text",
      "confidence_score": 73,
      "verdict": "Largely True|Partially True|Unproven|Misleading|False|Needs Context",
      "verdict_summary": "2-3 sentence plain-English explanation of the score",
      "key_supporting_points": ["point1", "point2"],
      "key_contradicting_points": ["point1"],
      "recommended_action": "Safe to follow|Consult doctor first|Exercise caution|Avoid|Seek more evidence",
      "evidence_grade": "A|B|C|D|F",
      "citations": [
        {{
          "source": "NEJM 2023",
          "title": "Study title",
          "url": "https://...",
          "supports": true
        }}
      ]
    }}
  ],
  "overall_video_verdict": "A brief overall assessment of the video's reliability",
  "video_trustworthiness_score": 65,
  "top_recommendation": "The single most important takeaway for the viewer"
}}

Evidence grade rubric:
A = Multiple RCTs + meta-analyses + guideline endorsement
B = High-quality studies, strong consensus
C = Moderate quality evidence, some consensus
D = Weak evidence, contradicted or insufficient studies  
F = No credible evidence or demonstrably false"""


def score_claims(
    claims: list,
    research_results: list,
    title: str = "",
    video_summary: str = "",
    api_key: str = None,
) -> dict:
    """
    Final confidence scoring for all claims.

    Args:
        claims: Original claims list
        research_results: Per-claim research findings
        title: Video title
        video_summary: Video summary
        api_key: Anthropic API key

    Returns:
        Final scored claims dict
    """
    client = anthropic.Anthropic(api_key=api_key) if api_key else anthropic.Anthropic()

    # Build comprehensive evidence summary
    evidence_parts = []
    for claim in claims:
        cid = claim.get("id")
        research = next(
            (r for r in research_results if r.get("claim_id") == cid),
            {}
        )

        supporting = research.get("supporting_evidence", [])
        contradicting = research.get("contradicting_evidence", [])
        pubmed = research.get("pubmed_articles", [])

        part = [
            f"=== CLAIM {cid}: {claim.get('claim', '')} ===",
            f"Category: {claim.get('category', '')}",
            f"Assertion strength in video: {claim.get('assertion_strength', 'unknown')}",
            f"",
            f"SUPPORTING EVIDENCE ({len(supporting)} items):",
        ]

        for ev in supporting[:4]:
            part.append(
                f"  • [{ev.get('evidence_quality', '')}] {ev.get('source', '')} {ev.get('year', '')}: "
                f"{ev.get('finding', '')}"
            )

        part.append(f"")
        part.append(f"CONTRADICTING EVIDENCE ({len(contradicting)} items):")
        for ev in contradicting[:3]:
            part.append(
                f"  • [{ev.get('evidence_quality', '')}] {ev.get('source', '')} {ev.get('year', '')}: "
                f"{ev.get('finding', '')}"
            )

        if pubmed:
            part.append(f"")
            part.append(f"PUBMED ARTICLES ({len(pubmed)} found):")
            for art in pubmed[:3]:
                part.append(f"  • {art.get('journal', '')} {art.get('year', '')}: {art.get('title', '')}")

        part.append(f"")
        part.append(f"Research consensus: {research.get('consensus_direction', 'unknown')}")
        part.append(f"Research summary: {research.get('research_summary', '')}")

        evidence_parts.append("\n".join(part))

    evidence_summary = "\n\n".join(evidence_parts)

    prompt = SCORING_PROMPT.format(
        title=title or "Unknown",
        video_summary=video_summary or "Not available",
        evidence_summary=evidence_summary,
    )

    logger.info("ConfidenceScorerAgent: Generating final scores...")

    message = client.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=4000,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": prompt}],
    )

    response_text = message.content[0].text.strip()

    if response_text.startswith("```"):
        parts = response_text.split("```")
        response_text = parts[1] if len(parts) > 1 else response_text
        if response_text.startswith("json"):
            response_text = response_text[4:]
    response_text = response_text.strip()

    try:
        result = json.loads(response_text)
        logger.info(
            f"ConfidenceScorerAgent: Done. "
            f"Video trustworthiness: {result.get('video_trustworthiness_score')}%"
        )
        return result
    except json.JSONDecodeError as e:
        logger.error(f"ConfidenceScorerAgent: JSON parse error: {e}")
        # Fallback: generate basic scores
        scored_claims = []
        for claim in claims:
            cid = claim.get("id")
            research = next(
                (r for r in research_results if r.get("claim_id") == cid),
                {}
            )
            direction = research.get("consensus_direction", "insufficient_evidence")
            score = {"supports": 65, "contradicts": 25, "mixed": 45, "insufficient_evidence": 35}.get(
                direction, 40
            )
            scored_claims.append({
                "claim_id": cid,
                "claim": claim.get("claim", ""),
                "confidence_score": score,
                "verdict": "Needs Context",
                "verdict_summary": research.get("research_summary", "Insufficient data to score."),
                "key_supporting_points": [],
                "key_contradicting_points": [],
                "recommended_action": "Consult doctor first",
                "evidence_grade": "C",
                "citations": [],
            })
        return {
            "scored_claims": scored_claims,
            "overall_video_verdict": "Analysis completed with partial data.",
            "video_trustworthiness_score": 50,
            "top_recommendation": "Consult a healthcare professional before acting on any health advice.",
        }
