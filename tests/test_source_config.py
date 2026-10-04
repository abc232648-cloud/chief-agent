from pathlib import Path

import pytest

from config.source_config import SourceConfigError, load_sources
from browser.sources.controlled import ControlledPlaywrightJobSource, ControlledSourceError


ROOT = Path(__file__).resolve().parents[1]


def test_load_approved_sources():
    sources = load_sources(ROOT / "config" / "sources.json")
    assert {s.name for s in sources} == {"linkedin", "upwork"}
    assert all(s.read_only for s in sources)


def test_config_rejects_non_read_only(tmp_path):
    path = tmp_path / "sources.json"
    path.write_text('{"sources":[{"name":"x","domains":["example.com"],"read_only":false}]}')
    with pytest.raises(SourceConfigError):
        load_sources(path)


def test_controlled_source_rejects_unapproved_url():
    source = load_sources(ROOT / "config" / "sources.json")[0]
    reader = ControlledPlaywrightJobSource(source, ())
    with pytest.raises(ControlledSourceError):
        reader.validate_url("https://evil.example/job")


def test_controlled_source_accepts_subdomain():
    source = load_sources(ROOT / "config" / "sources.json")[0]
    reader = ControlledPlaywrightJobSource(source, ())
    reader.validate_url("https://www.linkedin.com/jobs/view/123")
