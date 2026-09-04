"""Authenticated Google Trends Explore extraction over the browser CDP session."""

import json
import logging
from pathlib import Path
import time
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import parse_qs, urlencode, urlparse

LOGGER = logging.getLogger(__name__)
BATCH_EXECUTE_URL = "https://trends.google.com/_/TrendsUi/data/batchexecute"

_FIELD_ALIASES = {
    "query": {"query", "keyword", "term"},
    "interest": {"search interest", "searchinterest", "interest", "value", "formattedvalue"},
    "change": {"change", "change (%)", "change%", "percent change", "formattedchange"},
}


def _canonical(value: Any) -> str:
    return "".join(str(value).strip().lower().split())


def _field_name(value: Any) -> Optional[str]:
    key = _canonical(value)
    for field, aliases in _FIELD_ALIASES.items():
        if key in {_canonical(alias) for alias in aliases}:
            return field
    return None


def _decode_nested(value: Any) -> Any:
    if isinstance(value, str):
        candidate = value.strip()
        if candidate and candidate[0] in "[{":
            try:
                return _decode_nested(json.loads(candidate))
            except (TypeError, ValueError):
                return value
        return value
    if isinstance(value, list):
        return [_decode_nested(item) for item in value]
    if isinstance(value, dict):
        return {key: _decode_nested(item) for key, item in value.items()}
    return value


def decode_batchexecute_response(response_text: str) -> Any:
    """Decode Google's line-delimited, nested JSON RPC response generically."""
    text = response_text.lstrip()
    if text.startswith(")]}'"):
        text = text.split("\n", 1)[1] if "\n" in text else ""
    values = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            values.append(_decode_nested(json.loads(line)))
        except ValueError:
            continue
    return values[0] if len(values) == 1 else values


def _row_from_mapping(row: Dict[Any, Any]) -> Optional[Dict[str, Any]]:
    result = {}
    for key, value in row.items():
        field = _field_name(key)
        if field:
            result[field] = value
    return result if "query" in result and ("interest" in result or "change" in result) else None


def _rows_from_header_table(table: list) -> Optional[List[Dict[str, Any]]]:
    if not table or not isinstance(table[0], list):
        return None
    headers = [_field_name(item) for item in table[0]]
    if "query" not in headers or not ("interest" in headers and "change" in headers):
        return None
    rows = []
    for values in table[1:]:
        if not isinstance(values, list):
            continue
        row = {field: values[index] for index, field in enumerate(headers)
               if field and index < len(values)}
        if "query" in row:
            rows.append(row)
    return rows or None


def _walk_tables(node: Any, path: Tuple[str, ...] = ()) -> Iterable[Tuple[str, List[Dict[str, Any]]]]:
    if isinstance(node, dict):
        own_row = _row_from_mapping(node)
        if own_row:
            yield ("rising" if any("rising" in part for part in path) else "top", [own_row])
        direct = [_row_from_mapping(row) for row in node.values() if isinstance(row, dict)]
        direct = [row for row in direct if row]
        if direct:
            yield ("rising" if any("rising" in part for part in path) else "top", direct)
        for key, value in node.items():
            yield from _walk_tables(value, path + (_canonical(key),))
    elif isinstance(node, list):
        table = _rows_from_header_table(node)
        if table:
            yield ("rising" if any("rising" in part for part in path) else "top", table)
        for item in node:
            yield from _walk_tables(item, path)


def extract_related_query_tables(decoded_rpc: Any) -> Dict[str, List[Dict[str, Any]]]:
    """Find Top and Rising tables without relying on RPC IDs or row positions."""
    tables = {"top": [], "rising": []}
    seen = {"top": set(), "rising": set()}
    for table_type, rows in _walk_tables(decoded_rpc):
        for row in rows:
            marker = (row.get("query"), row.get("interest"), row.get("change"))
            if marker not in seen[table_type]:
                tables[table_type].append(row)
                seen[table_type].add(marker)
    return tables


def normalize_related_query_tables(tables: Dict[str, List[Dict[str, Any]]]) -> Dict[str, List[Dict[str, Any]]]:
    """Normalize only after extraction; Google's raw values remain available to callers."""
    normalized = {}
    for table_type, rows in tables.items():
        normalized[table_type] = [
            {
                "query": str(row.get("query")) if row.get("query") is not None else "",
                "interest": int(row["interest"]) if isinstance(row.get("interest"), (int, float))
                or (isinstance(row.get("interest"), str) and row["interest"].strip().isdigit()) else None,
                "change": str(row["change"]) if row.get("change") is not None else None,
            }
            for row in rows
        ]
    return normalized


class AuthenticatedExploreSession:
    """Capture Explore RPC responses from an already-authenticated CDP page."""

    def __init__(self, debugger_url: str = "http://127.0.0.1:9222", timeout: float = 25.0):
        self.debugger_url = debugger_url.rstrip("/")
        self.timeout = timeout
        self.last_scroll_result = None
        self.last_download_result = None

    def _page_target(self) -> Dict[str, Any]:
        import requests
        pages = requests.get(self.debugger_url + "/json/list", timeout=5).json()
        for page in pages:
            if "trends.google.com" in page.get("url", "") and page.get("webSocketDebuggerUrl"):
                return page
        raise RuntimeError("No authenticated Google Trends page with a CDP websocket was found")

    def _page_websocket_url(self) -> str:
        return self._page_target()["webSocketDebuggerUrl"]

    def capture(self, explore_url: str, download_dir: Optional[str] = None) -> List[Dict[str, Any]]:
        try:
            import importlib
            websocket = importlib.import_module("websocket")
        except ImportError as exc:
            raise RuntimeError("Install websocket-client to capture the authenticated Edge session") from exc
        page = self._page_target()
        socket = websocket.create_connection(page["webSocketDebuggerUrl"], timeout=1, suppress_origin=True)
        next_id = 0
        events = []

        def command(method, params=None):
            nonlocal next_id
            next_id += 1
            socket.send(json.dumps({"id": next_id, "method": method, "params": params or {}}))
            return next_id

        command("Network.enable")
        command("Page.enable")
        command("Runtime.enable")
        if download_dir:
            Path(download_dir).mkdir(parents=True, exist_ok=True)
            command("Page.setDownloadBehavior", {
                "behavior": "allow",
                "downloadPath": str(Path(download_dir).resolve()),
            })
        current = parse_qs(urlparse(page.get("url", "")).query)
        requested = parse_qs(urlparse(explore_url).query)
        scroll_expression = """(async()=>{
    const pause=()=>new Promise(resolve=>setTimeout(resolve,700));
    let container=null;
    for(let attempt=0;attempt<15&&!container;attempt++){
      container=document.querySelector('.Jh24Ne')||Array.from(document.querySelectorAll('*')).find(el=>el.scrollHeight>el.clientHeight+20&&getComputedStyle(el).overflowY==='auto');
      if(!container) await pause();
    }
    if(!container) return {scrollTop:null,maxScrollTop:null,containerFound:false};
    container.scrollIntoView({block:'start'});
    const max=container.scrollHeight-container.clientHeight;
    for(let top=0;top<=max;top+=600){container.scrollTop=top;container.dispatchEvent(new Event('scroll',{bubbles:true}));await pause();}
    container.scrollTop=max;
    container.dispatchEvent(new Event('scroll',{bubbles:true}));
    await pause();
    return {scrollTop:container.scrollTop,maxScrollTop:max,containerFound:true};
    })()"""
        download_expression = """(async()=>{
        const pause=()=>new Promise(resolve=>setTimeout(resolve,700));
        const labels=['Download top queries CSV','Download rising queries CSV'];
        const found=[];
        for(let attempt=0;attempt<20;attempt++){
            for(const label of labels){
                if(!found.includes(label)&&document.querySelector(`button[aria-label="${label}"]`))found.push(label);
            }
            if(found.length===labels.length)break;
            await pause();
        }
        const clicked=[];
        for(const label of labels){
            const button=document.querySelector(`button[aria-label="${label}"]`);
            if(button){button.click();clicked.push(label);await pause();}
        }
        return {found,clicked};
        })()"""
        same_explore = page.get("url", "").startswith("https://trends.google.com/explore") and all(
            current.get(key, [""]) == requested.get(key, [""]) for key in ("q", "date", "geo", "gprop")
        )
        if not same_explore:
            command("Page.navigate", {"url": explore_url})
            scroll_command_id = None
            download_command_id = None
        else:
            scroll_command_id = command("Runtime.evaluate", {
                "expression": scroll_expression,
                "awaitPromise": True,
                "returnByValue": True,
            })
            download_command_id = command("Runtime.evaluate", {
                "expression": download_expression,
                "awaitPromise": True,
                "returnByValue": True,
            })

        def dispatch_wheel():
            command("Input.dispatchMouseEvent", {
                "type": "mouseMoved",
                "x": 500,
                "y": 350,
            })
            wheel_command_ids.append(command("Input.dispatchMouseEvent", {
                "type": "mouseWheel",
                "x": 500,
                "y": 350,
                "deltaX": 0,
                "deltaY": 600,
            }))

        wheel_command_ids = []
        if same_explore:
            for _ in range(7):
                dispatch_wheel()
        deadline = time.time() + self.timeout
        pending = {}
        scroll_started = False
        while time.time() < deadline:
            try:
                message = json.loads(socket.recv())
            except Exception:
                continue
            method = message.get("method")
            params = message.get("params", {})
            response_url = params.get("response", {}).get("url", "")
            if method == "Page.loadEventFired" and not scroll_started:
                scroll_command_id = command("Runtime.evaluate", {
                    "expression": scroll_expression,
                    "awaitPromise": True,
                    "returnByValue": True,
                })
                for _ in range(7):
                    dispatch_wheel()
                download_command_id = command("Runtime.evaluate", {
                    "expression": download_expression,
                    "awaitPromise": True,
                    "returnByValue": True,
                })
                scroll_started = True
            elif method == "Network.responseReceived" and response_url.startswith(BATCH_EXECUTE_URL):
                response = params["response"]
                request_id = params["requestId"]
                pending[request_id] = {"request_id": request_id, "response": response}
            elif method == "Network.loadingFinished" and params.get("requestId") in pending:
                event = pending.pop(params["requestId"])
                event["body_id"] = command("Network.getResponseBody", {"requestId": params["requestId"]})
                events.append(event)
            elif "id" in message:
                if message["id"] == scroll_command_id:
                    result = message.get("result", {}).get("result", {})
                    self.last_scroll_result = result.get("value")
                if message["id"] == download_command_id:
                    result = message.get("result", {}).get("result", {})
                    self.last_download_result = result.get("value")
                if message["id"] == scroll_command_id and "exceptionDetails" in message.get("result", {}):
                    details = message["result"]["exceptionDetails"]
                    raise RuntimeError(details.get("exception", {}).get("description") or details.get("text", "Explore scroll failed"))
                if message["id"] == download_command_id and "exceptionDetails" in message.get("result", {}):
                    details = message["result"]["exceptionDetails"]
                    raise RuntimeError(details.get("exception", {}).get("description") or details.get("text", "Explore download click failed"))
                for event in events:
                    if event["body_id"] == message["id"]:
                        event["body"] = message.get("result", {}).get("body", "")
        socket.close()
        return events

    def retry_missing_downloads(self, labels: List[str], download_dir: Optional[str] = None,
                                delay: float = 3.0) -> Dict[str, List[str]]:
        """Retry only download buttons whose browser exports were not observed."""
        if not labels:
            return {"found": [], "clicked": []}
        import importlib
        websocket = importlib.import_module("websocket")
        page = self._page_target()
        socket = websocket.create_connection(page["webSocketDebuggerUrl"], timeout=1, suppress_origin=True)
        next_id = 0

        def command(method, params=None):
            nonlocal next_id
            next_id += 1
            socket.send(json.dumps({"id": next_id, "method": method, "params": params or {}}))
            return next_id

        command("Runtime.enable")
        if download_dir:
            Path(download_dir).mkdir(parents=True, exist_ok=True)
            command("Page.setDownloadBehavior", {
                "behavior": "allow",
                "downloadPath": str(Path(download_dir).resolve()),
            })
        quoted_labels = json.dumps(list(labels))
        expression = """(async()=>{
const labels=LABELS;
await new Promise(resolve=>setTimeout(resolve,DELAY));
const found=[];
for(let attempt=0;attempt<20;attempt++){
  for(const label of labels){if(!found.includes(label)&&document.querySelector(`button[aria-label="${label}"]`))found.push(label);}
  if(found.length===labels.length)break;
  await new Promise(resolve=>setTimeout(resolve,700));
}
const clicked=[];
for(const label of found){document.querySelector(`button[aria-label="${label}"]`).click();clicked.push(label);await new Promise(resolve=>setTimeout(resolve,700));}
return {found,clicked};
})()""".replace("LABELS", quoted_labels).replace("DELAY", str(int(delay * 1000)))
        command_id = command("Runtime.evaluate", {
            "expression": expression,
            "awaitPromise": True,
            "returnByValue": True,
        })
        result = {"found": [], "clicked": []}
        deadline = time.time() + max(delay + 20, 25)
        while time.time() < deadline:
            try:
                message = json.loads(socket.recv())
            except Exception:
                continue
            if message.get("id") != command_id:
                continue
            if "exceptionDetails" in message.get("result", {}):
                details = message["result"]["exceptionDetails"]
                raise RuntimeError(details.get("exception", {}).get("description") or "Download retry failed")
            result = message.get("result", {}).get("result", {}).get("value", result)
            break
        socket.close()
        self.last_download_result = result
        return result


def build_explore_url(query: str, start_date: str, end_date: str, geo: str, search_type: str) -> str:
    gprop = "" if search_type == "web" else search_type
    params = {"q": query, "date": "%s %s" % (start_date, end_date), "geo": "" if geo == "Worldwide" else geo, "gprop": gprop}
    return "https://trends.google.com/explore?" + urlencode(params)


def extract_from_captured_events(events: List[Dict[str, Any]], logger: logging.Logger = LOGGER) -> Dict[str, List[Dict[str, Any]]]:
    for event in events:
        response = event.get("response", {})
        body = event.get("body", "")
        decoded = decode_batchexecute_response(body)
        tables = extract_related_query_tables(decoded)
        rpc_id = parse_qs(urlparse(response.get("url", "")).query).get("rpcids", ["unknown"])[0]
        if isinstance(decoded, list):
            for item in decoded:
                if isinstance(item, list) and len(item) > 1 and item[0] == "wrb.fr":
                    rpc_id = item[1]
                    break
        logger.info("Explore RPC id=%s url=%s status=%s response_size=%s related_queries=%s rows=%s fields=%s",
                    rpc_id, response.get("url"), response.get("status"), len(body), bool(tables["top"] or tables["rising"]),
                    len(tables["top"]) + len(tables["rising"]), sorted({field for rows in tables.values() for row in rows for field in row}))
        if tables["top"] or tables["rising"]:
            return tables
    return {"top": [], "rising": []}
