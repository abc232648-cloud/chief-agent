from pathlib import Path

from browser.sources import JsonJobSource, SourceRegistry


def test_json_source_preserves_provenance(tmp_path: Path):
    path = tmp_path / "jobs.json"
    path.write_text(
        '[{"title":"Junior SOC Analyst","company":"Example","url":"https://example.test/job/1"}]',
        encoding="utf-8",
    )
    listings = JsonJobSource(path).fetch()
    assert len(listings) == 1
    assert listings[0].source == "json"
    assert listings[0].url == "https://example.test/job/1"
    assert listings[0].as_raw_job()["title"] == "Junior SOC Analyst"


def test_registry_combines_sources(tmp_path: Path):
    path = tmp_path / "jobs.json"
    path.write_text('[{"title":"A"},{"title":"B"}]', encoding="utf-8")
    registry = SourceRegistry([JsonJobSource(path)])
    assert [x.payload["title"] for x in registry.fetch_all()] == ["A", "B"]
