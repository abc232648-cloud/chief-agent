from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_work_shell_is_prepared_before_context_wait():
    source = (ROOT / 'static' / 'work.js').read_text(encoding='utf-8')
    assert "prepareFarmWorkShell();" in source
    assert source.index("prepareFarmWorkShell();") < source.index("chiefContext=await api('/api/ui/context')")
    assert "if(typeof prepareFarmSections==='function')prepareFarmSections();" in source


def test_farm_ai_setup_is_primed_before_full_farm_refresh():
    source = (ROOT / 'static' / 'chief-navigation.js').read_text(encoding='utf-8')
    start = source.index("button[data-action=\"farm-ai-setup\"]")
    handler = source[start:]
    assert "await renderFarmAI();" in handler
    assert "await show('farmRecords');" in handler
    assert handler.index("await renderFarmAI();") < handler.index("await show('farmRecords');")
    assert "uiActionAllowed(button.dataset)" in handler
    assert "event.stopImmediatePropagation()" in handler
