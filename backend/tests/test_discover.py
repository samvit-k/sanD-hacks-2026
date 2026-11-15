"""Discovery workflow with mocked HTTP (no real network calls)."""
import httpx
import pytest

from app import db, discover
from app.config import settings

SCHOLAR_RAW = [
    {"title": "Long-term effects of intelligent tutoring", "link": "https://doi.org/10.1234/ITS.2021.5", "year": "2021",
     "cited_by": 40},
    {"title": "Tutoring and retention: a meta-analysis", "url": "https://example.org/meta", "date": "Mar 2019"},
    {"error": "blocked"},
    {"no_title": True},
]
OA_WORK = {"id": "https://openalex.org/W1", "title": "Long-term effects of intelligent tutoring",
           "doi": "https://doi.org/10.1234/its.2021.5", "publication_year": 2021,
           "best_oa_location": {"pdf_url": "https://oa.example/its.pdf", "license": "cc-by"},
           "open_access": {"is_oa": True}, "cited_by_count": 55,
           "abstract_inverted_index": {"Tutoring": [0], "helps": [1]}}
OA_META = {"id": "https://openalex.org/W2", "title": "Tutoring and retention: a meta-analysis",
           "doi": None, "publication_year": 2019, "abstract_inverted_index": None}


def test_parse_scholar_records():
    items = discover.parse_scholar_records(SCHOLAR_RAW)
    assert [i["title"] for i in items] == ["Long-term effects of intelligent tutoring",
                                           "Tutoring and retention: a meta-analysis"]
    assert items[0]["doi"] == "10.1234/its.2021.5" and items[0]["year"] == 2021
    assert items[1]["doi"] is None and items[1]["year"] == 2019
    assert discover.parse_scholar_records([{"organic": SCHOLAR_RAW[:1]}])[0]["year"] == 2021


def test_reconstruct_abstract_and_dedupe():
    assert discover.reconstruct_abstract({"b": [1], "a": [0]}) == "a b"
    rs = [{"title": "X", "doi": "10.1/a"}, {"title": "x", "doi": None}, {"title": "Y", "doi": "10.1/lib"}]
    assert discover.dedupe(rs, ["10.1/LIB"], 10) == [{"title": "X", "doi": "10.1/a"}]


def _transport(calls, bd_status="ready", bd_fail=False, progress_code=200):
    def handler(req: httpx.Request):
        calls.append(str(req.url))
        u = req.url
        if u.host == "api.brightdata.com":
            if bd_fail:
                return httpx.Response(500, text="boom")
            assert req.headers["Authorization"] == "Bearer tok"
            if u.path.endswith("/trigger"):
                assert u.params["dataset_id"] == "gd_test"
                return httpx.Response(200, json={"snapshot_id": "s_1"})
            if "/progress/" in u.path:
                if progress_code != 200:
                    return httpx.Response(progress_code, text="error")
                return httpx.Response(200, json={"status": bd_status})
            if "/snapshot/" in u.path:
                return httpx.Response(200, json=SCHOLAR_RAW)
        if u.host == "api.openalex.org":
            if u.path.startswith("/works/doi:"):
                return httpx.Response(200, json=OA_WORK)
            return httpx.Response(200, json={"results": [OA_META]})
        return httpx.Response(404)
    return httpx.MockTransport(handler)


@pytest.fixture
def bd_on(monkeypatch):
    monkeypatch.setattr(settings, "brightdata_token", "tok")
    monkeypatch.setattr(settings, "brightdata_dataset_id", "gd_test")


def test_brightdata_flow_resolves_and_caches(clean_db, bd_on):
    calls = []
    client = httpx.Client(transport=_transport(calls))
    res = discover.discover("ai tutor retention", for_paper_id=None, wait_s=1, client=client)
    assert res["source"] == "brightdata" and not res["cached"]
    first = res["results"][0]
    assert first["resolved"] and first["oa_url"] == "https://oa.example/its.pdf" and first["abstract"] == "Tutoring helps"
    assert first["scholar_cited_by"] == 40
    assert res["results"][1]["openalex_id"] == "https://openalex.org/W2"
    n = len(calls)
    again = discover.discover("AI  tutor retention", wait_s=1, client=client)  # normalized query hits cache
    assert again["cached"] and len(calls) == n


def test_falls_back_to_openalex_when_brightdata_fails(clean_db, bd_on):
    calls = []
    res = discover.discover("ai tutor", wait_s=1, client=httpx.Client(transport=_transport(calls, bd_fail=True)))
    assert res["source"] == "openalex" and "Bright Data unavailable" in res["fallback_reason"]


def test_falls_back_when_snapshot_times_out(clean_db, bd_on, monkeypatch):
    monkeypatch.setattr(discover.time, "sleep", lambda s: None)
    res = discover.discover("ai tutor", wait_s=0, client=httpx.Client(transport=_transport([], bd_status="running")))
    assert res["source"] == "openalex" and "not ready" in res["fallback_reason"]


def test_not_configured_uses_openalex_and_excludes_library(clean_db, monkeypatch):
    monkeypatch.setattr(settings, "brightdata_token", "")
    with db.connect() as conn:
        db.insert_paper(conn, {"id": "lib", "title": "Library paper", "doi": "10.1234/its.2021.5"})
    calls = []
    res = discover.discover("ai tutor", client=httpx.Client(transport=_transport(calls)))
    assert res["source"] == "openalex" and res["fallback_reason"] == "Bright Data not configured"
    assert not any("brightdata" in c for c in calls)


def test_shared_query_is_linked_to_each_requesting_paper(clean_db, bd_on):
    with db.connect() as conn:
        db.insert_paper(conn, {"id": "pd", "title": "Paper D"})
        db.insert_paper(conn, {"id": "pe", "title": "Paper E"})
    calls = []
    client = httpx.Client(transport=_transport(calls))
    r1 = discover.discover("ai tutor retention", for_paper_id="pd", wait_s=1, client=client)
    n = len(calls)
    r2 = discover.discover("ai tutor retention", for_paper_id="pe", wait_s=1, client=client)
    assert len(calls) == n and r2["cached"]  # second paper reuses the cache...
    assert (r1["for_paper_id"], r2["for_paper_id"]) == ("pd", "pe")  # ...but gets its own association
    with db.connect() as conn:
        d, e = db.latest_discovery_for(conn, "pd"), db.latest_discovery_for(conn, "pe")
        assert d["for_paper_id"] == "pd" and e["for_paper_id"] == "pe"
        assert d["results"] == e["results"]
        links = conn.execute("SELECT paper_id FROM discovery_links ORDER BY paper_id").fetchall()
        assert [l["paper_id"] for l in links] == ["pd", "pe"]


def test_discovery_for_one_paper_never_shows_on_another(clean_db, bd_on):
    with db.connect() as conn:
        db.insert_paper(conn, {"id": "pd", "title": "Paper D"})
        db.insert_paper(conn, {"id": "pe", "title": "Paper E"})
    discover.discover("query one", for_paper_id="pd", wait_s=1, client=httpx.Client(transport=_transport([])))
    with db.connect() as conn:
        assert db.latest_discovery_for(conn, "pe") is None


def test_unknown_paper_id_rejected(clean_db, bd_on):
    with pytest.raises(ValueError, match="unknown paper_id"):
        discover.discover("q", for_paper_id="nope", client=httpx.Client(transport=_transport([])))


def test_polling_client_error_falls_back_immediately(clean_db, bd_on, monkeypatch):
    sleeps = []
    monkeypatch.setattr(discover.time, "sleep", sleeps.append)
    calls = []
    res = discover.discover("ai tutor", wait_s=600, client=httpx.Client(transport=_transport(calls, progress_code=401)))
    assert res["source"] == "openalex" and "HTTP 401" in res["fallback_reason"]
    assert sum("/progress/" in c for c in calls) == 1 and sleeps == []  # no waiting on a hopeless snapshot


def test_polling_server_errors_retry_briefly_then_fall_back(clean_db, bd_on, monkeypatch):
    sleeps = []
    monkeypatch.setattr(discover.time, "sleep", sleeps.append)
    calls = []
    res = discover.discover("ai tutor", wait_s=600, client=httpx.Client(transport=_transport(calls, progress_code=503)))
    assert res["source"] == "openalex" and "3 consecutive errors" in res["fallback_reason"]
    assert sum("/progress/" in c for c in calls) == 3 and len(sleeps) == 2  # bounded, far below the 600s timeout


def test_network_error_during_polling_falls_back(clean_db, bd_on):
    def handler(req):
        if req.url.host == "api.brightdata.com" and "/progress/" in req.url.path:
            raise httpx.ConnectError("connection reset")
        return _transport([]).handle_request(req)
    res = discover.discover("ai tutor", wait_s=600, client=httpx.Client(transport=httpx.MockTransport(handler)))
    assert res["source"] == "openalex" and "connection reset" in res["fallback_reason"]


def test_bd_input_template(monkeypatch):
    monkeypatch.setattr(settings, "brightdata_input_template", '{"keyword": "{query}", "pages": 1}')
    assert discover.bd_input("tutors") == {"keyword": "tutors", "pages": 1}
