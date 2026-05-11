"""
utils/pubmed.py

Helper to search PubMed (NCBI) for peer-reviewed evidence.
Uses the free E-utilities API — no API key required (rate limited to 3 req/s).
"""

import requests
import logging
import time
from typing import List, Dict

logger = logging.getLogger(__name__)

PUBMED_SEARCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
PUBMED_FETCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
PUBMED_SUMMARY_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi"


def search_pubmed(query: str, max_results: int = 5) -> List[Dict]:
    """
    Search PubMed for articles related to a query.

    Returns list of article summaries with:
        - pmid, title, authors, journal, year, abstract_url
    """
    try:
        # Search for article IDs
        search_params = {
            "db": "pubmed",
            "term": query,
            "retmax": max_results,
            "retmode": "json",
            "sort": "relevance",
            # Prefer recent high-impact articles
            "datetype": "pdat",
        }

        search_resp = requests.get(PUBMED_SEARCH_URL, params=search_params, timeout=15)
        search_resp.raise_for_status()
        search_data = search_resp.json()

        pmids = search_data.get("esearchresult", {}).get("idlist", [])
        if not pmids:
            return []

        time.sleep(0.35)  # Respect rate limit

        # Fetch summaries for those IDs
        summary_params = {
            "db": "pubmed",
            "id": ",".join(pmids),
            "retmode": "json",
        }

        summary_resp = requests.get(PUBMED_SUMMARY_URL, params=summary_params, timeout=15)
        summary_resp.raise_for_status()
        summary_data = summary_resp.json()

        articles = []
        result = summary_data.get("result", {})
        for pmid in pmids:
            article = result.get(pmid)
            if not article:
                continue

            authors = article.get("authors", [])
            author_names = [a.get("name", "") for a in authors[:3]]
            if len(authors) > 3:
                author_names.append("et al.")

            articles.append({
                "pmid": pmid,
                "title": article.get("title", "").rstrip("."),
                "authors": ", ".join(author_names),
                "journal": article.get("fulljournalname", article.get("source", "")),
                "year": article.get("pubdate", "")[:4],
                "abstract_url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
                "doi": next(
                    (
                        aid.get("value", "")
                        for aid in article.get("articleids", [])
                        if aid.get("idtype") == "doi"
                    ),
                    None,
                ),
            })

        return articles

    except requests.RequestException as e:
        logger.warning(f"PubMed search failed for '{query}': {e}")
        return []
    except Exception as e:
        logger.warning(f"PubMed parsing error: {e}")
        return []


def format_pubmed_results(articles: List[Dict]) -> str:
    """Format PubMed results as a readable string for agent context."""
    if not articles:
        return "No PubMed results found."

    lines = []
    for i, art in enumerate(articles, 1):
        lines.append(f"{i}. [{art['journal']} {art['year']}] {art['title']}")
        lines.append(f"   Authors: {art['authors']}")
        lines.append(f"   URL: {art['abstract_url']}")
        if art.get("doi"):
            lines.append(f"   DOI: {art['doi']}")
        lines.append("")

    return "\n".join(lines)
