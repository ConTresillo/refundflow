from pathlib import Path

from pypdf import PdfWriter

from app.document_parsers import DocumentParseError
from app.incremental_index import SQLitePolicyIndex
from app.policy_catalog import PolicyCatalog
from app.policy_ingestion import PolicyIngestionService
from app.retrieval import SQLiteEvidenceRetriever


def make_pdf(path: Path, text: str) -> None:
    writer = PdfWriter()
    page = writer.add_blank_page(width=300, height=300)
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

    stream = DecodedStreamObject()
    stream.set_data(b"BT /F1 12 Tf 20 200 Td (" + text.encode("ascii") + b") Tj ET")
    page[NameObject("/Contents")] = writer._add_object(stream)
    font = DictionaryObject()
    for key, value in (("/Type", "/Font"), ("/Subtype", "/Type1"), ("/BaseFont", "/Helvetica")):
        font[NameObject(key)] = NameObject(value)
    fonts = DictionaryObject()
    fonts[NameObject("/F1")] = writer._add_object(font)
    resources = DictionaryObject()
    resources[NameObject("/Font")] = fonts
    page[NameObject("/Resources")] = resources
    with path.open("wb") as target:
        writer.write(target)


def record(version: str, text: str) -> dict:
    from app.policies import compute_content_hash

    item = {
        "id": "pdf-billing-demo", "department_id": "billing", "version": version,
        "title": "Synthetic PDF policy", "status": "active", "effective_from": "2026-01-01",
        "effective_to": None, "source_type": "synthetic_demo", "source_name": "Local synthetic PDF",
        "source_url": "", "provenance": "Generated locally for tests.",
        "license_notes": "Synthetic test fixture.", "excerpts": [text],
    }
    item["content_hash"] = compute_content_hash(item)
    return item


def setup(tmp_path: Path):
    source = tmp_path / "policies"
    source.mkdir()
    database = tmp_path / "data.sqlite3"
    catalog = PolicyCatalog(database)
    index = SQLitePolicyIndex(database)
    service = PolicyIngestionService(source, catalog, index)
    pdf = tmp_path / "pdf-billing-demo.pdf"
    return service, catalog, index, pdf


def test_pdf_ingest_retrieves_page_citation_and_failed_replacement_keeps_old(tmp_path):
    service, catalog, index, pdf = setup(tmp_path)
    make_pdf(pdf, "Refund review requires receipt evidence")
    service.replace_pdf(pdf, record("1.0", "unused source label"), occurred_at="2026-11-22T00:00:00Z", tenant_id="northstar-retail")
    result = SQLiteEvidenceRetriever(index, catalog).retrieve("receipt evidence", departments={"billing"}, as_of="2026-11-22")
    assert result[0].citation == "pdf-billing-demo@1.0#page-1"
    assert "receipt evidence" in result[0].text

    bad_pdf = tmp_path / "pdf-billing-demo.pdf"
    bad_pdf.write_bytes(b"corrupt")
    try:
        service.replace_pdf(bad_pdf, record("2.0", "bad replacement"), occurred_at="2026-11-23T00:00:00Z", tenant_id="northstar-retail")
    except DocumentParseError:
        pass
    else:
        raise AssertionError("corrupt replacement unexpectedly succeeded")
    current = catalog.current("pdf-billing-demo")
    assert current is not None and current["version"] == "1.0"
    assert index.search("receipt", department_id="billing")[0].version == "1.0"
    status = catalog.file_status("northstar-retail")
    assert status[0]["status"] == "error"
    assert "DocumentParseError" in status[0]["message"]


def test_unauthorized_tenant_has_no_index_candidates(tmp_path):
    service, catalog, index, pdf = setup(tmp_path)
    make_pdf(pdf, "Tenant-specific refund receipt evidence")
    service.replace_pdf(pdf, record("1.0", "unused source label"), occurred_at="2026-11-22T00:00:00Z", tenant_id="northstar-retail")
    retriever = SQLiteEvidenceRetriever(index, catalog)
    assert retriever.retrieve("receipt evidence", departments={"billing"}, as_of="2026-11-22", allowed_policy_ids=set()) == []
    assert retriever.retrieve("receipt evidence", departments={"billing"}, as_of="2026-11-22", allowed_policy_ids={"another-tenant-policy"}) == []
