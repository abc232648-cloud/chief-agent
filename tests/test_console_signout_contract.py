"""Tests for on-farm Chief Console sign-out verification, CSRF enforcement, and error resilience."""
import re
from playwright.sync_api import sync_playwright, expect
from tests.test_identity_http import request


def test_console_signout_csrf_cookie_revocation_and_redirect(dashboard):
    """Verify sign-out submits X-Chief-CSRF header, revokes server session, and redirects to login."""
    d = dashboard
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context()
        page = context.new_page()
        page.goto(d.url)
        expect(page.locator('#navLinks')).to_be_visible()

        # Intercept logout call to verify headers and payload
        logout_requests = []

        def handle_route(route):
            if route.request.url.endswith('/api/auth/logout'):
                headers = route.request.headers
                logout_requests.append({
                    'csrf': headers.get('x-chief-csrf'),
                    'method': route.request.method,
                    'post_data': route.request.post_data,
                })
            route.continue_()

        page.route('**/api/auth/logout', handle_route)

        # Click the Sign out button in topbar
        sign_out_btn = page.locator('#signOutBtn')
        expect(sign_out_btn).to_be_visible()
        sign_out_btn.click()

        # Verify page redirects to /login
        page.wait_for_url(re.compile(r'.*/login.*'), timeout=10000)

        # Verify request intercepted
        assert len(logout_requests) == 1
        assert logout_requests[0]['method'] == 'POST'
        assert logout_requests[0]['csrf'] != ''
        assert logout_requests[0]['post_data'] == '{}'

        # Verify previous session is now revoked (401)
        raw_cookie = d.credentials['raw']
        assert request(d, '/api/auth/session', raw=raw_cookie)[0] == 401

        browser.close()


def test_console_signout_failed_revocation_shows_retryable_error_and_does_not_redirect(dashboard):
    """Verify sign-out does not swallow logout errors, displays dashboard error, and keeps user on page."""
    d = dashboard
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context()
        page = context.new_page()
        page.goto(d.url)
        expect(page.locator('#navLinks')).to_be_visible()

        # Mock /api/auth/logout failure (500 Internal Server Error)
        def handle_route_fail(route):
            if route.request.url.endswith('/api/auth/logout'):
                route.fulfill(
                    status=500,
                    content_type='application/json',
                    body='{"reason": "Simulated server error during logout"}'
                )
            else:
                route.continue_()

        page.route('**/api/auth/logout', handle_route_fail)

        sign_out_btn = page.locator('#signOutBtn')
        expect(sign_out_btn).to_be_visible()
        sign_out_btn.click()

        # Ensure error banner is visible with failure message
        error_banner = page.locator('#dashboardError')
        expect(error_banner).to_be_visible(timeout=5000)
        expect(error_banner).to_contain_text('Sign-out failed')
        expect(error_banner).to_contain_text('Simulated server error during logout')

        # Crucially: verify URL did NOT change to /login
        assert '/login' not in page.url

        # Verify Sign out button is re-enabled for retry
        expect(sign_out_btn).to_be_enabled()

        browser.close()


def test_console_signout_csrf_error_shows_retryable_error(dashboard):
    """Verify that if /api/auth/logout fails with CSRF rejection, sign-out fails truthfully without redirecting."""
    d = dashboard
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context()
        page = context.new_page()
        page.goto(d.url)
        expect(page.locator('#navLinks')).to_be_visible()

        # Mock /api/auth/logout 403 CSRF error
        def handle_csrf_fail(route):
            if route.request.url.endswith('/api/auth/logout'):
                route.fulfill(
                    status=403,
                    content_type='application/json',
                    body='{"reason": "CSRF validation failed."}'
                )
            else:
                route.continue_()

        page.route('**/api/auth/logout', handle_csrf_fail)

        sign_out_btn = page.locator('#signOutBtn')
        expect(sign_out_btn).to_be_visible()
        sign_out_btn.click()

        # Error banner must be shown
        error_banner = page.locator('#dashboardError')
        expect(error_banner).to_be_visible(timeout=5000)
        expect(error_banner).to_contain_text('Sign-out failed')
        expect(error_banner).to_contain_text('CSRF validation failed.')

        # Did NOT redirect to /login
        assert '/login' not in page.url
        expect(sign_out_btn).to_be_enabled()

        browser.close()
