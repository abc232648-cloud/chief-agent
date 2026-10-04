"""Owner pages explain actual limits without exposing internal catalog dumps."""
def test_foundation_pages_are_readable_and_links_reach_real_pages(dashboard):
    from playwright.sync_api import sync_playwright,expect
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True)
        try:
            page=browser.new_page();page.goto(dashboard.url)
            for target,text in [('capabilities','Supporting components'),('policies','Action risk levels'),('integrations','Manage AI connections'),('devices','Device connections are not available yet')]:
                page.locator('#navLinks button[data-target="'+target+'"]').click()
                content=page.locator('#'+target+'Content')
                expect(content).to_contain_text(text)
                assert content.locator('pre').count()==0
                assert 'tests/test_' not in content.inner_text()
                assert '"dependencies":' not in content.inner_text()
            page.locator('#navLinks button[data-target="integrations"]').click()
            page.get_by_role('button',name='Manage AI connections',exact=True).click()
            expect(page.locator('#assignmentAgent')).to_be_visible()
            page.locator('#navLinks button[data-target="capabilities"]').click()
            # Untrusted descriptions remain plain text after the presentation change.
            page.evaluate("()=>{document.getElementById('capabilitiesContent').innerHTML=capabilityCatalog({capabilities:[{id:'synthetic',description:'<img src=x onerror=alert(1)>',owner:'farming',maturity:'LIMITED'}],components:[]})}")
            assert page.locator('#capabilitiesContent img').count()==0
            expect(page.locator('#capabilitiesContent')).to_contain_text('<img')
        finally:browser.close()
