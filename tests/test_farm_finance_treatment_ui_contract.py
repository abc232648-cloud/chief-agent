from pathlib import Path


def test_finance_ui_collects_authoritative_sale_provenance_when_available():
    source=Path('static/farm-finance.js').read_text(encoding='utf-8')
    assert "id=\"financeFlockWrap\"" in source
    assert "id=\"financeFlock\"" in source
    assert "['EGG_SALES','BIRD_SALES'].includes(category)" in source
    assert "hasFlocks=[...select.options].some(option=>option.value)" in source
    assert "if(!flock&&hasFlocks)" in source
    assert "if(flock){details.flock_id=flock;details.output_type=category==='EGG_SALES'?'EGGS':'MEAT';}" in source
    assert "select.required=productSale&&hasFlocks" in source
    assert "farmCatalog.filter(e=>e.entity_type==='FLOCK')" in source
    assert 'Choose the source flock for egg or bird sales so treatment holds can be checked.' in source
    assert 'Legacy unprovenanced sales remain compatible only when no treatment hold exists.' in source
