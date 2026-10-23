from genai_platform.ingest import ParentChildChunker, PIIRedactor, RecursiveChunker, SemanticChunker
from genai_platform.ingest.chunkers import split_sections
from genai_platform.providers.local import HashEmbedder
from genai_platform.types import Document

DOC = Document(id="d1", source="x.md", title="Handbook", text="""# Handbook
## Leave
Employees get 24 days. Leave accrues monthly. Requests need approval.

| Band | Days |
| --- | --- |
| A | 24 |

## Travel
Book via the portal. Economy under 6 hours.
""")


def test_sections_follow_headings():
    secs = split_sections(DOC)
    assert [s.path for s in secs] == ["Handbook > Leave", "Handbook > Travel"]


def test_recursive_chunker_keeps_tables_whole_and_adds_context():
    chunks = RecursiveChunker(target_tokens=12, overlap_tokens=0).chunk(DOC)
    table = [c for c in chunks if c.text.startswith("|")]
    assert len(table) == 1 and table[0].text.count("|") >= 9
    assert all(c.context_header.startswith("Document: Handbook | Section:") for c in chunks)
    assert len({c.content_hash for c in chunks}) == len(chunks)


def test_semantic_and_parent_child_chunkers():
    sem = SemanticChunker(HashEmbedder(), target_tokens=50).chunk(DOC)
    assert sem and all(c.section for c in sem)
    pc = ParentChildChunker(parent_tokens=60, child_tokens=10).chunk(DOC)
    assert all(c.parent_id and c.parent_text and c.text in c.parent_text for c in pc)


def test_pii_redaction():
    text, found = PIIRedactor().redact("Mail jane.doe@acme.example or call +91 80 4000 1234. Card 4111 1111 1111 1111.")
    assert "[EMAIL]" in text and "[PHONE]" in text and "[CARD]" in text
    assert {f.kind for f in found} == {"EMAIL", "PHONE", "CARD"}
    clean, none = PIIRedactor().redact("Order 1234 shipped in 2025.")
    assert clean == "Order 1234 shipped in 2025." and not none


def test_ingest_is_idempotent(platform):
    again = platform.ingest()
    assert again.embedded == 0 and again.skipped_unchanged == again.chunks > 0


def test_pii_never_reaches_index(platform):
    joined = " ".join(platform.store.get(cid).text for cid in platform.store._chunks)
    assert "people-ops@acme.example" not in joined and "[EMAIL]" in joined
