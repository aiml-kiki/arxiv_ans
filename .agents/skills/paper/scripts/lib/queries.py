"""Persistent actual query lists, screening, citation expansion and deltas.

The selected provider alone makes requests. Imports retain raw supplied data;
no imported/browser list is described as an API call made by this program.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime
from email.utils import parsedate_to_datetime
import json
import os
from pathlib import Path
import re
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import Request, HTTPRedirectHandler, build_opener
import xml.etree.ElementTree as ET

from .core import default_root, digest, inside, locked, read_json, safe_id, utc_now, write_json

FORMAT = "paper-query-v1"
PROVIDERS = ("arxiv", "crossref", "openalex")
MAX_BYTES = 20 * 1024 * 1024
DOI = re.compile(r"^10\.\d{4,9}/\S+$", re.I)
ARXIV = re.compile(r"^((?:\d{4}\.\d{4,5}|[a-z-]+(?:\.[A-Z]{2})?/\d{7}))(v\d+)?$", re.I)


def _trace_path(root, trace):
    return inside(root, f".research/queries/{safe_id(trace)}/state.json")


def _load(root, trace):
    data = read_json(_trace_path(root, trace))
    if data.get("format") != FORMAT:
        raise ValueError("研究记录格式不兼容；不得覆盖未知旧记录")
    checked = {}
    for event_id, pointer in data.get("events", {}).items():
        event_path = inside(root, pointer["path"])
        if not event_path.is_file() or digest(event_path.read_bytes()) != pointer["event_sha256"]:
            raise ValueError(f"持久查询事件缺失/哈希变化：{event_id}；不能继续声称原始列表可追溯")
        event = read_json(event_path)
        responses = list(event.get("pages", []))
        if event.get("raw_path"):
            responses.append(event)
        for response in responses:
            raw_path = inside(root, response["raw_path"])
            if str(raw_path) not in checked:
                checked[str(raw_path)] = digest(raw_path.read_bytes()) if raw_path.is_file() else None
            if checked[str(raw_path)] != response["raw_sha256"]:
                raise ValueError(f"原始查询响应缺失/哈希变化：{response['raw_path']}")
    return data


def _save(root, trace, data):
    data["updated_at"] = utc_now()
    write_json(_trace_path(root, trace), data)


def _dated(value):
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, AttributeError) as exc:
        raise ValueError("取得日期须为 ISO 日期或时间；未知取得日期不能冒充当前日期") from exc
    return value


def _doi(value):
    text = str(value or "").strip()
    text = re.sub(r"^(?:https?://(?:dx\.)?doi\.org/|doi:)", "", text, flags=re.I)
    return text.lower() if DOI.fullmatch(text) else None


def _arxiv(value):
    text = str(value or "").strip()
    text = re.sub(r"^(?:https?://(?:export\.)?arxiv\.org/(?:abs|pdf)/|arxiv:)", "", text, flags=re.I)
    if text.endswith(".pdf"):
        text = text[:-4]
    match = ARXIV.fullmatch(text)
    return (match[1], match[2]) if match else (None, None)


def _openalex(value):
    text = str(value or "").strip().rsplit("/", 1)[-1]
    return text.upper() if re.fullmatch(r"W\d+", text, re.I) else None


def _json_sha(value):
    return digest(json.dumps(value, ensure_ascii=False, sort_keys=True))


def _scalar(value):
    if isinstance(value, list):
        return str(value[0]) if value else None
    return str(value) if value is not None else None


def _crossref(item):
    date_field = next((item[name] for name in ("published", "published-print", "published-online")
                       if isinstance(item.get(name), dict)), {})
    dates = date_field.get("date-parts", [])
    date = "-".join(str(part).zfill(2) if index else str(part) for index, part in enumerate(dates[0])) if dates else None
    authors = [" ".join(str(author.get(field, "")) for field in ("given", "family")).strip()
               or author.get("name") for author in (item.get("author") or []) if isinstance(author, dict)]
    return {"title": _scalar(item.get("title")), "doi": _doi(item.get("DOI")),
            "provider_id": item.get("DOI"), "authors": [name for name in authors if name],
            "published": date, "abstract": item.get("abstract"), "version": item.get("version"),
            "url": item.get("URL"), "record_type": item.get("type"), "metadata": item}


def _openalex_record(item):
    ids = item.get("ids", {}) or {}
    authors = [(entry.get("author") or {}).get("display_name") for entry in (item.get("authorships") or []) if isinstance(entry, dict)]
    primary = item.get("primary_location", {}) or {}
    return {"title": item.get("display_name", item.get("title")), "doi": _doi(item.get("doi", ids.get("doi"))),
            "openalex": _openalex(item.get("id", ids.get("openalex"))), "provider_id": item.get("id"),
            "authors": [author for author in authors if author], "published": item.get("publication_date"),
            "abstract": None, "version": primary.get("version"),
            "url": primary.get("landing_page_url", item.get("id")),
            "record_type": item.get("type"), "metadata": item}


def parse_response(provider, raw):
    """Return only actual items, reported total, and provider continuation."""
    if provider == "arxiv":
        if b"<!DOCTYPE" in raw.upper() or b"<!ENTITY" in raw.upper():
            raise ValueError("拒绝带实体声明的查询 XML")
        tree = ET.fromstring(raw)
        atom = "{http://www.w3.org/2005/Atom}"
        arxiv = "{http://arxiv.org/schemas/atom}"
        search = "{http://a9.com/-/spec/opensearch/1.1/}"
        if tree.tag != atom + "feed":
            raise ValueError("arXiv 原始响应不是 Atom feed；不能当作零结果")
        records = []
        for entry in tree.findall(atom + "entry"):
            entry_id = entry.findtext(atom + "id", "")
            if "api/errors" in entry_id:
                raise ValueError("arXiv 返回错误条目：" + entry.findtext(atom + "summary", "unknown"))
            base, version = _arxiv(entry_id)
            if not base:
                raise ValueError("arXiv 响应条目缺少可识别实际身份")
            records.append({"title": " ".join(entry.findtext(atom + "title", "").split()),
                            "arxiv": base, "version": version, "provider_id": entry_id,
                            "doi": _doi(entry.findtext(arxiv + "doi")),
                            "authors": [author.findtext(atom + "name") for author in entry.findall(atom + "author")],
                            "abstract": " ".join(entry.findtext(atom + "summary", "").split()),
                            "published": entry.findtext(atom + "published"), "url": entry_id,
                            "record_type": "preprint", "metadata": {"updated": entry.findtext(atom + "updated"),
                            "categories": [item.attrib.get("term") for item in entry.findall(atom + "category")]}})
        total = tree.findtext(search + "totalResults")
        return records, int(total) if total is not None else None, None
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("provider 原始 JSON 响应须为对象，不能把未知格式当作零结果")
    if provider == "crossref":
        message = data.get("message", {})
        if data.get("status") != "ok" or not isinstance(message, dict):
            raise ValueError("Crossref 返回错误/未知响应，原始内容保留")
        items = message.get("items")
        if items is None:
            if not message.get("DOI"):
                raise ValueError("Crossref 响应缺实际 items 或 singleton DOI")
            items = [message]
        if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
            raise ValueError("Crossref items 格式不符")
        return [_crossref(item) for item in items], message.get("total-results"), message.get("next-cursor")
    if provider == "openalex":
        items = data.get("results")
        if items is None:
            if not data.get("id"):
                raise ValueError("OpenAlex 响应缺实际 results 或 singleton ID")
            items = [data]
        if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
            raise ValueError("OpenAlex results 格式不符")
        return [_openalex_record(item) for item in items], data.get("meta", {}).get("count"), data.get("meta", {}).get("next_cursor")
    if provider == "manual":
        if not isinstance(data, dict) or not isinstance(data.get("records"), list):
            raise ValueError("人工/浏览器导入须为含 records 数组的 JSON；保留实际列表，不接受自由总结替代")
        if data.get("reported_total") is not None and (not isinstance(data["reported_total"], int) or isinstance(data["reported_total"], bool) or data["reported_total"] < 0):
            raise ValueError("reported_total 须为实际非负整数或 null")
        normalized = []
        for item in data["records"]:
            if not isinstance(item, dict):
                raise ValueError("每项实际记录须为对象")
            if not any(item.get(key) for key in ("title", "doi", "arxiv", "openalex", "provider_id", "url")):
                raise ValueError("实际记录至少提供身份线索；缺字段保持 null")
            if item.get("study_id") is not None and not isinstance(item["study_id"], str):
                raise ValueError("已确认独立研究身份须为明确字符串；未知保留 null")
            normalized.append(dict(item))
        return normalized, data.get("reported_total"), data.get("continuation")
    raise ValueError("不支持的真实响应格式：" + provider)


def _identity(item):
    doi = _doi(item.get("doi"))
    arxiv, detected_version = _arxiv(item.get("arxiv"))
    openalex = _openalex(item.get("openalex"))
    version = _scalar(item.get("version")) or detected_version
    # Explicit versions of a preprint are separate reports, one logical work.
    if arxiv:
        work = f"arxiv:{arxiv}"
        report = f"{work}:{version or 'version-unknown'}"
    elif doi:
        # The same DOI names one report. Landing-page publication labels and
        # metadata revisions do not create independent reports or studies.
        work, report = f"doi:{doi}", f"doi:{doi}"
    elif openalex:
        work, report = f"openalex:{openalex}", f"openalex:{openalex}:{version or 'version-unknown'}"
    else:
        work = "unresolved:" + digest(str(item.get("provider_id") or item.get("url") or _json_sha(item)))[:24]
        report = f"{work}:{version or 'version-unknown'}"
    aliases = [f"doi:{doi}" if doi else None, f"arxiv:{arxiv}" if arxiv else None,
               f"openalex:{openalex}" if openalex else None]
    return {"doi": doi, "arxiv": arxiv, "openalex": openalex, "version": version,
            "work_id": work, "report_id": report, "identity_aliases": [value for value in aliases if value]}


def _ingest(state, event_id, items, provider, observed_at):
    record_ids = []
    for index, item in enumerate(items):
        identity = _identity(item)
        record_id = "record-" + digest(f"{event_id}:{index}")[:24]
        record = {**item, **identity, "record_id": record_id, "provider": provider,
                  "observed_at": observed_at, "event_id": event_id, "item_index": index,
                  "metadata_sha256": _json_sha(item), "study_id": item.get("study_id"),
                  "screening": {"status": "待判断", "reason": "尚未语义筛选", "history": []}}
        state["records"][record_id] = record
        record_ids.append(record_id)
    return record_ids


def _event_id(state, prefix):
    stamp = datetime.now().strftime("%Y%m%d%H%M%S%f")
    return safe_id(f"{prefix}-{stamp}-{len(state['events']) + 1}")


def _safe_headers(headers):
    allowed = {"content-type", "date", "retry-after", "x-rate-limit-limit", "x-rate-limit-interval",
               "x-ratelimit-limit", "x-ratelimit-remaining", "x-ratelimit-reset", "x-ratelimit-credits-used"}
    return {key: value for key, value in headers.items() if key.lower() in allowed}


def _raw_path(root, trace, event, page, extension):
    return inside(root, f".research/queries/{safe_id(trace)}/responses/{safe_id(event)}/{page:04d}.{extension}")


def _request(root, trace, event, provider, path, params, page, timeout=45, interval=None):
    bases = {"arxiv": "https://export.arxiv.org", "crossref": "https://api.crossref.org", "openalex": "https://api.openalex.org"}
    if provider not in bases or not path.startswith("/") or ".." in path.split("/"):
        raise ValueError("网络查询只允许已配置官方 provider 的确定路径")
    if any(key.lower() in {"api_key", "key", "token", "authorization"} for key in params):
        raise ValueError("密钥只能通过环境变量和请求头使用，不能进入持久查询参数")
    url = bases[provider] + path + ("?" + urlencode(params) if params else "")
    contact = os.environ.get("PAPER_API_CONTACT")
    headers = {"User-Agent": "paper-research/1.0" + (f" (mailto:{contact})" if contact else ""),
               "Accept": "application/atom+xml" if provider == "arxiv" else "application/json"}
    if provider == "openalex" and os.environ.get("PAPER_OPENALEX_API_KEY"):
        headers["Authorization"] = "Bearer " + os.environ["PAPER_OPENALEX_API_KEY"]
    interval = interval if interval is not None else (3.0 if provider == "arxiv" else 1.0)
    if interval < 0 or interval > 30:
        raise ValueError("请求间隔须在0..30秒，429后由实际 Retry-After 决定稍后续跑")
    provider_state = inside(root, f".research/queries/provider-state/{provider}.json")
    failure, raw, status, response_headers = None, b"", None, {}
    with locked(root, f"query-provider:{provider}"):
        last = read_json(provider_state) if provider_state.exists() else {}
        if float(last.get("retry_after_epoch") or 0) > time.time():
            raise ValueError("provider 的实际 Retry-After 尚未结束；稍后接续，不提前重复请求")
        remaining = float(last.get("last_request_epoch", 0)) + interval - time.time()
        if remaining > 0:
            time.sleep(min(remaining, interval))
        observed_at = utc_now()
        try:
            allowed_hosts = {urlsplit(bases[provider]).hostname, "arxiv.org" if provider == "arxiv" else ""}
            class OfficialRedirect(HTTPRedirectHandler):
                def redirect_request(self, request, stream, code, message, response_headers, newurl):
                    destination = urlsplit(newurl)
                    if destination.scheme != "https" or destination.hostname not in allowed_hosts:
                        raise ValueError("provider 重定向到非官方 HTTPS 域名；未转发请求或认证头")
                    return super().redirect_request(request, stream, code, message, response_headers, newurl)
            opener = build_opener(OfficialRedirect())
            with opener.open(Request(url, headers=headers), timeout=timeout) as response:
                status, response_headers = response.status, _safe_headers(response.headers)
                raw = response.read(MAX_BYTES + 1)
                if len(raw) > MAX_BYTES:
                    raise ValueError("单响应超过20MiB；请缩小查询页而不是无界下载")
        except HTTPError as exc:
            raw = exc.read(MAX_BYTES)
            status, response_headers = exc.code, _safe_headers(exc.headers)
            failure = f"HTTP {exc.code}；原始响应已保留，未自动换 provider 或伪造零结果"
        except (URLError, OSError, ValueError) as exc:
            failure = f"{type(exc).__name__}: {str(exc)}"
        finally:
            retry = next((value for key, value in response_headers.items() if key.lower() == "retry-after"), None)
            retry_epoch = None
            if retry:
                try:
                    retry_epoch = time.time() + max(0.0, float(retry))
                except ValueError:
                    try:
                        retry_epoch = parsedate_to_datetime(retry).timestamp()
                    except (TypeError, ValueError):
                        pass
            write_json(provider_state, {"last_request_epoch": time.time(), "last_request_at": observed_at,
                                        "headers": response_headers, "status": status, "retry_after_epoch": retry_epoch})
    extension = "xml" if provider == "arxiv" else "json"
    destination = _raw_path(root, trace, event, page, extension)
    # Response bodies are stored byte-for-byte, not a normalized substitute.
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        raise ValueError("响应路径已存在；不能覆盖持久查询原始证据")
    with destination.open("xb") as stream:
        stream.write(raw)
    info = {"page": page, "provider": provider, "request_url": url, "parameters": params,
            "observed_at": observed_at, "status": status, "headers": response_headers,
            "raw_path": str(destination.relative_to(root)), "raw_sha256": digest(raw), "error": failure,
            "authentication": "environment-header" if "Authorization" in headers else "none"}
    return raw, info


def _store_event(root, trace, state, event):
    event_path = inside(root, f".research/queries/{trace}/events/{event['id']}.json")
    if event_path.exists():
        raise ValueError("事件文件已存在；持久查询事件不可覆盖")
    write_json(event_path, event)
    state["events"][event["id"]] = {"path": str(event_path.relative_to(root)), "kind": event["kind"],
                                     "observed_at": event["observed_at"], "status": event["status"],
                                     "record_ids": event.get("record_ids", []), "event_sha256": digest(event_path.read_bytes())}
    if event["kind"] in {"search", "import", "expand"}:
        state["stop"] = None  # old coverage decision does not cover a new retrieval
    _save(root, trace, state)


def _group_records(state):
    """Deduplicate records by exact report identities; never merge similar titles."""
    reports = defaultdict(list)
    for record_id, record in state["records"].items():
        reports[record["report_id"]].append(record_id)
    groups = []
    for report_id, record_ids in sorted(reports.items()):
        records = [state["records"][record_id] for record_id in record_ids]
        revision = len(state["criteria_history"]) - 1
        current = [record for record in records if record["screening"].get("history")
                   and record["screening"]["history"][-1].get("criterion_revision") == revision]
        explicit = [record["screening"] for record in current if record["screening"]["status"] != "待判断"]
        statuses = {decision["status"] for decision in explicit}
        status = next(iter(statuses)) if len(statuses) == 1 else "待判断"
        studies = {record["study_id"] for record in records if record.get("study_id")}
        works = {record["work_id"] for record in records}
        groups.append({"report_id": report_id, "record_ids": record_ids, "work_ids": sorted(works),
                       "study_ids": sorted(studies), "status": status,
                       "decision_conflict": len(statuses) > 1, "study_identity_conflict": len(studies) > 1,
                       "criteria_stale_records": [record["record_id"] for record in records
                                                   if record["screening"]["status"] != "待判断" and record not in current],
                       "metadata_versions": sorted({record["metadata_sha256"] for record in records})})
    return groups


def _summary(state):
    reports = _group_records(state)
    status_counts = {name: sum(group["status"] == name for group in reports) for name in ("纳入", "排除", "待判断")}
    known_studies = {study for group in reports if group["status"] == "纳入" for study in group["study_ids"]}
    included_works = {work for group in reports if group["status"] == "纳入" for work in group["work_ids"]}
    return {"trace_id": state["trace_id"], "question": state["question"], "mode": state["mode"],
            "query_events": len(state["events"]), "retrieved_records": len(state["records"]),
            "unique_reports": len(reports), "duplicate_records": len(state["records"]) - len(reports),
            "report_decisions": status_counts, "included_work_ids": len(included_works),
            "confirmed_included_studies": len(known_studies),
            "included_reports_without_study_identity": sum(group["status"] == "纳入" and not group["study_ids"] for group in reports),
            "decision_conflicts": [group["report_id"] for group in reports if group["decision_conflict"]],
            "study_identity_conflicts": [group["report_id"] for group in reports if group["study_identity_conflict"]],
            "reports_needing_rescreen_after_scope_change": [group["report_id"] for group in reports if group["criteria_stale_records"]],
            "actual_citation_edges": len(state["citation_edges"]), "stop": state.get("stop"),
            "coverage_limit": "计数来自实际取得列表；报告数/独立研究数不同，未知研究身份不推断为独立支持。"}


def _citation_edge(state, source, target, event_id, location, raw_path, raw_sha256, observed_at=None):
    if not source or not target:
        raise ValueError("引文事实边必须有实际双方身份；未知引用保留原始条目，不能捏造节点")
    edge = {"source": source, "target": target, "type": "cites", "event_id": event_id,
            "location": location, "raw_path": raw_path, "raw_sha256": raw_sha256,
            "observed_at": observed_at or utc_now()}
    edge["id"] = "edge-" + _json_sha(edge)[:24]
    state["citation_edges"].append(edge)


def search(root, args):
    if args.start < 0 or (args.start != 0 and args.provider != "arxiv"):
        raise ValueError("start 只用于 arXiv 的非负分页位置；Crossref/OpenAlex 从实际 cursor 分页")
    with locked(root, f"query:{args.trace}"):
        state = _load(root, args.trace)
        event_id = _event_id(state, "search")
        event = {"id": event_id, "kind": "search", "provider": args.provider, "query": args.query,
                 "observed_at": utc_now(), "requested_limit": args.limit, "pages": [], "record_ids": [],
                 "reported_total": None, "status": "complete", "filters": args.filter, "sort": args.sort,
                 "pagination_limit": args.pages, "scope_revision": len(state["criteria_history"]) - 1}
        cursor, consumed = "*", 0
        try:
            for page in range(args.pages):
                count = min(args.page_size, args.limit - consumed)
                if count <= 0:
                    break
                if args.provider == "arxiv":
                    if args.filter:
                        raise ValueError("arXiv 条件直接写在实际 search_query；不把 Crossref/OpenAlex filter 静默忽略")
                    params = {"search_query": args.query, "start": args.start + consumed,
                              "max_results": count, "sortBy": args.sort or "relevance", "sortOrder": args.order or "descending"}
                    if params["sortBy"] not in {"relevance", "lastUpdatedDate", "submittedDate"}:
                        raise ValueError("arXiv sort 须为 relevance/lastUpdatedDate/submittedDate")
                    path = "/api/query"
                elif args.provider == "crossref":
                    params = {"query": args.query, "rows": count, "cursor": cursor}
                    if args.filter:
                        params["filter"] = args.filter
                    if args.sort:
                        params["sort"] = args.sort
                    if args.order:
                        params["order"] = "asc" if args.order == "ascending" else "desc"
                    path = "/works"
                else:
                    params = {"search": args.query, "per_page": count, "cursor": cursor}
                    if args.filter:
                        params["filter"] = args.filter
                    if args.sort:
                        params["sort"] = args.sort
                    if args.order:
                        raise ValueError("OpenAlex 顺序须直接写在 sort 参数（如 -publication_date），不使用 order")
                    path = "/works"
                raw, info = _request(root, args.trace, event_id, args.provider, path, params, page,
                                     timeout=args.timeout, interval=args.interval)
                event["pages"].append(info)
                if info["error"]:
                    event["status"] = "failed" if not consumed else "partial"
                    break
                items, total, next_cursor = parse_response(args.provider, raw)
                info.update(returned_records=len(items), reported_total=total, continuation=next_cursor)
                event["reported_total"] = total
                event["record_ids"].extend(_ingest(state, f"{event_id}-page-{page}", items, args.provider, info["observed_at"]))
                consumed += len(items)
                if not items or len(items) < count:
                    event["continuation"] = None
                    break
                event["continuation"] = next_cursor if args.provider != "arxiv" else args.start + consumed
                if args.provider != "arxiv":
                    if not next_cursor or next_cursor == cursor:
                        break
                    cursor = next_cursor
            event["retrieved_records"] = consumed
            event["complete_provider_result_set"] = (event["status"] == "complete" and event["reported_total"] is not None
                                                     and consumed >= event["reported_total"] and args.start == 0)
            if event.get("continuation") is not None and not event["complete_provider_result_set"]:
                event["limit_reached"] = True
        except (ValueError, TypeError, AttributeError, OSError, ET.ParseError) as exc:
            event["status"] = "partial" if consumed else "failed"
            event["error"] = str(exc)
        finally:
            _store_event(root, args.trace, state, event)
        return {"event": event, "summary": _summary(state)}


def import_records(root, args):
    source = inside(root, args.input)
    raw = source.read_bytes()
    if len(raw) > MAX_BYTES:
        raise ValueError("导入文件超过20MiB；按实际查询页拆分")
    observed_at = _dated(args.observed_at)
    items, total, continuation = parse_response(args.provider, raw)
    imported_edges = json.loads(raw).get("citation_edges", []) if args.provider == "manual" else []
    if not isinstance(imported_edges, list) or any(not isinstance(edge, dict) or not edge.get("source")
                                                 or not edge.get("target") or not edge.get("location") for edge in imported_edges):
        raise ValueError("导入事实边数组须含实际 source/target 和原始定位 location")
    parameters = read_json(inside(root, args.parameters)) if args.parameters else {}
    if not isinstance(parameters, dict):
        raise ValueError("查询参数文件须为 JSON 对象")
    if any(str(key).lower() in {"api_key", "token", "authorization", "key"} for key in parameters):
        raise ValueError("持久参数不得含 API 密钥")
    with locked(root, f"query:{args.trace}"):
        state = _load(root, args.trace)
        event_id = _event_id(state, "import")
        target = _raw_path(root, args.trace, event_id, 0, "xml" if args.provider == "arxiv" else "json")
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("xb") as stream:
            stream.write(raw)
        record_ids = _ingest(state, event_id, items, args.provider, observed_at)
        for index, edge in enumerate(imported_edges):
            _citation_edge(state, str(edge["source"]), str(edge["target"]), event_id,
                           f"citation_edges[{index}]: {edge['location']}", str(target.relative_to(root)), digest(raw), observed_at)
        event = {"id": event_id, "kind": "import", "provider": args.provider, "query": args.query,
                 "source_description": args.source, "observed_at": observed_at, "imported_at": utc_now(),
                 "parameters": parameters, "status": "complete", "record_ids": record_ids,
                 "reported_total": total, "continuation": continuation, "retrieved_records": len(items),
                 "raw_path": str(target.relative_to(root)), "raw_sha256": digest(raw),
                 "input_path": str(source.relative_to(root)), "program_made_network_call": False,
                 "scope_revision": len(state["criteria_history"]) - 1}
        _store_event(root, args.trace, state, event)
        return {"event": event, "summary": _summary(state)}


def screen(root, args):
    decisions = read_json(inside(root, args.input))
    if isinstance(decisions, dict):
        decisions = decisions.get("decisions")
    if not isinstance(decisions, list):
        raise ValueError("筛选输入须为 decisions 数组")
    with locked(root, f"query:{args.trace}"):
        state = _load(root, args.trace)
        history = []
        # Validate the whole batch before mutating any persisted screening state.
        targets = []
        for decision in decisions:
            if not isinstance(decision, dict) or decision.get("status") not in {"纳入", "排除", "待判断"} or not str(decision.get("reason", "")).strip():
                raise ValueError("每个筛选决定须有合法 status 与具体 reason")
            if decision.get("record_id"):
                records = [state["records"].get(decision["record_id"])]
            elif decision.get("report_id"):
                records = [record for record in state["records"].values() if record["report_id"] == decision["report_id"]]
            else:
                raise ValueError("筛选须指定真实 record_id 或 report_id")
            if not records or any(record is None for record in records):
                raise ValueError("筛选指向未实际取得记录")
            if any(name in decision for name in ("work_id", "report_id_override", "study_id")) and not decision.get("identity_reason"):
                raise ValueError("工作/报告/研究身份关联须有人工核验 identity_reason，不能仅凭标题合并")
            if any(name in decision and not decision[name] for name in ("work_id", "report_id_override", "study_id")):
                raise ValueError("明确身份关联不能为空；未知身份使用不填写")
            targets.append((decision, records))
        for decision, records in targets:
            for record in records:
                item = {"at": utc_now(), "status": decision["status"], "reason": decision["reason"],
                        "criterion_revision": len(state["criteria_history"]) - 1,
                        "identity_reason": decision.get("identity_reason"), "previous": record["screening"]["status"]}
                record["screening"]["history"].append(item)
                record["screening"].update(status=item["status"], reason=item["reason"])
                for source_key, dest_key in (("work_id", "work_id"), ("report_id_override", "report_id"), ("study_id", "study_id")):
                    if source_key in decision:
                        record[dest_key] = str(decision[source_key])
                history.append({"record_id": record["record_id"], **item})
        event_id = _event_id(state, "screen")
        event = {"id": event_id, "kind": "screen", "observed_at": utc_now(), "status": "complete",
                 "decisions": history, "input_sha256": digest(inside(root, args.input).read_bytes()), "record_ids": []}
        _store_event(root, args.trace, state, event)
        return {"event": event_id, "decisions": history, "summary": _summary(state)}


def _seed_record(state, seed):
    if seed in state["records"]:
        return state["records"][seed]
    candidates = [record for record in state["records"].values()
                  if seed in {record["work_id"], record["report_id"], record.get("doi"), record.get("openalex")}
                  or seed in record.get("identity_aliases", [])]
    if not candidates:
        raise ValueError("种子须为已实际取得的 record/work/report ID；先 query import/search")
    return sorted(candidates, key=lambda item: item["observed_at"], reverse=True)[0]


def expand(root, args):
    if args.provider == "arxiv":
        raise ValueError("arXiv 元数据 API 不提供引用邻居；使用实际引用列表 import 或所选引用 provider")
    if args.provider == "crossref" and args.direction in {"forward", "both"}:
        raise ValueError("Crossref 此适配器只提供报告所列后向引用；前向请明确选择 OpenAlex，不自动调用另一来源")
    with locked(root, f"query:{args.trace}"):
        state = _load(root, args.trace)
        seed = _seed_record(state, args.seed)
        event_id = _event_id(state, "expand")
        event = {"id": event_id, "kind": "expand", "provider": args.provider, "seed": seed["record_id"],
                 "direction": args.direction, "observed_at": utc_now(), "pages": [], "record_ids": [],
                 "status": "complete", "requested_limit": args.limit, "limitations": [],
                 "scope_revision": len(state["criteria_history"]) - 1}
        page_number = 0
        try:
            if args.provider == "crossref":
                if not seed.get("doi"):
                    raise ValueError("Crossref 后向扩展种子需要核实的 DOI")
                path = "/works/" + quote(seed["doi"], safe="")
            else:
                identifier = seed.get("openalex") or ("https://doi.org/" + seed["doi"] if seed.get("doi") else None)
                if not identifier:
                    raise ValueError("OpenAlex 种子须有 OpenAlex ID 或 DOI；不能靠近似标题猜种子")
                path = "/works/" + quote(identifier, safe="")
            raw, info = _request(root, args.trace, event_id, args.provider, path, {}, page_number,
                                 timeout=args.timeout, interval=args.interval)
            event["pages"].append(info)
            page_number += 1
            if info["error"]:
                raise ValueError(info["error"])
            seed_items, _, _ = parse_response(args.provider, raw)
            if len(seed_items) != 1:
                raise ValueError("引用扩展种子响应不是一个实际工作")
            data = seed_items[0]["metadata"]
            if args.provider == "crossref":
                work = data
                source_id = "doi:" + seed["doi"]
                refs = work.get("reference", [])
                candidates = []
                unidentified = []
                for index, ref in enumerate(refs):
                    doi = _doi(ref.get("DOI"))
                    if not doi:
                        unidentified.append({"index": index, "raw_reference": ref})
                        continue
                    target = f"doi:{doi}"
                    _citation_edge(state, source_id, target, event_id, f"message.reference[{index}]", info["raw_path"], info["raw_sha256"])
                    if len(candidates) < args.limit:
                        candidates.append({"doi": doi, "provider_id": doi, "title": ref.get("article-title"),
                                           "published": ref.get("year"), "version": None, "authors": [],
                                           "url": "https://doi.org/" + doi, "metadata": ref,
                                           "origin": {"seed": source_id, "direction": "backward", "event_id": event_id}})
                event["unidentified_references"] = unidentified
                event["reported_references"] = len(refs)
                event["record_ids"].extend(_ingest(state, event_id, candidates, args.provider, info["observed_at"]))
                if unidentified:
                    event["limitations"].append("未识别 DOI 的原始引用仍保留；不伪造匹配工作。")
                if len(candidates) < sum(bool(_doi(ref.get("DOI"))) for ref in refs):
                    event["limitations"].append("候选数量受请求预算限制；已识别事实引用边全部保留。")
            else:
                actual_seed = _openalex(data.get("id"))
                if not actual_seed:
                    raise ValueError("OpenAlex 种子响应缺少实际工作 ID")
                source_id = f"openalex:{actual_seed}"
                if args.direction in {"backward", "both"}:
                    references = data.get("referenced_works", []) or []
                    candidates = []
                    for index, reference in enumerate(references):
                        target_id = _openalex(reference)
                        if not target_id:
                            event["limitations"].append(f"无法识别实际引用身份：{reference}")
                            continue
                        _citation_edge(state, source_id, f"openalex:{target_id}", event_id, f"referenced_works[{index}]", info["raw_path"], info["raw_sha256"])
                        if len(candidates) < args.limit:
                            candidates.append({"openalex": target_id, "provider_id": reference, "title": None,
                                               "version": None, "authors": [], "url": reference,
                                               "metadata": {"referenced_by": data["id"]},
                                               "origin": {"seed": source_id, "direction": "backward", "event_id": event_id}})
                    event["record_ids"].extend(_ingest(state, event_id + "-backward", candidates, args.provider, info["observed_at"]))
                    event["reported_references"] = len(references)
                    if len(references) > args.limit:
                        event["limitations"].append("后向候选数量受请求预算限制；原始引用边全部保留。")
                    event["limitations"].append("后向候选仅有 API 引用身份，标题/摘要待实际元数据补充，不视为已读。")
                if args.direction in {"forward", "both"}:
                    cursor, consumed = "*", 0
                    for _ in range(args.pages):
                        count = min(args.page_size, args.limit - consumed)
                        if count <= 0:
                            break
                        params = {"filter": f"cites:{actual_seed}", "per_page": count, "cursor": cursor}
                        next_raw, next_info = _request(root, args.trace, event_id, args.provider, "/works", params, page_number,
                                                       timeout=args.timeout, interval=args.interval)
                        page_number += 1
                        event["pages"].append(next_info)
                        if next_info["error"]:
                            event["status"] = "partial"
                            break
                        items, total, next_cursor = parse_response(args.provider, next_raw)
                        next_info.update(returned_records=len(items), reported_total=total, continuation=next_cursor)
                        event["forward_reported_total"] = total
                        for index, item in enumerate(items):
                            citing_id = item.get("openalex")
                            if citing_id:
                                _citation_edge(state, f"openalex:{citing_id}", source_id, event_id,
                                               f"results[{index}] from filter=cites:{actual_seed}", next_info["raw_path"], next_info["raw_sha256"])
                            item["origin"] = {"seed": source_id, "direction": "forward", "event_id": event_id}
                        event["record_ids"].extend(_ingest(state, f"{event_id}-forward-{page_number}", items, args.provider, next_info["observed_at"]))
                        consumed += len(items)
                        if not items or len(items) < count or not next_cursor or next_cursor == cursor:
                            break
                        cursor = next_cursor
                    if event.get("forward_reported_total") is not None and consumed < event["forward_reported_total"]:
                        event["limitations"].append("前向查询未取完 provider 结果集；不能声称覆盖穷尽。")
                    event["forward_retrieved_records"] = consumed
        except (ValueError, TypeError, AttributeError, OSError) as exc:
            event["status"] = "partial" if event["record_ids"] else "failed"
            event["error"] = str(exc)
        finally:
            _store_event(root, args.trace, state, event)
        return {"event": event, "summary": _summary(state)}


def _components(nodes, edges):
    """Iterative Kosaraju SCC, safe for long citation chains."""
    adjacency, reverse = defaultdict(set), defaultdict(set)
    for source, target in edges:
        adjacency[source].add(target)
        reverse[target].add(source)
    seen, order = set(), []
    for node in sorted(nodes):
        if node in seen:
            continue
        seen.add(node)
        stack = [(node, iter(sorted(adjacency[node])))]
        while stack:
            current, remaining = stack[-1]
            child = next(remaining, None)
            if child is None:
                order.append(current)
                stack.pop()
            elif child not in seen:
                seen.add(child)
                stack.append((child, iter(sorted(adjacency[child]))))
    seen, components = set(), []
    for node in reversed(order):
        if node in seen:
            continue
        seen.add(node)
        group, queue = [], [node]
        while queue:
            current = queue.pop()
            group.append(current)
            for child in reverse[current]:
                if child not in seen:
                    seen.add(child)
                    queue.append(child)
        components.append(sorted(group))
    return sorted(components, key=lambda item: item[0])


def graph(root, args):
    with locked(root, f"query:{args.trace}"):
        state = _load(root, args.trace)
        actual_edges = state["citation_edges"]
        # Exact observed identity aliases may be canonicalized only by screening.
        alias_map = {}
        conflicts = set()
        for record in state["records"].values():
            for alias in record.get("identity_aliases", []):
                if alias in alias_map and alias_map[alias] != record["work_id"]:
                    conflicts.add(alias)
                else:
                    alias_map[alias] = record["work_id"]
        for alias in conflicts:
            alias_map.pop(alias, None)
        observed_pairs = {(alias_map.get(edge["source"], edge["source"]), alias_map.get(edge["target"], edge["target"])) for edge in actual_edges}
        nodes = {node for pair in observed_pairs for node in pair}
        nodes.update(record["work_id"] for record in state["records"].values())
        components = _components(nodes, observed_pairs)
        assignments = {node: index for index, group in enumerate(components) for node in group}
        condensed = sorted({(assignments[source], assignments[target]) for source, target in observed_pairs
                            if assignments[source] != assignments[target]})
        graph_data = {"format": "paper-citation-graph-v1", "derived_at": utc_now(),
                      "query_state_sha256": digest(_trace_path(root, args.trace).read_bytes()),
                      "scope": {"trace_id": args.trace, "collection": "all actual retrieved records and observed citation endpoints"},
                      "raw_edges": actual_edges, "observed_identity_aliases": alias_map,
                      "identity_conflicts": sorted(conflicts), "nodes": sorted(nodes),
                      "distinct_citation_pairs": [list(pair) for pair in sorted(observed_pairs)],
                      "scc": [{"component": index, "members": group} for index, group in enumerate(components)],
                      "condensation_edges": [list(pair) for pair in condensed],
                      "limits": ["引用事实不自动成为机制承继或因果改进。", "凝聚图只压缩 SCC，不删除原始引用边。"]}
        if args.projection == "included":
            included = {work for group in _group_records(state) if group["status"] == "纳入" for work in group["work_ids"]}
            graph_data["projection"] = {"kind": "included", "condition": "both endpoints have actually included work IDs",
                                        "nodes": sorted(included), "edges": [list(pair) for pair in sorted(observed_pairs)
                                        if pair[0] in included and pair[1] in included]}
        target = inside(root, f".research/queries/{args.trace}/derived/citation-graph.json")
        write_json(target, graph_data)
        return {"path": str(target.relative_to(root)), "nodes": len(nodes), "raw_edges": len(actual_edges),
                "distinct_pairs": len(observed_pairs), "scc": len(components), "identity_conflicts": sorted(conflicts)}


def delta(root, args):
    with locked(root, f"query:{args.trace}"):
        state = _load(root, args.trace)
        snapshot_dir = inside(root, f".research/queries/{args.trace}/snapshots")
        previous = None
        if args.since:
            previous = read_json(inside(root, args.since))
            if previous.get("trace_id") != args.trace:
                raise ValueError("增量基线必须属于相同研究范围，跨方向不能冒充连续更新")
        elif snapshot_dir.exists():
            candidates = sorted(snapshot_dir.glob("*.json"))
            previous = read_json(candidates[-1]) if candidates else None
        reports = {}
        for group in _group_records(state):
            reports[group["report_id"]] = {"work_ids": group["work_ids"], "study_ids": group["study_ids"],
                                            "status": group["status"], "metadata_versions": group["metadata_versions"]}
        current = {"format": "paper-query-snapshot-v1", "trace_id": args.trace, "at": utc_now(),
                   "question": state["question"], "scope": state["scope"], "criteria_revision": len(state["criteria_history"]) - 1,
                   "last_event_ids": sorted(state["events"]), "reports": reports,
                   "query_state_sha256": digest(_trace_path(root, args.trace).read_bytes())}
        old_reports = previous.get("reports", {}) if previous else {}
        added = sorted(reports.keys() - old_reports.keys())
        changed = sorted(key for key in reports.keys() & old_reports.keys() if reports[key] != old_reports[key])
        new_versions = [key for key in added if set(reports[key]["work_ids"]) & {work for value in old_reports.values() for work in value["work_ids"]}]
        result = {"format": "paper-query-delta-v1", "created_at": utc_now(), "trace_id": args.trace,
                  "baseline_at": previous.get("at") if previous else None,
                  "current_state_sha256": current["query_state_sha256"], "new_reports": added,
                  "new_versions_of_known_work": new_versions, "changed_metadata_or_screening": changed,
                  "removed_reports": sorted(old_reports.keys() - reports.keys()),
                  "criteria_changed": bool(previous and previous["criteria_revision"] != current["criteria_revision"]),
                  "next": "新增/修订报告先核身份及固定全文，再 derive impact 并由模型判断是否改变知识；不会自动裁决。"}
        if args.checkpoint:
            snapshot_id = datetime.now().strftime("%Y%m%d%H%M%S%f")
            target = snapshot_dir / f"{snapshot_id}.json"
            write_json(target, current)
            result["snapshot"] = str(target.relative_to(root))
            write_json(inside(root, f".research/queries/{args.trace}/deltas/{snapshot_id}.json"), result)
        return result


def main(argv=None):
    parser = argparse.ArgumentParser(prog="research.py query", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("init", "search", "import", "screen", "expand", "graph", "status", "delta", "scope", "stop"):
        command = sub.add_parser(name)
        command.add_argument("--root", type=Path, default=default_root())
        command.add_argument("--trace", required=True, type=safe_id)
        if name in {"init", "scope"}:
            command.add_argument("--question", required=name == "init")
            command.add_argument("--scope", required=True)
            command.add_argument("--include", action="append", default=[])
            command.add_argument("--exclude", action="append", default=[])
            command.add_argument("--budget", required=True)
            command.add_argument("--reason", required=name == "scope")
            if name == "init":
                command.add_argument("--mode", choices=("local", "lineage", "systematic"), default="lineage")
        if name in {"search", "expand"}:
            command.add_argument("--provider", choices=PROVIDERS, required=True)
            command.add_argument("--limit", type=int, default=50)
            command.add_argument("--pages", type=int, default=1)
            command.add_argument("--page-size", type=int, default=50)
            command.add_argument("--timeout", type=float, default=45)
            command.add_argument("--interval", type=float)
        if name == "search":
            command.add_argument("--query", required=True)
            command.add_argument("--filter")
            command.add_argument("--sort")
            command.add_argument("--order", choices=("ascending", "descending"))
            command.add_argument("--start", type=int, default=0)
        if name == "import":
            command.add_argument("--provider", choices=(*PROVIDERS, "manual"), required=True)
            command.add_argument("--input", required=True, help="库根相对原始 JSON/XML 文件")
            command.add_argument("--observed-at", required=True, help="实际取得日期，不是当前导入日期")
            command.add_argument("--source", required=True, help="取得者/浏览器页面/实际 API 的描述")
            command.add_argument("--query", required=True)
            command.add_argument("--parameters", help="实际查询参数的库根相对 JSON 文件")
        if name == "screen":
            command.add_argument("--input", required=True)
        if name == "expand":
            command.add_argument("--seed", required=True)
            command.add_argument("--direction", choices=("backward", "forward", "both"), default="both")
        if name == "graph":
            command.add_argument("--projection", choices=("all", "included"), default="all")
        if name == "delta":
            command.add_argument("--since", help="同方向的旧查询快照库根相对路径")
            command.add_argument("--checkpoint", action="store_true", help="保存增量基线和变化记录；不自动查询")
        if name == "status":
            command.add_argument("--records", action="store_true")
            command.add_argument("--reports", action="store_true")
        if name == "stop":
            command.add_argument("--reason", required=True)
            command.add_argument("--coverage", required=True)
            command.add_argument("--missing-route", action="append", default=[])
            command.add_argument("--next-question", action="append", default=[])
            command.add_argument("--saturation", choices=("当前条件下无新增", "未饱和", "尚未判断"), default="未饱和")
    args = parser.parse_args(argv)
    root = args.root.resolve()
    if args.command in {"search", "expand"}:
        if not 1 <= args.limit <= 10000 or not 1 <= args.pages <= 100 or not 1 <= args.page_size <= 100:
            raise ValueError("limit=1..10000, pages=1..100, page-size=1..100；大范围需明确分批预算")
        if not 1 <= args.timeout <= 60:
            raise ValueError("单请求 timeout 须在1..60秒")
        if args.interval is not None and not 0 <= args.interval <= 30:
            raise ValueError("请求间隔须在0..30秒")
    if args.command == "init":
        with locked(root, f"query:{args.trace}"):
            target = _trace_path(root, args.trace)
            if target.exists():
                raise ValueError("已有持久研究范围，请 query status/scope 接续；不重置真实列表")
            criteria = {"at": utc_now(), "scope": args.scope, "include": args.include, "exclude": args.exclude,
                        "budget": args.budget, "reason": "初始研究范围"}
            state = {"format": FORMAT, "trace_id": args.trace, "created_at": utc_now(), "question": args.question,
                     "scope": args.scope, "mode": args.mode, "criteria_history": [criteria],
                     "records": {}, "events": {}, "citation_edges": [], "stop": None}
            _save(root, args.trace, state)
        result = {"state": str(target.relative_to(root)), "summary": _summary(state)}
    elif args.command == "search":
        result = search(root, args)
    elif args.command == "import":
        result = import_records(root, args)
    elif args.command == "screen":
        result = screen(root, args)
    elif args.command == "expand":
        result = expand(root, args)
    elif args.command == "graph":
        result = graph(root, args)
    elif args.command == "delta":
        result = delta(root, args)
    elif args.command == "status":
        state = _load(root, args.trace)
        result = {"summary": _summary(state), "criteria_history": state["criteria_history"], "events": state["events"]}
        if args.records:
            result["records"] = state["records"]
        if args.reports:
            result["reports"] = _group_records(state)
    elif args.command == "scope":
        with locked(root, f"query:{args.trace}"):
            state = _load(root, args.trace)
            state["criteria_history"].append({"at": utc_now(), "scope": args.scope, "include": args.include,
                                               "exclude": args.exclude, "budget": args.budget, "reason": args.reason})
            state["scope"] = args.scope
            if args.question:
                state["question"] = args.question
            state["stop"] = None
            _save(root, args.trace, state)
        result = {"scope_revision": len(state["criteria_history"]) - 1, "summary": _summary(state)}
    else:
        with locked(root, f"query:{args.trace}"):
            state = _load(root, args.trace)
            decision = {"at": utc_now(), "reason": args.reason, "coverage": args.coverage,
                        "missing_routes": args.missing_route, "next_questions": args.next_question,
                        "saturation": args.saturation, "criteria_revision": len(state["criteria_history"]) - 1}
            state.setdefault("stop_history", []).append(decision)
            state["stop"] = decision
            _save(root, args.trace, state)
        result = {"summary": _summary(state), "limits": "零新增仅适用于当前种子、来源和纳排条件，不能证明整个领域穷尽。"}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    event = result.get("event")
    return 1 if isinstance(event, dict) and event.get("status") in {"failed", "partial"} else 0
