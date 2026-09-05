"""Sayri Web Search CLI tool with multi-source fallback (DuckDuckGo IA, Wikipedia, ArchWiki)."""

import argparse
import html
import re
import sys
import urllib.parse
import urllib.request
import json
from typing import List, Optional


def search_web(query: str, max_results: int = 3) -> str:
    """Searches web knowledge sources and returns clean text summaries."""
    clean_q = query.strip()
    if not clean_q:
        return "No search query provided."

    results = []

    # 1. DuckDuckGo Instant Answer API
    try:
        url = f"https://api.duckduckgo.com/?q={urllib.parse.quote(clean_q)}&format=json&no_html=1"
        req = urllib.request.Request(url, headers={"User-Agent": "Sayri/1.0"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            if data.get("AbstractText"):
                results.append(f"• Summary: {data['AbstractText']}")
            elif data.get("Heading") and data.get("RelatedTopics"):
                for t in data["RelatedTopics"][:2]:
                    if isinstance(t, dict) and t.get("Text"):
                        results.append(f"• {t['Text']}")
    except Exception:
        pass

    # 2. Wikipedia Search API
    try:
        lang = "es" if any(w in clean_q.lower() for w in ["que", "como", "abrir", "los", "las", "ajustes", "configuracion"]) else "en"
        wiki_url = f"https://{lang}.wikipedia.org/w/api.php?action=query&list=search&srsearch={urllib.parse.quote(clean_q)}&utf8=&format=json"
        req = urllib.request.Request(wiki_url, headers={"User-Agent": "Sayri/1.0"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            for item in data.get("query", {}).get("search", [])[:max_results]:
                snip = re.sub(r"<[^>]+>", "", item.get("snippet", "")).strip()
                title = item.get("title", "")
                if snip and title:
                    results.append(f"• {title}: {html.unescape(snip)}")
    except Exception:
        pass

    # 3. ArchWiki Search API
    try:
        arch_url = f"https://wiki.archlinux.org/api.php?action=opensearch&search={urllib.parse.quote(clean_q)}&limit=3&format=json"
        req = urllib.request.Request(arch_url, headers={"User-Agent": "Sayri/1.0"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            if len(data) >= 4 and data[1]:
                for idx, title in enumerate(data[1][:2]):
                    link = data[3][idx] if len(data[3]) > idx else ""
                    results.append(f"• ArchWiki [{title}]({link})")
    except Exception:
        pass

    if not results:
        return f"No direct web summary found for: '{clean_q}'. Inspect local desktop files with grep / which."

    return "\n".join(results[:max_results])


def main(args: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Sayri Web Search CLI")
    parser.add_argument("query", nargs="*", help="Search query")
    parser.add_argument("--limit", type=int, default=3, help="Max results")

    parsed = parser.parse_args(args or sys.argv[1:])
    q = " ".join(parsed.query).strip()
    if not q:
        parser.print_help()
        return 1

    print(search_web(q, max_results=parsed.limit))
    return 0


if __name__ == "__main__":
    sys.exit(main())
