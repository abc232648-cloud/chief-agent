from playwright.sync_api import expect, sync_playwright


def test_work_shell_is_available_even_when_context_load_fails(dashboard):
    d = dashboard
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page.route('**/api/ui/context', lambda route: route.abort())
            page.goto(d.url + '/work', wait_until='domcontentloaded')
            expect(page.locator('#farmEntrySection')).to_be_attached()
            toolbar = page.locator('#farmRecords > .farmSectionTools')
            expect(toolbar).to_be_attached()
            expect(toolbar.locator('button', has_text='Expand all sections')).to_be_attached()
        finally:
            browser.close()


def test_owner_farm_ai_controls_do_not_wait_for_full_refresh(dashboard):
    d = dashboard
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page.goto(d.url)
            page.locator('#chiefAgents button[data-id="farming"]').click()
            expect(page.locator('#farmHomeAI')).to_be_visible()

            # The activation controls must be rendered before the broad Farm refresh.
            # A failed journal refresh must not make setup controls disappear or wait.
            page.route('**/api/farm/journal*', lambda route: route.abort())
            page.get_by_role('button', name='Choose connection / activate Farm AI', exact=True).click()
            expect(page.locator('#farmAIRegistration')).to_be_visible()
            expect(page.get_by_role('button', name='Test and activate Qwen', exact=True)).to_be_visible()
        finally:
            browser.close()
