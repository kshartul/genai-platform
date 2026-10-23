import pytest

from genai_platform.platform import Platform
from scripts.seed_warehouse import seed

PROFILE = "profiles/knowledge-assistant.local.yaml"


@pytest.fixture(scope="session")
def platform():
    seed("data/warehouse/analytics.db")
    p = Platform.from_profile(PROFILE)
    p.ingest()
    return p
