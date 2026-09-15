from __future__ import annotations

import json
import math
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
from typing import Iterable
from zoneinfo import ZoneInfo

import requests


TZ = ZoneInfo("Asia/Shanghai")
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
EM_FIELDS = "f2,f3,f6,f8,f12,f14,f15,f16,f17,f18,f20,f100,f124"
EM_FILTER = "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23"


def number(value):
    if value in (None, "", "-", "--"):
        return None
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except (TypeError, ValueError):
        return None


def mainboard(code: str, name: str = "") -> bool:
    code = str(code).zfill(6)
    upper = str(name or "").upper()
    return (
        code.startswith(("600", "601", "603", "605", "000", "001", "002", "003"))
        and "ST" not in upper
        and "退" not in upper
    )


def symbol(code: str) -> str:
    code = str(code).zfill(6)
    return ("sh" if code.startswith("6") else "sz") + code


class MarketClient:
    def __init__(self, timeout: int = 15, workers: int = 6):
        self.timeout = timeout
        self.workers = workers
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": UA, "Referer": "https://quote.eastmoney.com/"})
        self._cninfo_lock = threading.Lock()
        self._cninfo_last = 0.0

    def _market_page(self, page: int) -> dict:
        params = {
            "pn": page,
            "pz": 100,
            "po": 1,
            "np": 1,
            "fltt": 2,
            "invt": 2,
            "fid": "f12",
            "fs": EM_FILTER,
            "fields": EM_FIELDS,
        }
        last_error = None
        for host in ("push2.eastmoney.com", "push2delay.eastmoney.com"):
            try:
                response = self.session.get(
                    f"https://{host}/api/qt/clist/get", params=params, timeout=self.timeout
                )
                response.raise_for_status()
                payload = response.json().get("data") or {}
                if payload.get("diff") is not None:
                    return payload
                raise ValueError("行情页缺少 diff")
            except Exception as exc:  # source failover is intentional
                last_error = exc
        raise RuntimeError(f"东方财富行情第 {page} 页失败: {last_error}")

    def market_snapshot(self) -> tuple[list[dict], dict]:
        first = self._market_page(1)
        total = int(first.get("total") or 0)
        if total < 1000:
            raise RuntimeError(f"行情声明总数异常: {total}")
        rows = list(first.get("diff") or [])
        errors = []
        pages = range(2, math.ceil(total / 100) + 1)
        with ThreadPoolExecutor(max_workers=min(self.workers, 4)) as pool:
            futures = {pool.submit(self._market_page, page): page for page in pages}
            for future in as_completed(futures):
                try:
                    rows.extend(future.result().get("diff") or [])
                except Exception as exc:
                    errors.append({"page": futures[future], "error": str(exc)})
        by_code = {}
        for raw in rows:
            code = str(raw.get("f12") or "").zfill(6)
            name = str(raw.get("f14") or "")
            stamp = number(raw.get("f124"))
            by_code[code] = {
                "code": code,
                "name": name,
                "price": number(raw.get("f2")),
                "change_pct": number(raw.get("f3")),
                "amount": number(raw.get("f6")),
                "turnover_rate": number(raw.get("f8")),
                "high": number(raw.get("f15")),
                "low": number(raw.get("f16")),
                "open": number(raw.get("f17")),
                "previous_close": number(raw.get("f18")),
                "market_cap": number(raw.get("f20")),
                "industry": str(raw.get("f100") or "").strip() or None,
                "market_time": datetime.fromtimestamp(stamp, TZ).isoformat(timespec="seconds") if stamp else None,
            }
        coverage = len(by_code) / total if total else 0
        if errors or coverage < 0.95:
            raise RuntimeError(f"全市场行情不完整: {len(by_code)}/{total}, 失败页 {len(errors)}")
        output = [row for row in by_code.values() if mainboard(row["code"], row["name"])]
        return output, {
            "source": "东方财富沪深A股行情",
            "source_url": "https://push2.eastmoney.com/api/qt/clist/get",
            "declared_rows": total,
            "received_rows": len(by_code),
            "mainboard_rows": len(output),
            "coverage": coverage,
        }

    def index_quotes(self) -> list[dict]:
        mapping = {"sh000001": ("000001.SH", "上证指数"), "sz399001": ("399001.SZ", "深证成指"), "sh000300": ("000300.SH", "沪深300")}
        response = self.session.get(
            "https://qt.gtimg.cn/q=" + ",".join(mapping),
            headers={"User-Agent": UA, "Referer": "https://gu.qq.com/"},
            timeout=self.timeout,
        )
        response.raise_for_status()
        output = []
        for line in response.content.decode("gbk", "ignore").split(";"):
            if "=" not in line or '"' not in line:
                continue
            key = line.split("=")[0].split("_")[-1]
            values = line.split('"')[1].split("~")
            if key not in mapping or len(values) < 35:
                continue
            stamp = None
            try:
                stamp = datetime.strptime(values[30], "%Y%m%d%H%M%S").replace(tzinfo=TZ).isoformat(timespec="seconds")
            except ValueError:
                pass
            code, fallback_name = mapping[key]
            output.append({"code": code, "name": values[1] or fallback_name, "price": number(values[3]), "change_pct": number(values[32]), "market_time": stamp})
        return output

    def daily(self, code: str, count: int = 90, end: date | None = None) -> list[dict]:
        end = end or datetime.now(TZ).date()
        start = end - timedelta(days=max(180, count * 2))
        ticker = symbol(code)
        response = self.session.get(
            "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get",
            params={"param": f"{ticker},day,{start.isoformat()},{end.isoformat()},{count},qfq"},
            headers={"User-Agent": UA, "Referer": "https://gu.qq.com/"},
            timeout=self.timeout,
        )
        response.raise_for_status()
        block = (response.json().get("data") or {}).get(ticker) or {}
        values = block.get("qfqday") or block.get("day") or []
        parsed = []
        for item in values:
            if len(item) < 6:
                continue
            parsed.append(
                {
                    "date": str(item[0])[:10],
                    "open": number(item[1]),
                    "close": number(item[2]),
                    "high": number(item[3]),
                    "low": number(item[4]),
                    "volume": number(item[5]),
                }
            )
        if len(parsed) < 21:
            raise ValueError(f"{code} 日K不足21根")
        return parsed

    def minutes(self, code: str, count: int = 320) -> list[dict]:
        ticker = symbol(code)
        response = self.session.get(
            "https://ifzq.gtimg.cn/appstock/app/kline/mkline",
            params={"param": f"{ticker},m1,,{count}"},
            headers={"User-Agent": UA, "Referer": "https://gu.qq.com/"},
            timeout=self.timeout,
        )
        response.raise_for_status()
        values = ((response.json().get("data") or {}).get(ticker) or {}).get("m1") or []
        parsed = []
        for item in values:
            if len(item) < 6:
                continue
            try:
                stamp = datetime.strptime(str(item[0]), "%Y%m%d%H%M").replace(tzinfo=TZ)
            except ValueError:
                continue
            parsed.append(
                {
                    "time": stamp.isoformat(timespec="minutes"),
                    "open": number(item[1]),
                    "close": number(item[2]),
                    "high": number(item[3]),
                    "low": number(item[4]),
                    "volume": number(item[5]) or 0,
                }
            )
        return parsed

    def details(self, codes: Iterable[str], trade_date: date) -> tuple[dict, list[dict]]:
        details, errors = {}, []

        def fetch(code):
            return code, self.daily(code, end=trade_date), self.minutes(code)

        with ThreadPoolExecutor(max_workers=self.workers) as pool:
            futures = {pool.submit(fetch, code): code for code in codes}
            for future in as_completed(futures):
                code = futures[future]
                try:
                    _, daily, minute = future.result()
                    details[code] = {"daily": daily, "minute": minute}
                except Exception as exc:
                    errors.append({"code": code, "error": f"{type(exc).__name__}: {exc}"})
        return details, errors

    def announcements(self, code: str, end: date, lookback_days: int = 10) -> list[dict]:
        begin = end - timedelta(days=lookback_days)
        org = ("gssh0" if str(code).startswith("6") else "gssz0") + str(code).zfill(6)
        payload = {
            "stock": f"{code},{org}",
            "tabName": "fulltext",
            "pageSize": "20",
            "pageNum": "1",
            "column": "",
            "category": "",
            "plate": "",
            "seDate": f"{begin.isoformat()}~{end.isoformat()}",
            "searchkey": "",
            "secid": "",
            "sortName": "",
            "sortType": "",
            "isHLtitle": "true",
        }
        with self._cninfo_lock:
            delay = 0.45 - (time.monotonic() - self._cninfo_last)
            if delay > 0:
                time.sleep(delay)
            response = self.session.post(
                "https://www.cninfo.com.cn/new/hisAnnouncement/query",
                data=payload,
                headers={
                    "User-Agent": UA,
                    "Referer": "https://www.cninfo.com.cn/new/disclosure",
                    "Origin": "https://www.cninfo.com.cn",
                },
                timeout=self.timeout,
            )
            self._cninfo_last = time.monotonic()
        response.raise_for_status()
        data = response.json()
        raw_items = data.get("announcements")
        if raw_items is None and int(data.get("totalAnnouncement") or 0) > 0:
            raise ValueError("巨潮响应声明有公告但缺少明细")
        output = []
        for item in raw_items or []:
            stamp = item.get("announcementTime")
            published = datetime.fromtimestamp(stamp / 1000, TZ).isoformat(timespec="minutes") if stamp else None
            path = str(item.get("adjunctUrl") or "")
            title = str(item.get("announcementTitle") or "").replace("<em>", "").replace("</em>", "")
            output.append(
                {
                    "title": title,
                    "published_at": published,
                    "source": "巨潮资讯正式公告",
                    "source_url": "https://static.cninfo.com.cn/" + path.lstrip("/") if path else "https://www.cninfo.com.cn/",
                }
            )
        return output


def load_json(path):
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (FileNotFoundError, json.JSONDecodeError):
        return None
