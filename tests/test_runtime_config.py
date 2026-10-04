import os

from config.runtime import load_environment


def test_load_environment_reads_local_dotenv(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("TEST_RUNTIME_VALUE=from-dotenv\n")
    monkeypatch.delenv("TEST_RUNTIME_VALUE", raising=False)

    load_environment(env_file)

    assert os.environ["TEST_RUNTIME_VALUE"] == "from-dotenv"


def test_load_environment_does_not_override_process_environment(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("TEST_RUNTIME_VALUE=from-dotenv\n")
    monkeypatch.setenv("TEST_RUNTIME_VALUE", "explicit-process-value")

    load_environment(env_file)

    assert os.environ["TEST_RUNTIME_VALUE"] == "explicit-process-value"
