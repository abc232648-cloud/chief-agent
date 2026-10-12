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


def test_disclosure_preparation_is_repeatable_for_late_farm_cards_without_reinserting_stable_nodes():
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
    assert "if (previous.nextElementSibling !== section) previous.after(section);" in function


def test_farm_ai_setup_has_one_action_path_and_primes_controls_before_broad_refresh():
    navigation = (ROOT / 'static' / 'chief-navigation.js').read_text(encoding='utf-8')
    chief = (ROOT / 'static' / 'chief.js').read_text(encoding='utf-8')
    farm = (ROOT / 'static' / 'farm-records.js').read_text(encoding='utf-8')

    # The generic Chief action dispatcher is the only Farm-AI click executor.
    assert chief.count("'farm-ai-setup':") == 1
    assert "button[data-action=\"farm-ai-setup\"]" not in navigation
    assert "event.stopImmediatePropagation()" not in navigation

    action_start = chief.index("'farm-ai-setup':")
    action_end = chief.index("'toggle-sidebar':", action_start)
    action = chief[action_start:action_end]
    assert "await show('farmRecords');" in action
    assert "openFarmSection('farmAssistantCard');" in action
    assert action.index("await show('farmRecords');") < action.index("openFarmSection('farmAssistantCard');")
    assert "card.focus({preventScroll:true});" in action

    # The assistant controls are built while their disclosure is hidden, before
    # the failure-prone journal refresh. Visibility is restored in finally, so a
    # journal failure cannot strand setup controls or expose a half-ready view.
    load_start = farm.index('async function loadFarmRecords()')
    load_end = farm.index("document.addEventListener('DOMContentLoaded', () =>", load_start)
    load = farm[load_start:load_end]
    assert "assistantCard.hidden=true;" in load
    assert "await renderFarmAI();" in load
    assert "await api('/api/farm/journal?offset='+farmHistoryOffset);" in load
    assert load.index("assistantCard.hidden=true;") < load.index("await renderFarmAI();") < load.index("await api('/api/farm/journal?offset='+farmHistoryOffset);")
    assert "finally" in load
    assert "assistantCard.hidden=assistantWasHidden;" in load


def test_farm_setup_saved_status_is_not_exposed_until_refresh_finishes():
    source = (ROOT / 'static' / 'farm-records.js').read_text(encoding='utf-8')
    start = source.index("const bind=(formId,resultId,build)=>")
    end = source.index("const v=id=>", start)
    block = source[start:end]
    assert "Refreshing saved Farm setup" in block
    assert "await loadFarmRecords();" in block
    assert "result.textContent='Saved.';" in block
    assert block.index("await loadFarmRecords();") < block.index("result.textContent='Saved.';")
