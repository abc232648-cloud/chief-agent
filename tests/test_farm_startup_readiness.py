from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_work_shell_is_prepared_before_context_wait():
    source = (ROOT / 'static' / 'work.js').read_text(encoding='utf-8')
    assert "prepareFarmWorkShell();" in source
    assert source.index("prepareFarmWorkShell();") < source.index("chiefContext=await api('/api/ui/context')")
    assert "if(typeof prepareFarmSections==='function')prepareFarmSections();" in source


def test_work_expand_intent_is_reapplied_after_initial_farm_load():
    source = (ROOT / 'static' / 'work.js').read_text(encoding='utf-8')
    assert "let farmSectionOpenPreference=null;" in source
    assert "if(button.textContent==='Expand all sections')farmSectionOpenPreference=true;" in source
    assert "else if(button.textContent==='Collapse all sections')farmSectionOpenPreference=false;" in source
    load = source.index("await loadFarmRecords();")
    apply = source.index("applyFarmSectionOpenPreference();", load)
    assert load < apply


def test_dynamic_brief_shell_exists_before_disclosure_preparation():
    brief = (ROOT / 'static' / 'farm-brief.js').read_text(encoding='utf-8')
    page = (ROOT / 'ui' / 'work.html').read_text(encoding='utf-8')
    assert "function ensureFarmBriefShell()" in brief
    assert "ensureFarmBriefShell();" in brief
    assert page.index('/static/farm-brief.js') < page.index('/static/farm-records.js') < page.index('/static/work.js')


def test_clarifications_follow_the_record_entry_shell_when_it_exists():
    source = (ROOT / 'static' / 'farm-clarifications.js').read_text(encoding='utf-8')
    assert "const entry=document.getElementById('farmEntrySection');" in source
    assert "(entry||document.getElementById('farmRecords').querySelector(':scope > p')).after(host);" in source


def test_disclosure_preparation_is_repeatable_for_late_farm_cards():
    source = (ROOT / 'static' / 'farm-records.js').read_text(encoding='utf-8')
    start = source.index('function prepareFarmSections()')
    end = source.index('\nfunction farmConvertedQuantity', start)
    function = source[start:end]
    assert "if (!root) return;" in function
    assert "root.dataset.sectionsReady" in function
    assert "if (!root || root.dataset.sectionsReady) return;" not in function
    assert "if (balances && !document.getElementById('farmBalancesSection'))" in function
    assert "let toolbar = root.querySelector(':scope > .farmSectionTools')" in function
    assert "if (!toolbar)" in function
    assert "'farmPhysicalCounts'" in function
    assert "'farmHealthRecords'" in function
    assert "if (!section || section.parentElement !== root || section === previous) continue;" in function


def test_farm_ai_setup_is_primed_then_focuses_the_disclosure_after_refresh():
    source = (ROOT / 'static' / 'chief-navigation.js').read_text(encoding='utf-8')
    start = source.index("button[data-action=\"farm-ai-setup\"]")
    handler = source[start:]
    assert "await renderFarmAI();" in handler
    assert "openFarmSection('farmAssistantCard');" in handler
    assert "await show('farmRecords');" in handler
    assert handler.index("await renderFarmAI();") < handler.index("openFarmSection('farmAssistantCard');") < handler.index("await show('farmRecords');")
    assert "try{await show('farmRecords');}catch(error){reportError(error);}" in handler
    assert "const focusTarget=card.querySelector(':scope > summary')||card;" in handler
    assert handler.index("await show('farmRecords');") < handler.index("const focusTarget=card.querySelector(':scope > summary')||card;")
    assert "focusTarget.focus({preventScroll:true});" in handler
    assert "uiActionAllowed(button.dataset)" in handler
    assert "event.stopImmediatePropagation()" in handler
