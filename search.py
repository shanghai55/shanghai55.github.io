"""Web search helpers for researching personas and finding reference images."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import httpx
from duckduckgo_search import DDGS


@dataclass
class SearchResult:
    title: str
    url: str
    snippet: str


def search_web(query: str, max_results: int = 8) -> list[SearchResult]:
    """Search the web for persona research."""
    results: list[SearchResult] = []
    try:
        with DDGS() as ddgs:
            for item in ddgs.text(query, max_results=max_results):
                results.append(
                    SearchResult(
                        title=item.get("title", ""),
                        url=item.get("href", ""),
                        snippet=item.get("body", ""),
                    )
                )
    except Exception as exc:
        results.append(
            SearchResult(
                title="Search unavailable",
                url="",
                snippet=f"Could not complete search: {exc}",
            )
        )
    return results


def search_images(query: str, max_results: int = 12) -> list[dict[str, str]]:
    """Search for reference images online."""
    images: list[dict[str, str]] = []
    try:
        with DDGS() as ddgs:
            for item in ddgs.images(query, max_results=max_results):
                images.append(
                    {
                        "title": item.get("title", ""),
                        "url": item.get("image", "") or item.get("thumbnail", ""),
                        "source": item.get("source", ""),
                    }
                )
    except Exception as exc:
        images.append({"title": "Search failed", "url": "", "source": str(exc)})
    return [img for img in images if img.get("url")]


def format_research_summary(query: str, results: list[SearchResult]) -> str:
    """Turn search hits into a compact research brief."""
    if not results:
        return f"No web results found for: {query}"

    lines = [f"Research for: {query}", ""]
    for index, result in enumerate(results, start=1):
        lines.append(f"{index}. {result.title}")
        if result.snippet:
            lines.append(f"   {result.snippet}")
        if result.url:
            lines.append(f"   Source: {result.url}")
        lines.append("")
    return "\n".join(lines).strip()


def is_probably_image_url(url: str) -> bool:
    """Heuristic check for direct image URLs."""
    if not url:
        return False
    path = urlparse(url).path.lower()
    return bool(re.search(r"\.(png|jpe?g|webp|gif|bmp)(\?|$)", path))


def download_image(url: str, dest_path: str, timeout: float = 30.0) -> str:
    """Download an image from a URL to disk."""
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
    }
    with httpx.Client(follow_redirects=True, timeout=timeout, headers=headers) as client:
        response = client.get(url)
        response.raise_for_status()
        content_type = response.headers.get("content-type", "")
        if "image" not in content_type and not is_probably_image_url(url):
            raise ValueError("URL does not appear to be an image")

        path = Path(dest_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(response.content)
        return str(path)