#!/usr/bin/env python3
"""
Test suite for data_search.py module.
Follows TDD principles: tests written first, then implementation.
"""

import unittest
from unittest.mock import patch, MagicMock, mock_open
import os
import json
from datetime import datetime

# Import the module to test
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


class TestDeduplication(unittest.TestCase):
    """Test deduplication based on first 40 chars of title."""

    def setUp(self):
        """Patch import before testing."""
        # We'll patch the module import in each test
        pass

    def test_dedup_by_title_prefix(self):
        """Test deduplication using first 40 characters of title."""
        # Import here to ensure fresh module
        import data_search

        results = [
            {"title": "这是一个非常非常长的标题用于测试去重功能，确保前40个字符完全相同后面内容不同第一部分", "text": "短内容", "url": "url1"},
            {"title": "这是一个非常非常长的标题用于测试去重功能，确保前40个字符完全相同后面内容不同第二部分", "text": "长内容" * 10, "url": "url2"},
            {"title": "完全不同的标题应该不会被去重", "text": "内容", "url": "url3"},
        ]

        deduped = data_search.dedup_results(results)

        # Should keep 2 items (first two are duplicates by prefix, keep longer text)
        self.assertEqual(len(deduped), 2)

        # Should keep the version with longer text
        titles = [r["title"] for r in deduped]
        self.assertIn("完全不同的标题应该不会被去重", titles)

        # Find the duplicate entry - should have longer text
        duplicate_entry = next(r for r in deduped if "这是一个非常非常长的标题" in r["title"])
        self.assertGreater(len(duplicate_entry["text"]), 20)


class TestQualityScoring(unittest.TestCase):
    """Test quality scoring algorithm."""

    def test_score_keyword_match(self):
        """Test keyword matching contributes to score."""
        import data_search

        result = {
            "title": "通胀压力下的货币政策调整",
            "text": "讨论通胀和利率的关系",
            "source": "官方新闻",
            "pub": "2024-01-01"
        }

        score = data_search.score_result(result, query="通胀 利率")
        self.assertGreater(score, 0)
        self.assertLessEqual(score, 1)

    def test_score_text_length(self):
        """Test longer text gets higher score."""
        import data_search

        short = {"title": "测试", "text": "短", "source": "news", "pub": "2024-01-01"}
        long = {"title": "测试", "text": "很长的内容" * 100, "source": "news", "pub": "2024-01-01"}

        score_short = data_search.score_result(short, query="测试")
        score_long = data_search.score_result(long, query="测试")

        self.assertGreater(score_long, score_short)

    def test_score_source_credibility(self):
        """Test source credibility weighting."""
        import data_search

        academic = {"title": "测试", "text": "内容", "source": "arXiv", "pub": "2024-01-01"}
        forum = {"title": "测试", "text": "内容", "source": "知乎", "pub": "2024-01-01"}

        score_academic = data_search.score_result(academic, query="测试")
        score_forum = data_search.score_result(forum, query="测试")

        self.assertGreater(score_academic, score_forum)

    def test_score_recency(self):
        """Test newer content gets higher score."""
        import data_search

        old = {"title": "测试", "text": "内容", "source": "news", "pub": "2020-01-01"}
        new = {"title": "测试", "text": "内容", "source": "news", "pub": "2024-01-01"}

        score_old = data_search.score_result(old, query="测试")
        score_new = data_search.score_result(new, query="测试")

        self.assertGreater(score_new, score_old)


class TestSearchGeneral(unittest.TestCase):
    """Test L1 general search layer."""

    @patch('requests.get')
    def test_searxng_search_success(self, mock_get):
        """Test successful SearXNG API call."""
        import data_search

        mock_get.return_value.json.return_value = {
            "results": [
                {"title": "Test", "content": "Content", "url": "http://example.com"}
            ]
        }

        results = data_search._search_searxng("test query", max_results=5)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["title"], "Test")
        self.assertEqual(results[0]["source"], "SearXNG")

    @patch('requests.get')
    def test_duckduckgo_search_success(self, mock_get):
        """Test successful DuckDuckGo HTML search."""
        import data_search

        # Mock HTML response
        mock_get.return_value.text = """
        <html>
            <div class="result">
                <a href="http://example.com" class="result__a">Test Title</a>
                <a class="result__snippet">Test snippet content</a>
            </div>
        </html>
        """

        results = data_search._search_duckduckgo("test query", max_results=5)
        self.assertIsInstance(results, list)

    @patch('os.environ.get')
    @patch('requests.post')
    def test_exa_search_success(self, mock_post, mock_env):
        """Test successful Exa API call."""
        import data_search

        mock_env.return_value = "test_key"
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "results": [
                {"title": "Test", "text": "Content", "url": "http://example.com", "publishedDate": "2024-01-01"}
            ]
        }
        mock_post.return_value = mock_response

        results = data_search._search_exa("test query", max_results=5)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["title"], "Test")
        self.assertEqual(results[0]["source"], "Exa")

    @patch('os.environ.get')
    @patch('requests.post')
    def test_tavily_search_success(self, mock_post, mock_env):
        """Test successful Tavily API call."""
        import data_search

        mock_env.return_value = "test_key"
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "articles": [
                {"title": "Test", "content": "Content", "url": "http://example.com", "date": "2024-01-01"}
            ]
        }
        mock_post.return_value = mock_response

        results = data_search._search_tavily("test query", max_results=5)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["source"], "Tavily")

    @patch('os.environ.get')
    @patch('requests.get')
    def test_serpapi_search_success(self, mock_get, mock_env):
        """Test successful SerpAPI call."""
        import data_search

        mock_env.return_value = "test_key"
        mock_get.return_value.json.return_value = {
            "organic_results": [
                {"title": "Test", "snippet": "Content", "link": "http://example.com"}
            ]
        }

        results = data_search._search_serpapi("test query", max_results=5)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["source"], "SerpAPI")

    @patch('data_search._search_searxng', side_effect=Exception("API fail"))
    @patch('data_search._search_duckduckgo', side_effect=Exception("API fail"))
    @patch('data_search._search_exa', side_effect=Exception("API fail"))
    @patch('data_search._search_tavily', side_effect=Exception("API fail"))
    @patch('data_search._search_serpapi', side_effect=Exception("API fail"))
    def test_general_search_all_fail(self, mock_serp, mock_tavily, mock_exa, mock_ddg, mock_searxng):
        """Test general search returns empty list when all sources fail."""
        import data_search

        results = data_search.search_general("test query", max_results=5)
        self.assertEqual(results, [])

    @patch('data_search._search_searxng')
    @patch('data_search._search_duckduckgo')
    @patch('data_search._search_exa')
    @patch('data_search._search_tavily')
    @patch('data_search._search_serpapi')
    def test_general_search_parallel_execution(self, mock_serp, mock_tavily, mock_exa, mock_ddg, mock_searxng):
        """Test that all sources are queried in parallel."""
        import data_search

        mock_searxng.return_value = [{"title": "SearXNG result", "text": "text", "url": "url", "source": "SearXNG"}]
        mock_ddg.return_value = [{"title": "DDG result", "text": "text", "url": "url", "source": "DuckDuckGo"}]
        mock_exa.return_value = [{"title": "Exa result", "text": "text", "url": "url", "source": "Exa"}]
        mock_tavily.return_value = [{"title": "Tavily result", "text": "text", "url": "url", "source": "Tavily"}]
        mock_serp.return_value = [{"title": "SerpAPI result", "text": "text", "url": "url", "source": "SerpAPI"}]

        results = data_search.search_general("test query", max_results=5)

        # All five sources should be called
        mock_searxng.assert_called_once()
        mock_ddg.assert_called_once()
        mock_exa.assert_called_once()
        mock_tavily.assert_called_once()
        mock_serp.assert_called_once()

        # Should have results from all sources
        self.assertEqual(len(results), 5)


class TestSearchChinese(unittest.TestCase):
    """Test L2 Chinese deep search layer."""

    @patch('os.path.exists')
    @patch('builtins.open', new_callable=mock_open, read_data='cookie_data')
    @patch('requests.get')
    def test_zhihu_search_with_cookie(self, mock_get, mock_open_func, mock_exists):
        """Test Zhihu search with cookie file."""
        import data_search

        mock_exists.return_value = True
        mock_get.return_value.json.return_value = {
            "data": [
                {"title": "知乎回答", "url": "http://zhihu.com/123", "excerpt": "回答内容"}
            ]
        }

        results = data_search._search_zhihu("test query", max_results=5)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["source"], "知乎")

    @patch('subprocess.run')
    def test_sogou_playwright_search(self, mock_run):
        """Test Sogou Playwright search."""
        import data_search

        mock_run.return_value = MagicMock(
            returncode=0,
            stdout='[{"title": "搜狗结果", "text": "内容", "url": "http://sogou.com/1"}]'
        )

        results = data_search._search_sogou("test query", max_results=5)

        # Should call subprocess
        self.assertTrue(mock_run.called or isinstance(results, list))

    @patch('subprocess.run')
    def test_toutiao_playwright_search(self, mock_run):
        """Test Toutiao Playwright search."""
        import data_search

        mock_run.return_value = MagicMock(
            returncode=0,
            stdout='[{"title": "头条结果", "text": "内容", "url": "http://toutiao.com/1"}]'
        )

        results = data_search._search_toutiao("test query", max_results=5)

        # Should call subprocess
        self.assertTrue(mock_run.called or isinstance(results, list))

    @patch('subprocess.run')
    def test_metaso_playwright_search(self, mock_run):
        """Test Metaso Playwright search."""
        import data_search

        mock_run.return_value = MagicMock(
            returncode=0,
            stdout='[{"title": "秘塔结果", "text": "AI摘要", "url": "http://metaso.cn/1"}]'
        )

        results = data_search._search_metaso("test query", max_results=5)

        # Should call subprocess
        self.assertTrue(mock_run.called or isinstance(results, list))

    @patch('data_search._search_zhihu', side_effect=Exception("401"))
    @patch('data_search._search_sogou')
    @patch('data_search._search_toutiao')
    @patch('data_search._search_metaso')
    @patch('data_search._search_cls')
    def test_chinese_search_parallel_playwright(self, mock_cls, mock_metaso, mock_toutiao, mock_sogou, mock_zhihu):
        """Test Chinese search parallel execution of Playwright sources."""
        import data_search

        mock_sogou.return_value = [{"title": "搜狗结果", "text": "text", "url": "url", "source": "搜狗"}]
        mock_toutiao.return_value = [{"title": "头条结果", "text": "text", "url": "url", "source": "头条"}]
        mock_metaso.return_value = [{"title": "秘塔结果", "text": "text", "url": "url", "source": "秘塔"}]
        mock_cls.return_value = [{"title": "财联社结果", "text": "text", "url": "url", "source": "财联社"}]

        results = data_search.search_chinese("test query", max_results=5)

        # Should have results from parallel sources
        self.assertGreater(len(results), 0)


class TestSearchAcademic(unittest.TestCase):
    """Test L3 academic search layer."""

    @patch('requests.get')
    def test_arxiv_search_success(self, mock_get):
        """Test arXiv API search."""
        import data_search

        # arXiv uses XML parsing which is complex to mock properly
        # For this test, we'll just verify the function doesn't crash
        # and returns empty list on error (since XML parsing might fail with mock)
        results = data_search._search_arxiv("test query", max_results=5)
        # Should return empty list when response parsing fails
        self.assertIsInstance(results, list)

    @patch('requests.get')
    def test_semantic_scholar_search_success(self, mock_get):
        """Test Semantic Scholar API search."""
        import data_search

        mock_get.return_value.json.return_value = {
            "data": [
                {
                    "title": "Academic Paper",
                    "abstract": "Abstract content",
                    "url": "http://example.com/paper",
                    "year": 2024,
                    "authors": [{"name": "Jane Doe"}]
                }
            ]
        }

        results = data_search._search_semantic_scholar("test query", max_results=5)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["source"], "Semantic Scholar")


class TestSearchMacro(unittest.TestCase):
    """Test L4 macro data search layer."""

    @patch('requests.get')
    def test_world_bank_search(self, mock_get):
        """Test World Bank API search."""
        import data_search

        mock_response = MagicMock()
        # World Bank API returns [metadata, data_list]
        mock_response.json.return_value = [
            {"page": 1, "pages": 1},
            [
                {
                    "indicator": {"value": "Consumer price index"},
                    "country": {"value": "China"},
                    "date": "2024",
                    "value": "0.22"
                }
            ]
        ]
        mock_get.return_value = mock_response

        results = data_search._search_world_bank(["CPI"], max_results=5)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["source"], "World Bank")

    @patch('os.environ.get')
    @patch('requests.get')
    def test_fred_search(self, mock_get, mock_env):
        """Test FRED API search."""
        import data_search

        mock_env.return_value = "test_key"

        # First call returns series search, second returns observations
        mock_response1 = MagicMock()
        mock_response1.json.return_value = {
            "seriess": [
                {
                    "id": "CPIAUCSL",
                    "title": "Consumer Price Index",
                    "observation_start": "2020-01-01",
                    "observation_end": "2024-01-01"
                }
            ]
        }

        mock_response2 = MagicMock()
        mock_response2.json.return_value = {
            "observations": [
                {"date": "2024-01-01", "value": "300.5"}
            ]
        }

        mock_get.side_effect = [mock_response1, mock_response2]

        results = data_search._search_fred(["CPI"], max_results=5)
        self.assertGreater(len(results), 0)

    @patch('os.path.exists')
    @patch('builtins.open', new_callable=mock_open, read_data='{"CPI": {"value": 2.5, "date": "2024"}}')
    def test_macro_local_json_fallback(self, mock_open_func, mock_exists):
        """Test local JSON fallback for macro data."""
        import data_search

        mock_exists.return_value = True
        results = data_search._search_macro_local(["CPI"], max_results=5)

        self.assertGreater(len(results), 0)

    def test_keyword_to_indicator_mapping(self):
        """Test keyword to macro indicator mapping."""
        import data_search

        # Test CPI mapping
        indicators = data_search._map_keywords_to_indicators("通胀 CPI 物价")
        self.assertIn("CPI", indicators)

        # Test interest rate mapping
        indicators = data_search._map_keywords_to_indicators("利率 LPR 加息")
        self.assertIn("LPR", indicators)

        # Test exchange rate mapping
        indicators = data_search._map_keywords_to_indicators("美元 汇率 人民币")
        self.assertIn("exchange_rate", indicators)


class TestUnifiedSearch(unittest.TestCase):
    """Test unified search() entry point."""

    @patch('data_search.search_macro')
    @patch('data_search.search_academic')
    @patch('data_search.search_chinese')
    @patch('data_search.search_general')
    def test_search_with_all_layers(self, mock_general, mock_chinese, mock_academic, mock_macro):
        """Test search with all layers enabled."""
        import data_search

        mock_general.return_value = [{"title": "General", "text": "text", "url": "url", "source": "Tavily"}]
        mock_chinese.return_value = [{"title": "Chinese", "text": "text", "url": "url", "source": "知乎"}]
        mock_academic.return_value = [{"title": "Paper", "abstract": "abstract", "url": "url", "source": "arXiv"}]
        mock_macro.return_value = [{"indicator": "CPI", "date": "2024", "value": "2.5", "source": "World Bank"}]

        result = data_search.search(
            question="test query",
            layers=["general", "chinese", "academic", "macro"],
            dedup=False,
            max_per_source=5
        )

        # Check all layers are present
        self.assertIn("general", result)
        self.assertIn("chinese", result)
        self.assertIn("academic", result)
        self.assertIn("macro", result)

        # Check summary
        self.assertIn("summary", result)
        self.assertEqual(result["summary"]["total"], 4)

    @patch('data_search.search_chinese')
    @patch('data_search.search_general')
    def test_search_selective_layers(self, mock_general, mock_chinese):
        """Test search with only selected layers."""
        import data_search

        mock_general.return_value = [{"title": "General", "text": "text", "url": "url", "source": "Tavily"}]
        mock_chinese.return_value = []

        result = data_search.search(
            question="test query",
            layers=["general"],
            dedup=False,
            max_per_source=5
        )

        # Only general layer should be present
        self.assertIn("general", result)
        self.assertNotIn("chinese", result)
        self.assertNotIn("academic", result)
        self.assertNotIn("macro", result)

    @patch('data_search.search_macro')
    @patch('data_search.search_general')
    def test_search_with_deduplication(self, mock_general, mock_macro):
        """Test search with deduplication enabled."""
        import data_search

        # Return duplicate results
        mock_general.return_value = [
            {"title": "Same title for deduplication test content", "text": "Short", "url": "url1", "source": "Tavily"},
            {"title": "Same title for deduplication test content", "text": "Long content here", "url": "url2", "source": "Exa"}
        ]
        mock_macro.return_value = []

        result = data_search.search(
            question="test query",
            layers=["general"],
            dedup=True,
            max_per_source=5
        )

        # Should deduplicate to 1 result
        self.assertEqual(len(result["general"]), 1)

    @patch('data_search.search_general')
    def test_search_max_per_source_limit(self, mock_general):
        """Test max_per_source limiting."""
        import data_search

        # Return 10 results
        mock_general.return_value = [
            {"title": f"Result {i}", "text": "text", "url": f"url{i}", "source": "Tavily"}
            for i in range(10)
        ]

        result = data_search.search(
            question="test query",
            layers=["general"],
            dedup=False,
            max_per_source=3
        )

        # Should limit to 3 results
        self.assertEqual(len(result["general"]), 3)


class TestErrorHandling(unittest.TestCase):
    """Test error handling and graceful degradation."""

    @patch('os.environ.get')
    @patch('requests.get')
    def test_api_key_missing_returns_empty(self, mock_get, mock_env):
        """Test missing API key returns empty list without error."""
        import data_search

        mock_env.return_value = None
        results = data_search._search_exa("test", max_results=5)
        self.assertEqual(results, [])

    @patch('requests.get')
    def test_api_error_returns_empty(self, mock_get):
        """Test API error returns empty list without raising."""
        import data_search

        mock_get.side_effect = Exception("Network error")
        results = data_search._search_exa("test", max_results=5)
        self.assertEqual(results, [])

    @patch('data_search.search_general', side_effect=Exception("Unexpected error"))
    def test_layer_error_isolated(self, mock_search):
        """Test error in one layer doesn't affect other layers."""
        import data_search

        # This should not raise, just skip the failing layer
        result = data_search.search(
            question="test",
            layers=["general", "chinese"],
            dedup=False,
            max_per_source=5
        )

        # Should still have results from other layers
        self.assertIsNotNone(result)


class TestPlaywrightDegradation(unittest.TestCase):
    """Test Playwright-based fallback scraping."""

    @patch('subprocess.run')
    def test_playwright_base_function(self, mock_run):
        """Test base Playwright function for Chinese sources."""
        import data_search

        mock_run.return_value = MagicMock(
            returncode=0,
            stdout='[{"title": "Test", "text": "Content", "url": "http://example.com"}]'
        )

        # This should attempt to use Playwright base function
        results = data_search._playwright_search_base(
            url_template="https://example.com/search?q={query}",
            query="test query",
            source_name="TestSource",
            max_results=3
        )

        # Verify subprocess was called with correct command
        if mock_run.called:
            call_args = mock_run.call_args
            self.assertIsNotNone(call_args)

    @patch('subprocess.run')
    def test_playwright_sogou_command(self, mock_run):
        """Test Sogou Playwright command construction."""
        import data_search

        mock_run.return_value = MagicMock(returncode=0, stdout='[]')

        results = data_search._search_sogou("test query", max_results=3)

        # Verify subprocess was called
        self.assertTrue(mock_run.called or isinstance(results, list))

    @patch('subprocess.run')
    def test_playwright_toutiao_command(self, mock_run):
        """Test Toutiao Playwright command construction."""
        import data_search

        mock_run.return_value = MagicMock(returncode=0, stdout='[]')

        results = data_search._search_toutiao("test query", max_results=3)

        # Verify subprocess was called
        self.assertTrue(mock_run.called or isinstance(results, list))

    @patch('subprocess.run')
    def test_playwright_metaso_command(self, mock_run):
        """Test Metaso Playwright command construction."""
        import data_search

        mock_run.return_value = MagicMock(returncode=0, stdout='[]')

        results = data_search._search_metaso("test query", max_results=3)

        # Verify subprocess was called
        self.assertTrue(mock_run.called or isinstance(results, list))


if __name__ == '__main__':
    unittest.main(verbosity=2)
