import re
from playwright.sync_api import expect


def navigate(page,target):
    if page.locator('body').evaluate("e=>e.classList.contains('sidebarCollapsed')"):
        page.locator('#sidebarToggle').click()
        expect(page.locator('body')).not_to_have_class(re.compile(r'.*sidebarCollapsed.*'))
    button=page.locator('nav button[data-action="open-agent"][data-id="jobs"]') if target=='agentDetail' else page.locator(f'nav button[data-target="{target}"]')
    if button.count()==0:
        page.locator('nav button[data-target="domains"]').click()
        page.locator('#domainList [data-action="open-agent"][data-id="jobs"]').click()
        button=page.locator(f'nav button[data-target="{target}"]')
    button.evaluate("e=>{for(let p=e.parentElement;p;p=p.parentElement){if(p.tagName==='DETAILS')p.open=true;}}")
    button.click()
    # A click event can finish before Chief's awaited navigation renders. Do
    # not toggle the mobile sidebar or issue another navigation in that gap.
    expect(page.locator(f'section#{target}')).to_be_visible()
    if page.viewport_size['width']<=600:
        page.locator('#sidebarToggle').click()
        expect(page.locator('body')).to_have_class(re.compile(r'.*sidebarCollapsed.*'))
