from lecturelog.infrastructure.llm.rate_limit import parse_cooldown_ttl

# Реальные образцы из spike (2026-07-04).
RPD_RAW = (
    '{"error":{"code":429,"status":"RESOURCE_EXHAUSTED","details":['
    '{"@type":"type.googleapis.com/google.rpc.QuotaFailure","violations":['
    '{"quotaId":"GenerateRequestsPerDayPerProjectPerModel-FreeTier"}]},'
    '{"@type":"type.googleapis.com/google.rpc.RetryInfo","retryDelay":"58s"}]}}'
)
RPM_RAW = (
    '{"error":{"code":429,"status":"RESOURCE_EXHAUSTED","details":['
    '{"@type":"type.googleapis.com/google.rpc.QuotaFailure","violations":['
    '{"quotaId":"GenerateRequestsPerMinutePerProjectPerModel-FreeTier"}]},'
    '{"@type":"type.googleapis.com/google.rpc.RetryInfo","retryDelay":"38s"}]}}'
)
# retryDelay = null (битый формат) — RPD не должен зависеть от него.
RPD_RAW_NULL_RETRY_DELAY = (
    '{"error":{"code":429,"status":"RESOURCE_EXHAUSTED","details":['
    '{"@type":"type.googleapis.com/google.rpc.QuotaFailure","violations":['
    '{"quotaId":"GenerateRequestsPerDayPerProjectPerModel-FreeTier"}]},'
    '{"@type":"type.googleapis.com/google.rpc.RetryInfo","retryDelay":null}]}}'
)
# retryDelay отсутствует вовсе.
RPD_RAW_NO_RETRY_DELAY = (
    '{"error":{"code":429,"status":"RESOURCE_EXHAUSTED","details":['
    '{"@type":"type.googleapis.com/google.rpc.QuotaFailure","violations":['
    '{"quotaId":"GenerateRequestsPerDayPerProjectPerModel-FreeTier"}]},'
    '{"@type":"type.googleapis.com/google.rpc.RetryInfo"}]}}'
)
# retryDelay в непривычном формате слов ("38seconds" вместо "38s") при RPM.
RPM_RAW_BAD_RETRY_DELAY = (
    '{"error":{"code":429,"status":"RESOURCE_EXHAUSTED","details":['
    '{"@type":"type.googleapis.com/google.rpc.QuotaFailure","violations":['
    '{"quotaId":"GenerateRequestsPerMinutePerProjectPerModel-FreeTier"}]},'
    '{"@type":"type.googleapis.com/google.rpc.RetryInfo","retryDelay":"38seconds"}]}}'
)


def test_rpm_uses_retry_delay():
    ttl, kind = parse_cooldown_ttl(RPM_RAW, seconds_to_midnight=1000.0)
    assert kind == "rpm"
    assert 30 <= ttl <= 60  # retryDelay 38s


def test_rpd_uses_midnight_not_retry_delay():
    ttl, kind = parse_cooldown_ttl(RPD_RAW, seconds_to_midnight=1000.0)
    assert kind == "rpd"
    assert ttl == 1000.0  # игнорируем врущий retryDelay 58s


def test_unparseable_falls_back_to_60():
    ttl, kind = parse_cooldown_ttl("не json", seconds_to_midnight=1000.0)
    assert kind == "unknown"
    assert ttl == 60.0


def test_rpd_with_null_retry_delay_still_uses_midnight():
    # retryDelay=null не должен ронять весь парсинг и терять уже найденный RPD.
    ttl, kind = parse_cooldown_ttl(RPD_RAW_NULL_RETRY_DELAY, seconds_to_midnight=1000.0)
    assert kind == "rpd"
    assert ttl == 1000.0


def test_rpd_with_missing_retry_delay_still_uses_midnight():
    ttl, kind = parse_cooldown_ttl(RPD_RAW_NO_RETRY_DELAY, seconds_to_midnight=1000.0)
    assert kind == "rpd"
    assert ttl == 1000.0


def test_rpm_with_malformed_retry_delay_falls_back_to_60():
    # "38seconds" не матчится якорным regex — используем фикс-fallback, но kind
    # остаётся "rpm" (квота распознана верно).
    ttl, kind = parse_cooldown_ttl(RPM_RAW_BAD_RETRY_DELAY, seconds_to_midnight=1000.0)
    assert kind == "rpm"
    assert ttl == 60.0
