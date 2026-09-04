# TrendsPy

Python library for accessing Google Trends data, maintained for publication by **Chiqo-ke**. This is a fork of https://github.com/sdil87/trendspy.

This project provides two extraction paths:

- Public HTTP helpers for trend timelines, geography, suggestions, and existing TrendsPy features.
- An authenticated Google Trends Explore exporter that controls an already-running Microsoft Edge session through CDP and downloads the UI's Top and Rising Related Queries CSV files.

The authenticated exporter is intended for local, user-controlled browser sessions. It does not read cookies, passwords, OAuth credentials, localStorage tokens, or authorization headers.

## Key Features

**Explore**
- Track popularity over time (`interest_over_time`)
- Analyze geographic distribution (`interest_by_region`)
- Compare interest across different timeframes and regions (multirange support)
- Get related queries and topics (`related_queries`, `related_topics`)
- Export authenticated Explore Related Queries through Edge CDP (`get_related_query_tables`, `get_related_queries`)

**Trending Now**
- Access current trending searches (`trending_now`, `trending_now_by_rss`)
- Get related news articles (`trending_now_news_by_ids`)
- Retrieve historical data for 500+ trending keywords with independent normalization (`trending_now_showcase_timeline`)

**Search Utilities**
- Find category IDs (`categories`)
- Search for location codes (`geo`)

**Flexible Time Formats**
- Custom intervals: `'now 123-H'`, `'today 45-d'`
- Date-based offsets: `'2024-02-01 10-d'`
- Standard ranges: `'2024-01-01 2024-12-31'`

## Installation

```bash
pip install gtrendspy
```

## Basic Usage

```python
from trendspy import Trends
tr = Trends()
df = tr.interest_over_time(['python', 'javascript'])
df.plot(title='Python vs JavaScript Interest Over Time', 
        figsize=(12, 6))
```

```python
# Analyze geographic distribution
geo_df = tr.interest_by_region('python')
```
```python
# Get related queries
related = tr.related_queries('python')
```

### Authenticated Explore Related Queries

`get_related_query_tables()` does not launch Edge, sign in to Google, or create an authenticated browser session. It attaches to an Edge process that you start and authenticate yourself. Complete these steps before running Python code.

#### Requirements

- Microsoft Edge installed locally
- A Google account that can open Google Trends
- A dedicated Edge profile started with CDP remote debugging enabled
- A free local TCP port, normally `9222`
- The `websocket-client` dependency, installed automatically by `gtrendspy`

Do not use your normal daily Edge profile for automation. Use a dedicated profile directory so the session is isolated. Do not expose the CDP port beyond the local machine.

#### Windows setup

1. Close any Edge process using the dedicated profile directory.
2. Open PowerShell and run one of these commands, depending on your installation:

```powershell
& "C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe" `
    --remote-debugging-port=9222 `
    --user-data-dir="C:\Temp\gtrendspy-edge"
```

or:

```powershell
& "C:\Program Files\Microsoft\Edge\Application\msedge.exe" `
    --remote-debugging-port=9222 `
    --user-data-dir="C:\Temp\gtrendspy-edge"
```

3. In the new Edge window, sign in to Google manually if required.
4. Open Google Trends Explore: `https://trends.google.com/explore`.
5. Confirm that the CDP endpoint is available:

```powershell
Invoke-WebRequest http://127.0.0.1:9222/json/version
```

The response should contain an Edge browser version and a `webSocketDebuggerUrl`. Keep this Edge window open while the Python code runs.

#### macOS and Linux setup

Start the Edge executable with the same flags:

```bash
Microsoft\ Edge \\
    --remote-debugging-port=9222 \\
    --user-data-dir="$HOME/.gtrendspy-edge"
```

On Linux, the executable is commonly `microsoft-edge`:

```bash
microsoft-edge \\
    --remote-debugging-port=9222 \\
    --user-data-dir="$HOME/.gtrendspy-edge"
```

Then sign in manually, open Explore, and verify `http://127.0.0.1:9222/json/version` before running the package.

#### Run the authenticated exporter

Once Edge is running, authenticated, and displaying the Explore page:

```text
pip install gtrendspy
```

The library communicates with the page through CDP; it does not access browser credentials. The Python process must be able to reach `127.0.0.1:9222`.

```python
from pathlib import Path
from trendspy import Trends

tr = Trends()
tables = tr.get_related_query_tables(
    query="antimicrobial resistance",
    start_date="2025-01-01",
    end_date="2026-01-01",
    geo="Worldwide",
    search_type="web",
    debugger_url="http://127.0.0.1:9222",
    download_dir=str(Path("data").resolve()),
)

print(tables["top"])
print(tables["rising"])
```

The page may take several seconds to render. The exporter waits for the Explore content, scrolls the nested results container, clicks the Top and Rising CSV controls, and captures the resulting browser exports. Keep the Edge window open until the call returns.

The normalized return shape is:

```python
{
    "top": [
        {"query": "...", "interest": 123, "change": "45%"},
    ],
    "rising": [
        {"query": "...", "interest": 11, "change": "Breakout"},
    ],
}
```

For a single combined list, use `get_related_queries(...)`. Top and Rising remain separate in `get_related_query_tables(...)` because they are independent rankings. `interest` is the Search interest column; `change` is the Change/increase-percent column and may be a string such as `"Breakout"`.

#### Browser CSV exports

The Explore page provides two UI download buttons:

- `Download top queries CSV`
- `Download rising queries CSV`

The CDP exporter waits for the page, scrolls the nested Explore content container, clicks both buttons by their `aria-label`, and configures the browser download directory when `download_dir` is supplied. Google-generated filenames resemble:

```text
searched_with_top-searches_Worldwide_20250101-0300_20260821-0936.csv
searched_with_rising-searches_Worldwide_20250101-0300_20260821-0936.csv
```

The downloaded files preserve Google's original columns, such as `query`, `search interest`, and `increase percent`. RPC decoding is retained as a diagnostic/fallback layer and does not overwrite a successful browser export with an empty result.

#### Diagnostics and limitations

The exporter logs RPC ID, request URL, HTTP status, response size, Related Queries detection, row count, and detected fields. It never logs credential material. Google can change the Explore DOM, download labels, RPC payload shape, or availability of Related Queries. Live authenticated browser testing is therefore required before production use.

#### Troubleshooting

- **`No authenticated Google Trends page...`**: Edge is not running with CDP, the port is wrong, or the page is not a Google Trends page. Check `/json/version` and `/json/list`.
- **Connection refused on port `9222`**: restart Edge with `--remote-debugging-port=9222`; do not start the Python code first.
- **Google sign-in page appears**: authenticate manually in the dedicated Edge profile, then return to Explore.
- **The call returns empty tables**: wait for Explore to finish loading, confirm the Related Queries sections show rows in the browser, and keep the page open while the call runs.
- **Downloads appear in the wrong directory**: pass `download_dir` explicitly. The browser's default download directory is used only when no directory is supplied.

## Advanced Features

### Search Categories and Locations

```python
# Find technology-related categories
categories = tr.categories(find='technology')
# Output: [{'name': 'Computers & Electronics', 'id': '13'}, ...]

# Search for locations
locations = tr.geo(find='york')
# Output: [{'name': 'New York', 'id': 'US-NY'}, ...]

# Use in queries
df = tr.interest_over_time(
    'python',
    geo='US-NY',      # Found location ID
    cat='13'          # Found category ID
)
```

### Real-time Trending Searches and News

```python
# Get current trending searches in the US
trends = tr.trending_now(geo='US')

# Get trending searches with news articles
trends_with_news = tr.trending_now_by_rss(geo='US')
print(trends_with_news[0])  # First trending topic
print(trends_with_news[0].news[0])  # Associated news article

# Get news articles for specific trending topics
news = tr.trending_now_news_by_ids(
    trends[0].news_tokens,  # News tokens from trending topic
    max_news=3  # Number of articles to retrieve
)
for article in news:
    print(f"Title: {article.title}")
    print(f"Source: {article.source}")
    print(f"URL: {article.url}\n")
```

### Independent Historical Data for Multiple Keywords

```python
from trendspy import BatchPeriod

# Unlike standard interest_over_time where data is normalized across all keywords,
# trending_now_showcase_timeline provides independent data for each keyword
# (up to 500+ keywords in a single request)

keywords = ['keyword1', 'keyword2', ..., 'keyword500']

# Get independent historical data
df_24h = tr.trending_now_showcase_timeline(
    keywords,
    timeframe=BatchPeriod.Past24H  # 16-minute intervals
)

# Each keyword's data is normalized only to itself
df_24h.plot(
    subplots=True,
    layout=(5, 2),
    figsize=(15, 20),
    title="Independent Trend Lines"
)

# Available time windows:
# - Past4H:  ~30 points (8-minute intervals)
# - Past24H: ~90 points (16-minute intervals)
# - Past48H: ~180 points (16-minute intervals)
# - Past7D:  ~42 points (4-hour intervals)
```

### Geographic Analysis

```python
# Country-level data
country_df = tr.interest_by_region('python')

# State-level data for the US
state_df = tr.interest_by_region(
    'python',
    geo='US',
    resolution='REGION'
)

# City-level data for California
city_df = tr.interest_by_region(
    'python',
    geo='US-CA',
    resolution='CITY'
)
```

### Timeframe Formats

- Standard API timeframes: `'now 1-H'`, `'now 4-H'`, `'today 1-m'`, `'today 3-m'`, `'today 12-m'`
- Custom intervals:
  - Short-term (< 8 days): `'now 123-H'`, `'now 72-H'`
  - Long-term: `'today 45-d'`, `'today 90-d'`, `'today 18-m'`
  - Date-based: `'2024-02-01 10-d'`, `'2024-03-15 3-m'`
- Date ranges: `'2024-01-01 2024-12-31'`
- Hourly precision: `'2024-03-25T12 2024-03-25T15'` (for periods < 8 days)
- All available data: `'all'`

### Multirange Interest Over Time

Compare search interest across different time periods and regions:

```python
# Compare different time periods
timeframes = [
    '2024-01-25 12-d',    # 12-day period
    '2024-06-20 23-d'     # 23-day period
]
geo = ['US', 'GB']        # Compare US and UK

df = tr.interest_over_time(
    'python',
    timeframe=timeframes,
    geo=geo
)
```

Note: When using multiple timeframes, they must maintain consistent resolution and the maximum timeframe cannot be more than twice the length of the minimum timeframe.

### Proxy Support

TrendsPy supports the same proxy configuration as the `requests` library:

```python
# Initialize with proxy
tr = Trends(proxy="http://user:pass@10.10.1.10:3128")
# or
tr = Trends(proxy={
    "http": "http://10.10.1.10:3128",
    "https": "http://10.10.1.10:1080"
})

# Configure proxy after initialization
tr.set_proxy("http://10.10.1.10:3128")
```

## Documentation

For more examples and detailed API documentation, check out the Jupyter notebook in the repository: `basic_usage.ipynb`

## License

MIT License - see the [LICENSE](LICENSE) file for details.

## Disclaimer

This library is not affiliated with Google. Please ensure compliance with Google's terms of service when using this library.

## Maintainer and publication

The current package metadata identifies **Chiqo-ke** as the author and maintainer. See [PUBLISHING.md](PUBLISHING.md) for the release checklist.
