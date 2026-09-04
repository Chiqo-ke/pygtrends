import re
import json
import requests
import pandas as pd
import numpy as np
from enum import Enum
from typing import Dict, List, Optional
from urllib.parse import quote, quote_plus
from .utils import *
from .converter import TrendsDataConverter
from .trend_keyword import *
from .news_article import *
from .timeframe_utils import convert_timeframe, check_timeframe_resolution
from .hierarchical_search import create_hierarchical_index
from .trend_list import TrendList
from .authenticated_trends import (
	AuthenticatedExploreSession,
	build_explore_url,
	extract_from_captured_events,
	normalize_related_query_tables,
)
from time import sleep,time

class TrendsQuotaExceededError(Exception):
    """Raised when the Google Trends API quota is exceeded for related queries/topics."""
    def __init__(self):
        super().__init__(
            "API quota exceeded for related queries/topics. "
            "To resolve this, you can try:\n"
            "1. Use a different referer in request headers:\n"
            "   tr.related_queries(keyword, headers={'referer': 'https://www.google.com/'})\n"
            "2. Use a different IP address by configuring a proxy:\n"
            "   tr.set_proxy('http://proxy:port')\n"
            "   # or\n"
            "   tr = Trends(proxy={'http': 'http://proxy:port', 'https': 'https://proxy:port'})\n"
            "3. Wait before making additional requests"
        )

class BatchPeriod(Enum): # update every 2 min
	'''
	Time periods for batch operations.
	'''
	Past4H  = 2 #31 points (new points every 8 min)
	Past24H = 3 #91 points (every 16 min)
	Past48H = 5 #181 points (every 16 min)
	Past7D  = 4 #43 points (every 4 hours) 
	
BATCH_URL			    = f'https://trends.google.com/_/TrendsUi/data/batchexecute'
HOT_TRENDS_URL          = f'https://trends.google.com/trends/hottrends/visualize/internal/data'

# ----------- API LINKS -------------
API_URL  				= f'https://trends.google.com/trends/api'
API_EXPLORE_URL 		= f'{API_URL}/explore'
API_GEO_DATA_URL        = f'{API_URL}/explore/pickers/geo'
API_CATEGORY_URL        = f'{API_URL}/explore/pickers/category'
API_TOPCHARTS_URL       = f'{API_URL}/topcharts'
API_AUTOCOMPLETE        = f'{API_URL}/autocomplete/'
DAILY_SEARCHES_URL 		= f'{API_URL}/dailytrends'
REALTIME_SEARCHES_URL   = f'{API_URL}/realtimetrends'

API_TOKEN_URL 			= f'https://trends.google.com/trends/api/widgetdata'
API_TIMELINE_URL 		= f'{API_TOKEN_URL}/multiline'
API_MULTIRANGE_URL 		= f'{API_TOKEN_URL}/multirange'
API_GEO_URL 			= f'{API_TOKEN_URL}/comparedgeo'
API_RELATED_QUERIES_URL = f'{API_TOKEN_URL}/relatedsearches'

# ----------- EMBED LINKS -------------
EMBED_URL               = f'https://trends.google.com/trends/embed/explore'
EMBED_GEO_URL           = f'{EMBED_URL}/GEO_MAP'
EMBED_TOPICS_URL        = f'{EMBED_URL}/RELATED_TOPICS'
EMBED_QUERIES_URL       = f'{EMBED_URL}/RELATED_QUERIES'
EMBED_TIMESERIES_URL    = f'{EMBED_URL}/TIMESERIES'

# --------------- RSS ----------------- 
DAILY_RSS 				= f'https://trends.google.com/trends/trendingsearches/daily/rss'
REALTIME_RSS            = f'https://trends.google.com/trending/rss'

class Trends:
	"""
	A client for accessing Google Trends data.

	This class provides methods to analyze search trends, get real-time trending topics,
	and track interest over time and regions.

	Parameters:
		hl (str): Language and country code (e.g., 'en-US'). Defaults to 'en-US'.
		tzs (int): Timezone offset in minutes. Defaults to current system timezone.
		use_entity_names (bool): Whether to use entity names instead of keywords. 
			Defaults to False.
		proxy (str or dict): Proxy configuration. Can be a string URL or a dictionary
			with protocol-specific proxies. Examples:
			- "http://user:pass@10.10.1.10:3128"
			- {"http": "http://10.10.1.10:3128", "https": "http://10.10.1.10:1080"}
	"""
		
	def __init__(self, language='en', tzs=360, request_delay=1., max_retries=3, use_enitity_names = False, proxy=None, **kwargs):
		"""
		Initialize the Trends client.
		
		Args:
			language (str): Language code (e.g., 'en', 'es', 'fr').
			tzs (int): Timezone offset in minutes. Defaults to 360.
			request_delay (float): Minimum time interval between requests in seconds. Helps avoid hitting rate limits and behaving like a bot. Set to 0 to disable.
			max_retries (int): Maximum number of retry attempts for failed requests. Each retry includes exponential backoff delay of 2^(max_retries-retries) seconds for rate limit errors (429, 302).
			use_enitity_names (bool): Whether to use entity names instead of keywords.
			proxy (str or dict): Proxy configuration.
			**kwargs: Additional arguments for backwards compatibility.
				- hl (str, deprecated): Old-style language code (e.g., 'en' or 'en-US').
				If provided, will be used as fallback when language is invalid.
		"""
		if isinstance(language, str) and len(language) >= 2:
			self.language = language[:2].lower()
		elif 'hl' in kwargs and isinstance(kwargs['hl'], str) and len(kwargs['hl']) >= 2:
			self.language = kwargs['hl'][:2].lower()
		else:
			self.language = 'en'
	
		# self.hl = hl
		self.tzs = tzs or -int(datetime.now().astimezone().utcoffset().total_seconds()/60)
		self._default_params = {'hl': self.language, 'tz': tzs}
		self.use_enitity_names = use_enitity_names
		self.session = requests.session()
		self._headers = {'accept-language': self.language}
		self._geo_cache = {}
		self._category_cache = {}  # Add category cache
		self.request_delay = request_delay
		self.max_retires = max_retries
		self.last_request_times = {0,1}
		# Initialize proxy configuration
		self.set_proxy(proxy)
	
	def set_proxy(self, proxy=None):
		"""
		Set or update proxy configuration for the session.

		Args:
			proxy (str or dict, optional): Proxy configuration. Can be:
				- None: Remove proxy configuration
				- str: URL for all protocols (e.g., "http://10.10.1.10:3128")
				- dict: Protocol-specific proxies (e.g., {"http": "...", "https": "..."})
		"""
		if isinstance(proxy, str):
			# Convert string URL to dictionary format
			proxy = {
				'http': proxy,
				'https': proxy
			}
		
		# Update session's proxy configuration
		self.session.proxies.clear()
		if proxy:
			self.session.proxies.update(proxy)

	def _extract_keywords_from_token(self, token):
		if self.use_enitity_names:
			return [item['text'] for item in token['bullets']]
		else :
			return [item['complexKeywordsRestriction']['keyword'][0]['value'] for item in token['request']['comparisonItem']]

	@staticmethod
	def _parse_protected_json(response: requests.models.Response):
		"""
		Parses JSON data from a protected API response.

		Args:
			response (requests.models.Response): Response object from requests

		Returns:
			dict: Parsed JSON data

		Raises:
			ValueError: If response status is not 200, content type is invalid,
					or JSON parsing fails
		"""
		valid_content_types = {'application/json', 'application/javascript', 'text/javascript'}
		content_type = response.headers.get('Content-Type', '').split(';')[0].strip().lower()
		
		if (response.status_code != 200) or (content_type not in valid_content_types):
			raise ValueError(f"Invalid response: status {response.status_code}, content type '{content_type}'")

		try:
			json_data = response.text.split('\n')[-1]
			return json.loads(json_data)
		except json.JSONDecodeError:
			raise ValueError("Failed to parse JSON data")

	def _encode_items(self, keywords, timeframe="today 12-m", geo=''):
		data = list(map(ensure_list, [keywords, timeframe, geo]))
		lengths = list(map(len, data))
		max_len = max(lengths)
		if not all(max_len % length == 0 for length in lengths):
			raise ValueError(f"Ambiguous input sizes: unable to determine how to combine inputs of lengths {lengths}")
		data = [item * (max_len // len(item)) for item in data]
		items = [dict(zip(['keyword', 'time', 'geo'], values)) for values in zip(*data)]
		return items

	def _encode_request(self, params):
		if 'keyword' in params:
			keywords = ensure_list(params.pop('keyword'))
			if len(keywords) != 1:
				raise ValueError("This endpoint only supports a single keyword")
			params['keywords'] = keywords

		items = self._encode_items(
			keywords  = params['keywords'],
			timeframe = params.get('timeframe', "today 12-m"),
			geo		  = params.get('geo', '')
		)
		
		req = {'req': json.dumps({
			'comparisonItem': items,
			'category': params.get('cat', 0),
			'property': params.get('gprop', '')
		})}

		req.update(self._default_params)
		return req

	def _get(self, url, params=None, headers=None):
		"""
		Make HTTP GET request with retry logic and proxy support.
		
		Args:
			url (str): URL to request
			params (dict, optional): Query parameters
			
		Returns:
			requests.Response: Response object
			
		Raises:
			ValueError: If response status code is not 200
			requests.exceptions.RequestException: For network-related errors
		"""
		retries = self.max_retires
		response_code = 429
		response_codes = []
		last_response = None
		req = None
		while (retries > 0):
			try:

				if self.request_delay:
					min_time = min(self.last_request_times)
					sleep_time = max(0, self.request_delay - (time() - min_time))
					sleep(sleep_time)
					self.last_request_times = (self.last_request_times - {min_time,}) | {time(),}

				req = self.session.get(url, params=params, headers=headers)
				last_response = req
				response_code = req.status_code
				response_codes.append(response_code)

				if response_code == 200:
					return req
				else:
					if response_code in {429,302}:
						sleep(2**(self.max_retires-retries))
					retries -= 1
				
			except Exception as e:
				if retries == 0:
					raise
				retries -= 1

		if response_codes.count(429) > len(response_codes) / 2:
			current_delay = self.request_delay or 1
			print(f"\nWarning: Too many rate limit errors (429). Consider increasing request_delay "
				f"to Trends(request_delay={current_delay*2}) before Google implements a long-term "
				f"rate limit!")
		last_response.raise_for_status()

	@classmethod
	def _extract_embedded_data(cls, text):
		pattern = re.compile(r"JSON\.parse\('([^']+)'\)")
		matches = pattern.findall(text)
		# If matches found, decode and return result
		if matches:
			return json.loads(decode_escape_text(matches[0]))  # Take first match
		print("Failed to extract JSON data")

	def _token_to_data(self, token):
		URL = {
			'fe_line_chart': 		API_TIMELINE_URL,
			'fe_multi_range_chart':	API_MULTIRANGE_URL,
			'fe_multi_heat_map':    API_GEO_URL,
			'fe_geo_chart_explore': API_GEO_URL,
			'fe_related_searches':	API_RELATED_QUERIES_URL
		}[token['type']]

		params = {'req': json.dumps(token['request']), 'token': token['token']}
		params.update(self._default_params)
		# req    = self.session.get(URL, params=params)
		req    = self._get(URL, params=params)
		data   = Trends._parse_protected_json(req)
		return data

	def _get_token_data(self, url, params=None, request_fix=None, headers=None, raise_quota_error=False):
		"""
		Internal method to get token data from Google Trends API.
		
		Handles both 'keyword' and 'keywords' parameters for backward compatibility
		and convenience.
		"""

		params 	= self._encode_request(params)
		req 	= self._get(url, params=params, headers=headers)
		token 	= self._extract_embedded_data(req.text)

		if request_fix is not None:
			token = {**token, 'request':{**token['request'], **request_fix}}

		if raise_quota_error:
			user_type = token.get('request', {}).get('userConfig', {}).get('userType', '')
			if user_type == "USER_TYPE_EMBED_OVER_QUOTA":
				raise TrendsQuotaExceededError()

		data 	= self._token_to_data(token)
		return token, data

	def _get_batch(self, req_id, data):
		req_data = json.dumps([[[req_id,f"{json.dumps(data)}", None,"generic"]]])
		post_data  = f'f.req={req_data}'
		headers = {
			"content-type": "application/x-www-form-urlencoded;charset=UTF-8"
		}
		req = self.session.post(BATCH_URL, post_data, headers=headers)
		return req

	def interest_over_time(self, keywords, timeframe="today 12-m", geo='', cat=0, gprop='', return_raw = False, headers=None):
		"""
		Retrieves interest over time data for specified keywords.
		
		Parameters:
			keywords (str or list): Keywords to analyze.
			timeframe : str or list
				Defines the time range for querying interest over time. It can be specified as a single string or a list. 
				Supported formats include:

				- 'now 1-H', 'now 4-H', 'now 1-d', 'now 7-d'
				- 'today 1-m', 'today 3-m', 'today 12-m', 'today 5-y'
				- 'all' for all available data
				- 'YYYY-MM-DD YYYY-MM-DD' for specific date ranges
				- 'YYYY-MM-DDTHH YYYY-MM-DDTHH' for hourly data (if less than 8 days)

				Additional flexible formats:
				
				1. **'now {offset}'**: Timeframes less than 8 days (e.g., 'now 72-H' for the last 72 hours).
				2. **'today {offset}'**: Larger periods starting from today (e.g., 'today 5-m' for the last 5 months).
				3. **'date {offset}'**: Specific date with offset (e.g., '2024-03-25 5-m' for 5 months back from March 25, 2024).

				**Note:** Offsets always go backward in time.

				Resolutions based on timeframe length:
				
				- `< 5 hours`: 1 minute
				- `5 hours <= delta < 36 hours`: 8 minutes
				- `36 hours <= delta < 72 hours`: 16 minutes
				- `72 hours <= delta < 8 days`: 1 hour
				- `8 days <= delta < 270 days`: 1 day
				- `270 days <= delta < 1900 days`: 1 week
				- `>= 1900 days`: 1 month

				Restrictions:
				- **Same resolution**: All timeframes must have the same resolution.
				- **Timeframe length**: Maximum timeframe cannot be more than twice the length of the minimum timeframe.
			geo (str): Geographic location code (e.g., 'US' for United States).
			cat (int): Category ID. Defaults to 0 (all categories).
			gprop (str): Google property filter.
			return_raw (bool): If True, returns raw API response.

		Returns:
			pandas.DataFrame or raw API response
			Processed trending keywords data or raw API data if `return_raw=True`
		"""
		check_timeframe_resolution(timeframe)
		timeframe = list(map(convert_timeframe, ensure_list(timeframe)))

		token, data = self._get_token_data(EMBED_TIMESERIES_URL, locals(), headers=headers)
		if return_raw:
			return token, data

		if token['type']=='fe_line_chart':
			keywords = self._extract_keywords_from_token(token)
			return TrendsDataConverter.interest_over_time(data, keywords=keywords)
		if token['type']=='fe_multi_range_chart':
			bullets = TrendsDataConverter.token_to_bullets(token)
			return TrendsDataConverter.multirange_interest_over_time(data, bullets=bullets)
		return data
	
	def related_queries(self, keyword, timeframe="today 12-m", geo='', cat=0, gprop='', return_raw = False, headers=None):
		"""
        Retrieves related queries for a single search term.
        
        Args:
            keyword (str): A single keyword to analyze
            timeframe (str): Time range for analysis
            geo (str): Geographic location code
            cat (int): Category ID
            gprop (str): Google property filter
            return_raw (bool): If True, returns raw API response
            headers (dict, optional): Custom request headers. Can be used to set different referer
                                    to help bypass quota limits
        
        Raises:
            TrendsQuotaExceededError: When API quota is exceeded
			
		Parameters:
			dict: Two DataFrames containing 'top' and 'rising' related queries
			
		Example:
			>>> tr = Trends()
			>>> related = tr.related_queries('python')
			>>> print("Top queries:")
			>>> print(related['top'])
			>>> print("\nRising queries:")
			>>> print(related['rising'])
		"""
		headers = headers or {"referer": "https://trends.google.com/trends/explore"}
		token, data = self._get_token_data(EMBED_QUERIES_URL, locals(), headers=headers, raise_quota_error=True)
		if return_raw:
			return token, data
		return TrendsDataConverter.related_queries(data)

	def get_related_query_tables(self, query, start_date, end_date, geo="Worldwide",
								search_type="web", debugger_url="http://127.0.0.1:9222",
								return_raw=False, download_dir=None):
		"""Extract authenticated Explore Top and Rising Related Queries via CDP."""
		explore_url = build_explore_url(query, start_date, end_date, geo, search_type)
		self.last_explore_session = AuthenticatedExploreSession(debugger_url)
		events = self.last_explore_session.capture(explore_url, download_dir=download_dir)
		tables = extract_from_captured_events(events)
		return tables if return_raw else normalize_related_query_tables(tables)

	def get_related_queries(self, query, start_date, end_date, geo="Worldwide",
							search_type="web", debugger_url="http://127.0.0.1:9222"):
		"""Return normalized rows from both authenticated Explore query tables."""
		tables = self.get_related_query_tables(
			query, start_date, end_date, geo=geo, search_type=search_type,
			debugger_url=debugger_url,
		)
		return tables["top"] + tables["rising"]

	def retry_missing_downloads(self, labels, download_dir=None, delay=3.0):
		"""Retry only browser CSV buttons whose exports were not observed."""
		return self.last_explore_session.retry_missing_downloads(labels, download_dir, delay)
	
	def related_topics(self, keyword, timeframe="today 12-m", geo='', cat=0, gprop='', return_raw = False, headers=None):
		"""
		Retrieves related topics for a single search term.
		
		Parameters:
            keyword (str): A single keyword to analyze
            timeframe (str): Time range for analysis
            geo (str): Geographic location code
            cat (int): Category ID
            gprop (str): Google property filter
            return_raw (bool): If True, returns raw API response
            headers (dict, optional): Custom request headers. Can be used to set different referer
                                    to help bypass quota limits
        
        Raises:
            TrendsQuotaExceededError: When API quota is exceeded
			
		Example:
			>>> tr = Trends()
			>>> related = tr.related_topics('python')
			>>> print("Top topics:")
			>>> print(related['top'])
			>>> print("\nRising topics:")
			>>> print(related['rising'])
		"""
		headers = headers or {"referer": "https://trends.google.com/trends/explore"}
		token, data = self._get_token_data(EMBED_TOPICS_URL, locals(), headers=headers, raise_quota_error=True)
		if return_raw:
			return token, data
		return TrendsDataConverter.related_queries(data)


	def interest_by_region(self, keywords, timeframe="today 12-m", geo='', cat=0, gprop='', resolution=None, inc_low_vol=False, return_raw=False):
		"""
		Retrieves geographical interest data based on keywords and other parameters.

		Parameters:
			keywords (str or list): Search keywords to analyze.
			timeframe (str): Time range for analysis (e.g., "today 12-m", "2022-01-01 2022-12-31")
			geo (str): Geographic region code (e.g., "US" for United States)
			cat (int): Category ID (default: 0 for all categories)
			gprop (str): Google property filter
			resolution (str): Geographic resolution level:
				- 'COUNTRY' (default when geo is empty)
				- 'REGION' (states/provinces)
				- 'CITY' (cities)
				- 'DMA' (Designated Market Areas)
			inc_low_vol (bool): Include regions with low search volume
			return_raw (bool): Return unprocessed API response data

		Returns:
			pandas.DataFrame or dict: Processed geographic interest data, or raw API response if return_raw=True
		"""
		if (not resolution):
			resolution = 'COUNTRY' if ((geo=='') or (not geo)) else 'REGION'

		data_injection = {'resolution': resolution, 'includeLowSearchVolumeGeos': inc_low_vol}
		token, data = self._get_token_data(EMBED_GEO_URL, locals(), request_fix=data_injection)
		if return_raw:
			return token, data
		
		bullets = TrendsDataConverter.token_to_bullets(token)
		return TrendsDataConverter.geo_data(data, bullets)
	
	def suggestions(self, keyword, language=None, return_raw=False):
		params = {'hz':language, 'tz':self.tzs} if language else self._default_params
		encoded_keyword = keyword.replace("'", "")
		encoded_keyword = quote(encoded_keyword, safe='-')
		req  = self._get(API_AUTOCOMPLETE+encoded_keyword, params)
		data = self._parse_protected_json(req)
		if return_raw:
			return data
		return TrendsDataConverter.suggestions(data)

	def hot_trends(self):
		req = self.session.get(HOT_TRENDS_URL)
		return json.loads(req.text)

	def top_year_charts(self, year='2023', geo='GLOBAL'):
		"""
		https://trends.google.com/trends/yis/2023/GLOBAL/
		"""
		params = {'date':year, 'geo':geo, 'isMobile':False}
		params.update(self._default_params)
		req = self._get(API_TOPCHARTS_URL, params)
		data = self._parse_protected_json(req)
		return data

	def trending_stories(self, geo='US', category='all', max_stories=200, return_raw=False):
		'''
		Old API
		category: all: "all",  business: "b",  entertainment: "e",  health: "m",  sicTech: "t",  sports: "s",  top: "h"
		'''
		forms = {'ns': 15, 'geo': geo, 'tz': self.tzs, 'hl': 'en', 'cat': category, 'fi' : '0', 'fs' : '0', 'ri' : max_stories, 'rs' : max_stories, 'sort' : 0}
		url = 'https://trends.google.com/trends/api/realtimetrends'
		req = self._get(url, forms)
		data = self._parse_protected_json(req)
		if return_raw:
			return data
		
		data = data.get('storySummaries', {}).get('trendingStories', [])
		data = [TrendKeywordLite.from_api(item) for item in data]
		return data

	def daily_trends_deprecated(self, geo='US', return_raw=False):
		params = {'ns': 15, 'geo': geo, 'hl':'en'}
		params.update(self._default_params)
		req = self._get(DAILY_SEARCHES_URL, params)
		data = self._parse_protected_json(req)
		if return_raw:
			return data
		data = data.get('default', {}).get('trendingSearchesDays', [])
		data = [TrendKeywordLite.from_api(item) for day in data for item in day['trendingSearches']]
		return data

	def daily_trends_deprecated_by_rss(self, geo='US', safe=True, return_raw=False):
		'''
		Only last 20 daily news
		'''

		params = {'geo':geo, 'safe':safe}
		req  = self._get(DAILY_RSS, params)
		if return_raw:
			return req.text
		data = TrendsDataConverter.rss_items(req.text)
		data = list(map(TrendKeywordLite.from_api, data))
		return data
	
	def trending_now(self, geo='US', language='en', hours=24, num_news=0, return_raw=False):
		"""
		Retrieves trending keywords that have seen significant growth in popularity within the last specified number of hours.

		Parameters:
		-----------
		geo : str, optional
			The geographical region for the trends, default is 'US' (United States).
		language : str, optional
			The language of the trends, default is 'en' (English).
		hours : int, optional
			The time window (in hours) for detecting trending keywords. Minimum value is 1, and the maximum is 191. Default is 24.
		num_news : int, optional
			NOT RECOMMENDED to use as it significantly slows down the function. The feature for fetching news associated with the trends is rarely used on the platform. 
			If you want trending keywords with news, consider using `trending_now_by_rss` instead. Default is 0.
		return_raw : bool, optional
			If set to True, the function returns the raw data directly from the API. Default is False, meaning processed data will be returned.

		Returns:
		--------
		dict or raw API response
			Processed trending keywords data or raw API data if `return_raw=True`.
		"""
		req_data = [None, None, geo, num_news, language, hours, 1]
		req = self._get_batch('i0OFE', req_data)
		data = self._parse_protected_json(req)
		if return_raw:
			return data

		data = json.loads(data[0][2])
		data = TrendList(map(TrendKeyword, data[1]))
		return data

	def trending_now_by_rss(self, geo='US', return_raw=False):
		"""
		Retrieves trending keywords from the RSS feed for a specified geographical region.

		Parameters:
		-----------
		geo : str, optional
			The geographical region for the trends, default is 'US' (United States).
		return_raw : bool, optional
			If set to True, the function returns the raw data directly from the API. Default is False, meaning processed data will be returned.

		Returns:
		--------
		Union[dict, List[TrendKeywordLite]]
			A dictionary with raw RSS feed data if `return_raw=True`, or a list of `TrendKeyword` objects otherwise.
		"""
		params = {'geo':geo}
		req  = self._get(REALTIME_RSS, params)
		if return_raw:
			return req.text
		data = TrendsDataConverter.rss_items(req.text)
		data = list(map(TrendKeywordLite.from_api, data))
		return data
	
	def trending_now_news_by_ids(self, news_ids, max_news=3, return_raw=False):
		req = self._get_batch('w4opAf', [news_ids, max_news])
		data = self._parse_protected_json(req)
		if return_raw:
			return data

		data = json.loads(data[0][2])
		data = list(map(NewsArticle.from_api, data[0]))
		return data
	
	def trending_now_showcase_timeline(self, keywords, geo='US', timeframe=BatchPeriod.Past24H, return_raw=False):
		req_data = [None,None,[[geo, keyword, timeframe.value, 0, 3] for keyword in keywords]]
		request_timestamp = int(datetime.now(timezone.utc).timestamp())
		req  = self._get_batch('jpdkv', req_data)
		data = self._parse_protected_json(req)
		if return_raw:
			return data
		
		data = json.loads(data[0][2])[0]
		data = TrendsDataConverter.trending_now_showcase_timeline(data, request_timestamp)
		return data
	
	def categories(self, find: str = None, language: str = None) -> List[dict]:
		"""
		Search for categories in Google Trends data.
		
		This function retrieves and caches category data from Google Trends API, then performs
		a partial search on the categories. The results are cached by language to minimize API calls.
		
		Args:
			find (str, optional): Search query for categories. If None or empty string,
				returns all available categories. Defaults to None.
			language (str, optional): Language code for the response (e.g., 'en', 'es').
				If None, uses the instance's default language. Defaults to None.
		
		Returns:
			List[dict]: List of matching categories. Each category is a dictionary containing:
				- name (str): Category name in the specified language
				- id (str): Category identifier
		
		Examples:
			>>> trends = Trends()
			>>> # Find all categories containing "computer"
			>>> computer_cats = trends.categories(find="computer")
			>>> # Find all categories in Spanish
			>>> spanish_cats = trends.categories(language="es")
			>>> # Find specific category in German
			>>> tech_cats = trends.categories(find="Technologie", language="de")
		"""
		cur_language = language or self.language
		
		if cur_language not in self._category_cache:
			req = self._get(API_CATEGORY_URL, {'hl': cur_language, 'tz': self.tzs})
			data = self._parse_protected_json(req)
			self._category_cache[cur_language] = create_hierarchical_index(data, join_ids=False)
		
		if not find:
			return list(self._category_cache[cur_language].name_to_item.values())
			
		return self._category_cache[cur_language].partial_search(find)

	def geo(self, find: str = None, language: str = None) -> List[dict]:
		"""
		Search for geographical locations in Google Trends data.
		
		This function retrieves and caches geographical data from Google Trends API, then performs
		a partial search on the locations. The results are cached by language to minimize API calls.
		
		Args:
			find (str, optional): Search query for locations. If None or empty string,
				returns all available locations. Defaults to None.
			language (str, optional): Language code for the response (e.g., 'en', 'es').
				If None, uses the instance's default language. Defaults to None.
		
		Returns:
			List[dict]: List of matching locations. Each location is a dictionary containing:
				- name (str): Location name in the specified language
				- id (str): Location identifier (e.g., 'US-NY' for New York, United States)
		
		Examples:
			>>> trends = GoogleTrends()
			>>> # Find all locations containing "York"
			>>> locations = trends.geo(find="York")
			>>> # Find all locations in Spanish
			>>> spanish_locations = trends.geo(language="es")
			>>> # Find specific location in German
			>>> berlin = trends.geo(find="Berlin", language="de")
		
		Note:
			- Results are cached by language to improve performance
			- API response is parsed and structured for efficient searching
			- Case-insensitive partial matching is used for searches
		"""
		# Use provided language or fall back to instance default
		cur_language = language or self.language
		
		# Check if we need to fetch and cache data for this language
		if cur_language not in self._geo_cache:
			# Fetch geographical data from Google Trends API
			data = self._get(API_GEO_DATA_URL,
							{'hl': cur_language, 'tz': self.tzs})
			data = self._parse_protected_json(data)
			# Create and cache search system for this language
			self._geo_cache[cur_language] = create_hierarchical_index(data)
		
		# Perform partial search (empty string returns all locations)
		if not find:
			return list(self._geo_cache[cur_language].name_to_location.values())
			
		return self._geo_cache[cur_language].partial_search(find)