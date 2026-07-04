from __future__ import annotations

import json
import re

_FALLBACK_TTL = 60.0


def parse_cooldown_ttl(raw: str, *, seconds_to_midnight: float) -> tuple[float, str]:
    """Из строки metadata.raw (формат ошибки Google) вернуть (ttl_сек, вид).

    вид: 'rpm' | 'rpd' | 'unknown'. RPM → retryDelay; RPD → до полуночи Pacific
    (retryDelay для RPD врёт); иначе → фикс-60с. Парсинг защитный: любой сбой → unknown.
    """
    try:
        data = json.loads(raw)
        details = data.get("error", {}).get("details", [])
        quota_id = ""
        retry_delay = None
        for d in details:
            t = d.get("@type", "")
            if "QuotaFailure" in t:
                viol = d.get("violations", [{}])
                quota_id = viol[0].get("quotaId", "") if viol else ""
            elif "RetryInfo" in t:
                rd = d.get("retryDelay", "")  # напр. "38s"
                m = re.match(r"(\d+(?:\.\d+)?)s", rd)
                if m:
                    retry_delay = float(m.group(1))
        if "PerDay" in quota_id:
            return seconds_to_midnight, "rpd"
        if "PerMinute" in quota_id:
            return (retry_delay if retry_delay is not None else _FALLBACK_TTL), "rpm"
        return _FALLBACK_TTL, "unknown"
    except Exception:
        return _FALLBACK_TTL, "unknown"
