"""Web search tool backed by Tavily.

Tavily is an AI-optimized search API: it returns pre-summarized content
chunks rather than raw HTML snippets, which significantly reduces the
reasoning burden on the LLM consuming the results.

Register with TavilyAdapter().register(tools) when TAVILY_API_KEY is set.
"""

from __future__ import annotations

import logging
import os
from typing import Optional

import requests

logger = logging.getLogger(__name__)

TAVILY_ENDPOINT = "https://api.tavily.com/search"


class TavilyAdapter:
    """Web search via Tavily's AI-optimized API."""

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.environ.get("TAVILY_API_KEY")
        if not self.api_key:
            raise RuntimeError("TAVILY_API_KEY not set")

    def search(
        self,
        query: str,
        depth: str = "basic",
        max_results: int = 5,
        include_answer: bool = True,
    ) -> str:
        """Search the web and return a digestible result string.

        Args:
            query: Natural language search query.
            depth: 'basic' (fast, ~1s) or 'advanced' (thorough, ~3-5s).
            max_results: Number of result snippets to include (1-10).
            include_answer: Whether to ask Tavily for a synthesized answer.

        Returns:
            Multi-paragraph string with synthesized answer (if requested)
            and individual result snippets, ready for LLM consumption.
        """
        if not query or not query.strip():
            return "Empty search query."

        try:
            response = requests.post(
                TAVILY_ENDPOINT,
                json={
                    "api_key": self.api_key,
                    "query": query,
                    "search_depth": depth if depth in ("basic", "advanced") else "basic",
                    "include_answer": include_answer,
                    "max_results": max(1, min(int(max_results), 10)),
                },
                timeout=15,
            )
        except requests.RequestException as e:
            logger.warning("Tavily request failed: %s", e)
            return f"Search failed: {e}"

        if response.status_code != 200:
            logger.warning(
                "Tavily returned %d: %s", response.status_code, response.text[:200]
            )
            return f"Search returned HTTP {response.status_code}"

        try:
            data = response.json()
        except ValueError:
            return "Search returned non-JSON response"

        # Format for LLM consumption
        parts = []

        # Synthesized answer at top, if Tavily provided one
        answer = data.get("answer", "").strip()
        if answer:
            parts.append(f"SUMMARY: {answer}")

        # Individual results
        results = data.get("results", [])
        if results:
            parts.append("\nSOURCES:")
            for i, r in enumerate(results, 1):
                title = r.get("title", "Untitled").strip()
                url = r.get("url", "").strip()
                content = r.get("content", "").strip()
                # Trim excessively long snippets
                if len(content) > 500:
                    content = content[:497] + "..."
                parts.append(f"\n{i}. {title}\n   {url}\n   {content}")
        else:
            parts.append("\nNo results found.")

        return "\n".join(parts)

    def register(self, tools) -> None:
        """Register web_search as a tool on the given ToolRegistry."""
        tools.register(
            name="web_search",
            description=(
                "Search the web for current information. Use when the user "
                "asks about current events, prices, weather, business hours, "
                "recent news, or any factual query that may have changed "
                "since your training data. Do NOT use for general knowledge "
                "or things you already know. Use depth='advanced' for "
                "research-style queries that need detailed content; "
                "depth='basic' for quick lookups."
            ),
            schema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Natural language search query",
                    },
                    "depth": {
                        "type": "string",
                        "enum": ["basic", "advanced"],
                        "description": (
                            "Search depth. 'basic' is fast (~1s), 'advanced' "
                            "fetches and summarizes more content (~3-5s)."
                        ),
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Number of source snippets (1-10, default 5)",
                    },
                },
                "required": ["query"],
            },
            handler=lambda query, depth="basic", max_results=5: self.search(
                query, depth=depth, max_results=max_results
            ),
        )
        logger.info("Registered web_search tool (Tavily)")
