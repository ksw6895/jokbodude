import sys
import types

# Provide a lightweight redis stub so storage_manager can be imported without the real dependency.
redis_stub = types.SimpleNamespace(
    from_url=lambda *args, **kwargs: None,
    exceptions=types.SimpleNamespace(ConnectionError=Exception, TimeoutError=Exception),
)
sys.modules.setdefault("redis", redis_stub)

from pdf_processor.utils.billing import BillingConverter
from storage_manager import StorageManager


class DummyPipeline:
    def __init__(self, redis_client):
        self._redis = redis_client

    def hincrby(self, key, field, value):
        self._redis.hincrby(key, field, value)
        return self

    def hset(self, key, field, value):
        self._redis.hset(key, field, value)
        return self

    def expire(self, key, ttl):
        self._redis.expire(key, ttl)
        return self

    def execute(self):
        return True


class DummyRedis:
    def __init__(self):
        self._hashes: dict[str, dict[str, int]] = {}
        self._strings: dict[str, int] = {}

    def pipeline(self):
        return DummyPipeline(self)

    def hincrby(self, key, field, value):
        table = self._hashes.setdefault(key, {})
        table[field] = int(table.get(field, 0)) + int(value)
        return table[field]

    def hset(self, key, field, value):
        table = self._hashes.setdefault(key, {})
        table[field] = int(value)
        return 1

    def hgetall(self, key):
        table = self._hashes.get(key, {})
        return {k: str(v).encode() for k, v in table.items()}

    def expire(self, key, ttl):
        return True

    def incrby(self, key, value):
        self._strings[key] = int(self._strings.get(key, 0)) + int(value)
        return self._strings[key]


class FakeStorageManager(StorageManager):
    def __init__(self):  # type: ignore[super-init-not-called]
        self.use_local_only = False
        self.redis_client = DummyRedis()
        self._with_retry = lambda func, *args, **kwargs: func(*args, **kwargs)  # type: ignore

    def add_user_tokens(self, user_id: str, delta: int):  # type: ignore[override]
        key = f"user:{user_id}:tokens"
        return self.redis_client.incrby(key, delta)


def test_billing_converter_uses_pricing_with_usage_metadata(monkeypatch):
    monkeypatch.delenv("JD_TOKEN_MULTIPLIER_DEFAULT", raising=False)
    BillingConverter._load_multipliers.cache_clear()
    BillingConverter._usd_per_jd.cache_clear()

    usage = types.SimpleNamespace(
        input_token_count=1000,
        output_token_count=400,
        total_token_count=1400,
    )
    converted = BillingConverter.api_to_jd(1400, "flash", usage)
    assert converted == 1


def test_billing_converter_applies_model_multiplier(monkeypatch):
    monkeypatch.setenv("JD_TOKEN_MULTIPLIER_PRO", "2")
    try:
        BillingConverter._load_multipliers.cache_clear()
        BillingConverter._usd_per_jd.cache_clear()
        usage = types.SimpleNamespace(
            input_token_count=10_000,
            output_token_count=4_000,
            total_token_count=14_000,
        )
        converted = BillingConverter.api_to_jd(14_000, "gemini-pro", usage)
        assert converted == 53
    finally:
        monkeypatch.delenv("JD_TOKEN_MULTIPLIER_PRO", raising=False)
        BillingConverter._load_multipliers.cache_clear()
        BillingConverter._usd_per_jd.cache_clear()


def test_billing_converter_falls_back_when_usage_missing(monkeypatch):
    BillingConverter._load_multipliers.cache_clear()
    BillingConverter._usd_per_jd.cache_clear()
    monkeypatch.delenv("JD_TOKEN_MULTIPLIER_DEFAULT", raising=False)

    converted = BillingConverter.api_to_jd(1000, "flash", None)
    assert converted == 1


def test_storage_manager_records_and_refunds_streaming_usage():
    sm = FakeStorageManager()
    job_id = "job-stream"

    # Initial streamed charge
    sm.record_token_usage(job_id, 2, gemini_tokens_total=250, jd_tokens_total=2, debited_delta=2)
    details = sm.get_token_usage_details(job_id)
    assert details["jd_tokens_total"] == 2
    assert details["jd_tokens_debited_total"] == 2
    assert details["jd_tokens_running_total"] == 2
    assert details["gemini_tokens_total"] == 250
    assert sm.get_outstanding_token_charge(job_id) == 0

    # Additional usage without debit yet
    sm.record_token_usage(job_id, 1, gemini_tokens_total=360, jd_tokens_total=3, debited_delta=0)
    details = sm.get_token_usage_details(job_id)
    assert details["jd_tokens_total"] == 3
    assert details["jd_tokens_debited_total"] == 2
    assert sm.get_outstanding_token_charge(job_id) == 1

    # Mark the outstanding charge as debited
    sm.record_token_usage(job_id, 0, jd_tokens_total=3, debited_delta=1)
    assert sm.get_outstanding_token_charge(job_id) == 0

    # Refund everything and ensure no outstanding balance remains
    assert sm.refund_token_usage(job_id, "userA")
    details = sm.get_token_usage_details(job_id)
    assert details["jd_tokens_refunded_total"] == 3
    assert sm.get_outstanding_token_charge(job_id) == 0
