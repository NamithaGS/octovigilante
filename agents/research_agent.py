"""
agents/research_agent.py

Research Agent: Given a single health claim, searches for scientific evidence
using Claude's web_search tool to query top medical/health journals, plus
direct PubMed API calls.

One instance of this agent is spawned per claim (runs in parallel via threads).
"""

import json
import logging
import anthropic
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from utils.pubmed import search_pubmed, format_pubmed_results

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are a medical research specialist with access to web search. Your job is to find scientific evidence for or against specific health claims.

For each claim you research:
1. Search for peer-reviewed studies from authoritative sources: PubMed, NEJM, The Lancet, JAMA, BMJ, Nature Medicine, Cell Metabolism, WHO, NHS Evidence
2. Look for both supporting AND contradicting evidence
3. Note study quality: RCT > meta-analysis > cohort study > observational > case study > expert opinion
4. Extract specific findings: sample sizes, effect sizes, p-values, confidence intervals when available

Always search multiple angles:
- The claim directly (e.g., "vitamin D depression treatment")
- The mechanism (e.g., "vitamin D serotonin pathway")
- Contradicting evidence (e.g., "vitamin D depression no effect")

Return ONLY valid JSON. No markdown, no preamble."""

RESEARCH_PROMPT = """Research the following health claim using web search. Find scientific evidence from top medical journals.

CLAIM #{claim_id}: {claim}
CATEGORY: {category}
KEYWORDS: {keywords}

PUBMED PRE-SEARCH RESULTS (already retrieved):
{pubmed_results}

Instructions:
1. Use web_search to find additional evidence from: PubMed, NEJM, Lancet, JAMA, BMJ, WHO, Harvard Health, Mayo Clinic
2. Search for BOTH supporting and contradicting evidence
3. Find the most recent and highest-quality studies

Return a JSON object with this EXACT structure:
{{
  "claim_id": {claim_id},
  "supporting_evidence": [
    {{
      "source": "Journal/Institution name",
      "year": "2023",
      "title": "Study or article title",
      "finding": "What this evidence says in support of the claim",
      "url": "https://...",
      "evidence_quality": "RCT|meta-analysis|cohort|observational|review|expert_opinion|guideline",
      "sample_size": "n=1000 or null"
    }}
  ],
  "contradicting_evidence": [
    {{
      "source": "Journal/Institution name", 
      "year": "2022",
      "title": "Study title",
      "finding": "What this evidence says against the claim",
      "url": "https://...",
      "evidence_quality": "meta-analysis",
      "sample_size": null
    }}
  ],
  "research_summary": "2-3 sentence summary of what the scientific consensus shows",
  "consensus_direction": "supports|contradicts|mixed|insufficient_evidence",
  "notable_caveats": ["caveat1", "caveat2"]
}}"""


def research_claim(
    claim: dict,
    api_key: str = None,
) -> dict:
    """
    Research a single health claim using web search + PubMed.

    Args:
        claim: dict with keys: id, claim, category, keywords
        api_key: Anthropic API key (uses env var if None)

    Returns:
        Research results dict
    """
    client = anthropic.Anthropic(api_key=api_key) if api_key else anthropic.Anthropic()

    claim_id = claim.get("id", 1)
    claim_text = claim.get("claim", "")
    category = claim.get("category", "health")
    keywords = claim.get("keywords", [])

    logger.info(f"ResearchAgent [{claim_id}]: Researching: {claim_text[:80]}...")

    # Step 1: Pre-fetch PubMed results (free, fast, no web search credit used)
    pubmed_query = " ".join(keywords[:4]) if keywords else claim_text[:100]
    pubmed_articles = search_pubmed(pubmed_query, max_results=5)
    pubmed_text = format_pubmed_results(pubmed_articles)

    # Step 2: Use Claude with web_search for deeper research
    prompt = RESEARCH_PROMPT.format(
        claim_id=claim_id,
        claim=claim_text,
        category=category,
        keywords=", ".join(keywords),
        pubmed_results=pubmed_text,
    )

    tools = [
        {
            "type": "web_search_20250305",
            "name": "web_search",
        }
    ]

    messages = [{"role": "user", "content": prompt}]

    # Agentic loop — Claude may search multiple times
    max_iterations = 5
    for iteration in range(max_iterations):
        response = client.messages.create(
            model="claude-sonnet-4-5",
            max_tokens=3000,
            system=SYSTEM_PROMPT,
            tools=tools,
            messages=messages,
        )

        # Check stop reason
        if response.stop_reason == "end_turn":
            # Extract final text
            final_text = ""
            for block in response.content:
                if hasattr(block, "text"):
                    final_text = block.text
            break

        elif response.stop_reason == "tool_use":
            # Process tool calls and continue
            messages.append({"role": "assistant", "content": response.content})

            tool_results = []
            for block in response.content:
                if block.type == "tool_use":
                    logger.info(f"ResearchAgent [{claim_id}]: Web search: {block.input.get('query', '')[:60]}")
                    # Web search results are handled by Anthropic automatically
                    # but we need to pass back tool_result blocks
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": "Search completed.",
                    })

            if tool_results:
                messages.append({"role": "user", "content": tool_results})
        else:
            # Unexpected stop reason
            final_text = ""
            for block in response.content:
                if hasattr(block, "text"):
                    final_text = block.text
            break
    else:
        # Exhausted iterations
        final_text = ""
        for block in response.content:
            if hasattr(block, "text"):
                final_text = block.text

    # Parse JSON response
    final_text = final_text.strip()
    if final_text.startswith("```"):
        parts = final_text.split("```")
        final_text = parts[1] if len(parts) > 1 else final_text
        if final_text.startswith("json"):
            final_text = final_text[4:]
    final_text = final_text.strip()

    try:
        result = json.loads(final_text)
        # Inject PubMed articles as additional supporting evidence sources
        result["pubmed_articles"] = pubmed_articles
        result["claim_text"] = claim_text
        logger.info(
            f"ResearchAgent [{claim_id}]: Done. "
            f"Supporting: {len(result.get('supporting_evidence', []))}, "
            f"Contradicting: {len(result.get('contradicting_evidence', []))}"
        )
        return result
    except json.JSONDecodeError as e:
        logger.error(f"ResearchAgent [{claim_id}]: JSON parse error: {e}")
        # Return a minimal result rather than crashing
        return {
            "claim_id": claim_id,
            "claim_text": claim_text,
            "supporting_evidence": [],
            "contradicting_evidence": [],
            "pubmed_articles": pubmed_articles,
            "research_summary": "Research could not be fully parsed. Please try again.",
            "consensus_direction": "insufficient_evidence",
            "notable_caveats": ["Research parsing error occurred"],
            "error": str(e),
        }
