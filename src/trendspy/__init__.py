"""
TrendsPy - A Python library for working with Google Trends.

This library provides a simple and convenient interface for accessing Google Trends data,
allowing you to analyze search trends, get real-time trending topics, and track interest
over time and regions.

Main components:
- Trends: Main client class for accessing Google Trends data
- BatchPeriod: Enum for specifying time periods in batch operations
- TrendKeyword: Class representing a trending search term with metadata
- NewsArticle: Class representing news articles related to trends

Project links:
    Homepage: https://github.com/Chiqo-ke/trendspy
    Repository: https://github.com/Chiqo-ke/trendspy.git
    Issues: https://github.com/Chiqo-ke/trendspy/issues
"""

from .client import Trends, BatchPeriod
from .trend_keyword import TrendKeyword, TrendKeywordLite
from .news_article import NewsArticle

__version__ = "0.2.0"
__all__ = ['Trends', 'BatchPeriod', 'TrendKeyword', 'TrendKeywordLite', 'NewsArticle', 'TrendList']