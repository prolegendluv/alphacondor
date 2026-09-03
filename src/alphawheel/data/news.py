"""News data fetching for sentiment context."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Optional

from alpaca.data.historical.news import NewsClient
from alpaca.data.requests import NewsRequest

from alphawheel.config import AlphaWheelSettings

logger = logging.getLogger(__name__)


class NewsService:
    """Fetches recent news headlines for sentiment analysis."""

    def __init__(self, settings: AlphaWheelSettings):
        self.client = NewsClient(
            api_key=settings.alpaca_api_key,
            secret_key=settings.alpaca_secret_key,
        )

    def get_recent_news(self, symbol: str, limit: int = 10) -> list[dict]:
        """Fetch recent news articles for a symbol.

        Returns a list of dicts with 'headline', 'summary', 'created_at', 'source'.
        """
        try:
            req = NewsRequest(
                symbols=symbol,
                start=datetime.now() - timedelta(days=3),
                limit=limit,
                sort="desc",
            )
            news = self.client.get_news(req)

            # Support different versions of alpaca-py NewsSet
            raw_articles = []
            if hasattr(news, "data") and isinstance(news.data, dict) and "news" in news.data:
                raw_articles = news.data["news"]
            elif hasattr(news, "news"):
                raw_articles = news.news
            elif isinstance(news, dict) and "news" in news:
                raw_articles = news["news"]
            else:
                try:
                    raw_articles = list(news)
                except Exception:
                    raw_articles = []

            articles = []
            for article in raw_articles:
                headline = article.get("headline") if isinstance(article, dict) else getattr(article, "headline", "")
                summary = article.get("summary", "") if isinstance(article, dict) else getattr(article, "summary", "")
                created_at = article.get("created_at", "") if isinstance(article, dict) else getattr(article, "created_at", "")
                source = article.get("source", "unknown") if isinstance(article, dict) else getattr(article, "source", "unknown")

                articles.append(
                    {
                        "headline": headline,
                        "summary": summary or "",
                        "created_at": str(created_at),
                        "source": source or "unknown",
                    }
                )
            return articles

        except Exception as e:
            logger.warning(f"Failed to fetch news for {symbol}: {e}")
            return []

    def format_news_for_llm(self, articles: list[dict]) -> str:
        """Format news articles into a concise string for LLM context."""
        if not articles:
            return "No recent news available."

        lines = []
        for i, article in enumerate(articles[:5], 1):
            lines.append(f"{i}. [{article['source']}] {article['headline']}")
            if article['summary']:
                lines.append(f"   {article['summary'][:200]}")
        return "\n".join(lines)
