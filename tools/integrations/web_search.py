"""Web search tool backed by Tavily.

Tavily is an AI-optimized search API: it returns pre-summarized content
chunks rather than raw HTML snippets, which significantly reduces the
reasoning burden on the LLM consuming the results.

Register with TavilyAdapter().register(tools) when TAVILY_API_KEY is set.
"""

from __future__ import annotations

import ipaddress
import logging
import os
from typing import Any, Optional
from urllib.parse import urlsplit

import requests

logger = logging.getLogger(__name__)

TAVILY_ENDPOINT = "https://api.tavily.com/search"
TAVILY_EXTRACT_ENDPOINT = "https://api.tavily.com/extract"

# One page is capped so a long article cannot crowd out the conversation.
MAX_PAGE_CHARS = 12_000


class WebReadError(RuntimeError):
    """A page could not be read. The message is safe to show the user."""


def check_public_url(url: str) -> str:
    """Return the URL if it is a public http(s) address, else raise.

    Pages are fetched by Tavily, not from this host, so a private address
    could not be reached anyway. Refusing here gives a clear answer instead
    of a confusing fetch failure, and keeps tailnet hostnames out of a
    third party's logs.
    """
    url = str(url).strip()
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if parts.scheme not in ("http", "https") or not host:
        raise WebReadError("Give a full web address starting with http:// or https://.")
    private = host == "localhost" or host.endswith((".local", ".internal", ".ts.net"))
    try:
        address = ipaddress.ip_address(host)
        private = private or not address.is_global
    except ValueError:
        private = private or "." not in host
    if private:
        raise WebReadError("I can only read pages on the public web.")
    return url


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

    def _extract(self, url: str, depth: str) -> dict[str, Any]:
        try:
            response = requests.post(
                TAVILY_EXTRACT_ENDPOINT,
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={
                    "urls": [url],
                    "extract_depth": depth,
                    "format": "markdown",
                    "timeout": 20,
                },
                timeout=30,
            )
        except requests.RequestException as exc:
            raise WebReadError(f"The page reader could not be reached: {exc}") from None
        if response.status_code in (401, 403):
            raise WebReadError("The page reader rejected the stored API key.")
        if response.status_code == 429:
            raise WebReadError("The page reader is rate-limited; try again shortly.")
        if response.status_code != 200:
            raise WebReadError(
                f"The page reader returned HTTP {response.status_code}."
            )
        try:
            data = response.json()
        except ValueError:
            raise WebReadError("The page reader returned an unreadable reply.") from None
        return data if isinstance(data, dict) else {}

    def read(self, url: str) -> dict[str, Any]:
        """Fetch one public page as text. Raises WebReadError when it cannot."""
        url = check_public_url(url)
        content = ""
        title = None
        # Plain fetch first; pages that need JavaScript get one deeper retry.
        for depth in ("basic", "advanced"):
            data = self._extract(url, depth)
            results = data.get("results") or []
            first = results[0] if results and isinstance(results[0], dict) else {}
            content = str(first.get("raw_content") or "").strip()
            title = first.get("title") or None
            if content:
                break
        if not content:
            raise WebReadError(
                "That page returned no readable text. It may need a login, "
                "block automated readers, or not exist."
            )
        truncated = len(content) > MAX_PAGE_CHARS
        if truncated:
            content = content[:MAX_PAGE_CHARS].rstrip() + "\n[... page continues]"
        return {
            "url": url,
            "title": title,
            "truncated": truncated,
            "content": content,
        }

    def register(self, tools) -> None:
        """Register web_search and web_read on the given ToolRegistry."""
        tools.register(
            name="web_read",
            description=(
                "Read one public web page and return its text. Use when the "
                "user gives a URL, or after web_search when a result's "
                "snippet is not enough to answer. One page per call, and "
                "long pages are cut off. Cannot read pages behind a login "
                "or on a private network."
            ),
            schema={
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "Full address, starting with https://",
                    },
                },
                "required": ["url"],
                "additionalProperties": False,
            },
            handler=lambda url: self.read(url),
        )
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
