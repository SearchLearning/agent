"""
Web Search Tools

Provides tools for searching OpenSearch documentation.
"""

import json
import re
from html import unescape
from urllib.parse import parse_qs, quote_plus, urlparse
from urllib.request import Request, urlopen


def _strip_html(text: str) -> str:
    """Remove HTML tags from text."""
    return re.sub(r"<[^>]+>", "", text)


def _normalize_text(text: str) -> str:
    """Normalize whitespace in text."""
    return re.sub(r"\s+", " ", text).strip()


def _decode_duckduckgo_redirect(url: str) -> str:
    """Decode DuckDuckGo redirect URLs."""
    parsed = urlparse(url)
    if parsed.netloc.endswith("duckduckgo.com") and parsed.path.startswith("/l/"):
        target = parse_qs(parsed.query).get("uddg", [None])[0]
        if target:
            return target
    return url


def search_opensearch_org(query: str, number_of_results: int = 5) -> str:
    """
    Search the OpenSearch documentation using a site-restricted web query.
    
    Args:
        query: The search query text
        number_of_results: Maximum number of results to return (1-10)
    
    Returns:
        str: JSON string with query and filtered results from opensearch.org
    """
    try:
        limited_results = max(1, min(number_of_results, 10))
        search_query = quote_plus(f"site:opensearch.org {query}")
        url = f"https://duckduckgo.com/html/?q={search_query}"
        
        request = Request(
            url,
            headers={"User-Agent": "Mozilla/5.0 (compatible; OpenSearchAgent/1.0)"},
        )
        
        with urlopen(request, timeout=15) as response:
            html = response.read().decode("utf-8", errors="ignore")
        
        # Extract title and URL pairs
        title_matches = re.findall(
            r'<a[^>]*class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
            html,
            flags=re.IGNORECASE | re.DOTALL,
        )
        
        # Extract snippets
        snippet_matches = re.findall(
            r'<a[^>]*class="result__snippet"[^>]*>(.*?)</a>|'
            r'<div[^>]*class="result__snippet"[^>]*>(.*?)</div>',
            html,
            flags=re.IGNORECASE | re.DOTALL,
        )
        snippets = [left or right for left, right in snippet_matches]
        
        results = []
        for idx, (raw_href, raw_title) in enumerate(title_matches):
            href = _decode_duckduckgo_redirect(unescape(raw_href))
            netloc = urlparse(href).netloc.lower()
            
            # Filter to opensearch.org only
            if "opensearch.org" not in netloc:
                continue
            
            title = _normalize_text(unescape(_strip_html(raw_title)))
            snippet = ""
            if idx < len(snippets):
                snippet = _normalize_text(unescape(_strip_html(snippets[idx])))
            
            results.append({
                "title": title,
                "url": href,
                "snippet": snippet,
            })
            
            if len(results) >= limited_results:
                break
        
        if not results:
            return json.dumps(
                {
                    "query": query,
                    "results": [],
                    "message": "No opensearch.org results found.",
                },
                ensure_ascii=False,
                indent=2,
            )
        
        return json.dumps(
            {
                "query": query,
                "results": results,
            },
            ensure_ascii=False,
            indent=2,
        )
    
    except Exception as e:
        return f"Error searching opensearch.org: {e}"
