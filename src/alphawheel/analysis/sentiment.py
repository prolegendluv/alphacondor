"""Sentiment analysis using LLM for market context."""

from __future__ import annotations

import json
import logging
from typing import Optional

from alphawheel.config import AlphaWheelSettings

logger = logging.getLogger(__name__)


class SentimentAnalyzer:
    """Analyzes news sentiment using Google Gemini."""

    def __init__(self, settings: AlphaWheelSettings):
        self.settings = settings
        self._client = None

    @property
    def client(self):
        """Lazy-initialize the Gemini client."""
        if self._client is None:
            try:
                from google import genai
                self._client = genai.Client(api_key=self.settings.google_api_key)
            except ImportError:
                logger.error("google-genai not installed. Run: pip install google-genai")
                raise
        return self._client

    def analyze_sentiment(
        self,
        symbol: str,
        news_text: str,
    ) -> tuple[float, str]:
        """Analyze sentiment for a symbol based on news headlines.

        Args:
            symbol: Stock ticker symbol.
            news_text: Formatted news text.

        Returns:
            Tuple of (sentiment_score [-1.0 to 1.0], summary string).
        """
        if not news_text or news_text == "No recent news available.":
            return 0.0, "No news data available for sentiment analysis."

        if not self.settings.google_api_key:
            logger.warning("No Google API key configured, skipping sentiment analysis")
            return 0.0, "Sentiment analysis unavailable (no API key)."

        prompt = f"""Analyze the sentiment of the following news headlines for {symbol} stock.

News:
{news_text}

Respond with ONLY a JSON object (no markdown, no code fences):
{{
    "score": <float from -1.0 (very bearish) to 1.0 (very bullish)>,
    "summary": "<one sentence summary of overall sentiment and key themes>"
}}"""

        try:
            from google.genai import types
            config = types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=0.1,
            )
            response = self.client.models.generate_content(
                model=self.settings.llm_model,
                contents=prompt,
                config=config,
            )

            text = response.text.strip()
            # Strip markdown code fences if present
            if text.startswith("```"):
                text = text.split("\n", 1)[-1]
                text = text.rsplit("```", 1)[0].strip()

            result = json.loads(text)
            score = max(-1.0, min(1.0, float(result.get("score", 0.0))))
            summary = result.get("summary", "No summary available.")

            logger.info(f"Sentiment for {symbol}: score={score:.2f}, summary={summary}")
            return score, summary

        except Exception as e:
            logger.error(f"Sentiment analysis failed for {symbol}: {e}")
            return 0.0, f"Sentiment analysis failed: {str(e)}"
