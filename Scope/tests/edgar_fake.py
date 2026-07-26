"""Deterministic stand-in for EDGAR, backed by `fixtures/edgar/`.

Provenance of the fixtures:
  * `search_from*.json` — the real EDGAR full-text-search envelope, recorded
    live from https://efts.sec.gov/LATEST/search-index on 2026-07-26 (took /
    _shards / hits.total shape preserved), with the 100 real hits replaced by
    five controlled filings so the assertions are deterministic.
  * `f*.xml`, `h*.xml` — hand-authored to the SEC `ownershipDocument` (Form 4)
    schema. These are NOT captured traffic; they exercise the parser's paths
    (significant / below-threshold / award-only / unparseable), not EDGAR's
    real document variety.
  * `submissions_*.json` — hand-authored to the `data.sec.gov/submissions`
    shape (`filings.recent.form` / `.accessionNumber`).

Nothing here reaches the network, so tests using it prove the rule's control
flow — never its behaviour against live EDGAR.
"""
from __future__ import annotations

import json
import pathlib

FIXTURES = pathlib.Path(__file__).resolve().parent / "fixtures" / "edgar"

# The five filings the fake search returns, in fixture order.
ALL_ADSH = [
    "0001000001-26-000001",   # f1 — $1.25M sold, 12.5x history  -> CRITICAL
    "0001000002-26-000002",   # f2 — $10K, below MIN_VALUE       -> no alert
    "0001000003-26-000003",   # f3 — award only, no P/S          -> no alert
    "0001000004-26-000004",   # f4 — $200K bought, 2.5x history  -> HIGH
    "0001000005-26-000005",   # f5 — truncated XML               -> parse_failed
]

# Accession prefix (dashes stripped, first 10 chars) -> prior-filing document.
HISTORY_DOC = {"0001000001": "h1.xml", "0001000004": "h4.xml"}


class FakeClock:
    """Stands in for the `time` module inside rule_06_form4.

    The real run's wall clock is dominated by `SLEEP` before every HTTP call, so
    charging a fixed cost per fake call models it faithfully and lets the time
    budget be tested without waiting.
    """

    def __init__(self, start: float = 1_000_000.0, cost_per_call: float = 0.0):
        self.now = start
        self.cost_per_call = cost_per_call

    def time(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds

    def advance(self, seconds: float) -> None:
        self.now += seconds


class FakeEdgar:
    """Callable drop-in for `rule_06_form4._get`."""

    def __init__(
        self,
        search_status: int = 200,
        document_status: int = 200,
        raise_on_filing_fetch: int | None = None,
        exception: BaseException | None = None,
        clock: FakeClock | None = None,
        on_filing_fetch=None,
    ):
        # Called as on_filing_fetch(n) before the nth primary document is served,
        # so a test can observe committed DB state from *inside* a running scan.
        self.on_filing_fetch = on_filing_fetch
        self.search_status = search_status
        self.document_status = document_status
        # Counted over primary Form 4 documents only, so a test can say "die on
        # the 3rd filing" without counting the history lookups in between.
        self.raise_on_filing_fetch = raise_on_filing_fetch
        self.exception = exception
        self.clock = clock

        self.search_calls = 0
        self.search_params: list[dict] = []
        self.document_calls: list[str] = []

    # -- accounting -------------------------------------------------------

    @property
    def filing_fetches(self) -> list[str]:
        """Primary Form 4 documents fetched (excludes history lookups)."""
        return [name for name in self.document_calls if name.startswith("f")]

    @property
    def history_fetches(self) -> list[str]:
        return [name for name in self.document_calls if name.startswith("h")]

    # -- transport --------------------------------------------------------

    def __call__(self, url: str, params: dict | None = None) -> "FakeResponse":
        if self.clock is not None:
            self.clock.advance(self.clock.cost_per_call)

        if url.endswith("/search-index"):
            return self._search(params or {})
        if "/submissions/" in url:
            return self._submissions(url)
        return self._archive(url)

    def _search(self, params: dict) -> "FakeResponse":
        self.search_calls += 1
        self.search_params.append(dict(params))
        if self.search_status != 200:
            return FakeResponse(self.search_status, "")
        offset = int(params.get("from", 0) or 0)
        name = "search_from0.json" if offset == 0 else "search_from3.json"
        return FakeResponse(200, (FIXTURES / name).read_text())

    def _submissions(self, url: str) -> "FakeResponse":
        cik = url.split("CIK")[-1].removesuffix(".json")
        path = FIXTURES / f"submissions_{cik}.json"
        if not path.exists():
            return FakeResponse(404, "")
        return FakeResponse(200, path.read_text())

    def _archive(self, url: str) -> "FakeResponse":
        segments = url.rstrip("/").split("/")
        last, accession = segments[-1], segments[-2]

        if last == "index.json":
            document = HISTORY_DOC.get(accession[:10])
            if document is None:
                return FakeResponse(404, "")
            return FakeResponse(
                200, json.dumps({"directory": {"item": [{"name": document}]}})
            )

        self.document_calls.append(last)

        if self.on_filing_fetch is not None and last.startswith("f"):
            self.on_filing_fetch(len(self.filing_fetches))

        if (self.raise_on_filing_fetch is not None
                and last.startswith("f")
                and self.raise_on_filing_fetch == len(self.filing_fetches)):
            raise self.exception or RuntimeError("connection reset by peer")
        if self.document_status != 200:
            return FakeResponse(self.document_status, "")

        path = FIXTURES / last
        if not path.exists():
            return FakeResponse(404, "")
        return FakeResponse(200, path.read_text())


class FakeResponse:
    def __init__(self, status_code: int, text: str = ""):
        self.status_code = status_code
        self.text = text

    def json(self):
        return json.loads(self.text)
