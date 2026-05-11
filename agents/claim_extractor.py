"""
agents/claim_extractor.py

Agent 1: Extract the top health/medical claims from a video transcript.
Uses Claude with a structured prompt to identify factual claims being made.
"""

import json
import logging
import anthropic

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are a health claim extraction specialist. Your job is to analyze video transcripts and identify the key health, nutrition, food, or medical CLAIMS being made.

A "claim" is a specific factual assertion that can be verified against scientific literature. Examples:
- "Vitamin D deficiency causes depression"
- "Eating red meat increases cancer risk by 20%"
- "Intermittent fasting reverses type 2 diabetes"
- "Omega-3 supplements reduce inflammation"

NOT a claim:
- Opinions or preferences ("I love eating vegetables")
- Vague statements ("eating healthy is important")
- Questions or anecdotes without factual assertions

For each claim, extract:
1. The claim itself (clear, concise, 1-2 sentences)
2. The exact or paraphrased quote from the transcript that contains this claim
3. The category (nutrition/supplementation/disease/treatment/fitness/mental health/other)
4. How strongly the speaker asserts it (strong/moderate/weak)

Return ONLY valid JSON. No markdown, no explanation."""

EXTRACTION_PROMPT = """Analyze this health video transcript and extract the top {max_claims} most significant, specific, and verifiable health claims.

VIDEO TITLE: {title}

TRANSCRIPT:
{transcript}

Return a JSON object with this exact structure:
{{
  "claims": [
    {{
      "id": 1,
      "claim": "Clear statement of the health claim",
      "quote": "Relevant excerpt from transcript",
      "category": "nutrition|supplementation|disease|treatment|fitness|mental_health|other",
      "assertion_strength": "strong|moderate|weak",
      "keywords": ["keyword1", "keyword2", "keyword3"]
    }}
  ],
  "video_summary": "2-3 sentence summary of the video's main health message",
  "total_claims_found": 8
}}

Extract exactly the top {max_claims} most specific and verifiable claims. Prioritize claims that cite numbers, percentages, specific foods, supplements, or medical outcomes."""


def extract_claims(
    transcript: str,
    title: str = "",
    max_claims: int = 5,
    api_key: str = None,
) -> dict:
    """
    Extract health claims from a transcript using Claude.

    Returns:
        {
            "claims": [...],
            "video_summary": "...",
            "total_claims_found": N,
        }
    """
    client = anthropic.Anthropic(api_key=api_key) if api_key else anthropic.Anthropic()

    # Truncate transcript to avoid token limits (keep ~15k chars ≈ ~4k tokens)
    transcript_excerpt = transcript[:15000]
    if len(transcript) > 15000:
        transcript_excerpt += "\n\n[Transcript truncated for processing]"

    prompt = EXTRACTION_PROMPT.format(
        max_claims=max_claims,
        title=title or "Unknown Title",
        transcript=transcript_excerpt,
    )

    logger.info(f"ClaimExtractorAgent: Analyzing transcript ({len(transcript)} chars)...")

    message = client.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=2000,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": prompt}],
    )

    response_text = message.content[0].text.strip()

    # Clean up potential markdown fences
    if response_text.startswith("```"):
        response_text = response_text.split("```")[1]
        if response_text.startswith("json"):
            response_text = response_text[4:]
    response_text = response_text.strip()

    try:
        result = json.loads(response_text)
        logger.info(f"ClaimExtractorAgent: Found {len(result.get('claims', []))} claims")
        return result
    except json.JSONDecodeError as e:
        logger.error(f"ClaimExtractorAgent: JSON parse error: {e}\nResponse: {response_text[:500]}")
        raise ValueError(f"Failed to parse claim extraction response: {e}")
