"""
integrations/search.py

Unified web search utility module for AI Mentor agents.
Supports DuckDuckGo Search (zero-config local default) and Tavily Search API
(if TAVILY_API_KEY is present in environment variables).
"""

from __future__ import annotations

import os


def perform_web_search(query: str, max_results: int = 3) -> str:
    """
    Perform a web search for `query` and return a clean, formatted text summary.
    
    Order of preference:
    1. Tavily Search API (if TAVILY_API_KEY is set in environment)
    2. DuckDuckGo Search (fallback, no API key required)

    Returns:
        Formatted Markdown string containing search results, or a fallback message if search fails.
    """
    cleaned_query = query.strip()
    if not cleaned_query:
        return "No query provided for web search."

    # Strategy 1: Try Tavily API if key is present
    tavily_key = os.getenv("TAVILY_API_KEY", "").strip()
    if tavily_key:
        try:
            results_str = _search_tavily(cleaned_query, tavily_key, max_results)
            if results_str:
                return results_str
        except Exception as exc:
            print(f"[integrations.search] Tavily search failed: {exc}. Falling back to DuckDuckGo.")

    # Strategy 2: DuckDuckGo search fallback
    try:
        results_str = _search_duckduckgo(cleaned_query, max_results)
        if results_str:
            return results_str
    except Exception as exc:
        print(f"[integrations.search] DuckDuckGo search failed: {exc}.")

    return f"Web search for '{cleaned_query}' yielded no results or encountered an error."


def _search_tavily(query: str, api_key: str, max_results: int) -> str:
    """Search using Tavily API."""
    try:
        from tavily import TavilyClient
        client = TavilyClient(api_key=api_key)
        response = client.search(query=query, max_results=max_results)
        results = response.get("results", [])
        if not results:
            return ""

        formatted: list[str] = [f"### Web Search Results for '{query}' (via Tavily):"]
        for idx, item in enumerate(results, start=1):
            title = item.get("title", "No Title")
            snippet = item.get("content", "").strip()
            url = item.get("url", "")
            formatted.append(f"{idx}. **{title}**\n   {snippet}\n   *Source: {url}*")

        return "\n\n".join(formatted)
    except Exception:
        try:
            from tavily import TavilyClient
            client = TavilyClient(api_key=api_key)
            response = client.search(query=query, max_results=max_results)
            results = response.get("results", [])
            if not results:
                return ""
            formatted: list[str] = [f"### Web Search Results for '{query}' (via Tavily):"]
            for idx, item in enumerate(results, start=1):
                title = item.get("title", "No Title")
                snippet = item.get("content", "").strip()
                url = item.get("url", "")
                formatted.append(f"{idx}. **{title}**\n   {snippet}\n   *Source: {url}*")
            return "\n\n".join(formatted)
        except Exception:
            return ""


def _search_duckduckgo(query: str, max_results: int) -> str:
    """Search using DuckDuckGo (ddgs package)."""
    from ddgs import DDGS

    with DDGS() as ddgs:
        results = list(ddgs.text(query, max_results=max_results))

    if not results:
        return ""

    formatted: list[str] = [f"### Web Search Results for '{query}':"]
    for idx, item in enumerate(results, start=1):
        title = item.get("title", "No Title")
        snippet = item.get("body", "").strip()
        url = item.get("href", "")
        formatted.append(f"{idx}. **{title}**\n   {snippet}\n   *Source: {url}*")

    return "\n\n".join(formatted)
