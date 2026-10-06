"""Upgrade regression: a warm browser cache, not only fresh routed contexts."""
import pytest

from tests.test_frontend_browser import browser, page, playwright, server


@pytest.mark.parametrize('entry', ['landing', 'workspace'])
def test_cached_previous_verification_module_does_not_break_new_entry(browser, server, entry):
    url, mock = server
    mock.update(legacy_cache=True, legacy_cache_reads=0, requests=[], mode='no-tools', verification=None, saved_messages=[])
    context = browser.new_context()
    tab = context.new_page()
    try:
        # Deliberately do NOT use page.route/context.route: HTTP cache must work.
        tab.goto(url + '/')
        old = tab.evaluate("fetch('/js/verification.js').then(r=>r.text())")
        assert 'old-export-fixture' in old
        mock['legacy_cache'] = False
        # The second fetch still returns the old content from Chrome's HTTP cache.
        assert 'old-export-fixture' in tab.evaluate("fetch('/js/verification.js').then(r=>r.text())")
        assert mock['legacy_cache_reads'] == 1
        if entry == 'landing':
            tab.locator('#landing-question').fill('合成缓存升级测试')
            tab.locator('#landing-send').click()
        else:
            tab.goto(url + '/workspace/')
        try:
            playwright.expect(tab.locator('#message-input')).to_be_editable(timeout=7000)
        except AssertionError:
            error = tab.evaluate("async () => { try { await import('/js/response-details.js'); return ''; } catch(e) { return e.name + ': ' + e.message; } }")
            pytest.fail(f'Workspace failed after cached upgrade: {error}; {tab.locator("#notice").inner_text()}')
        assert tab.locator('.suggestion').count() == 4
        assert len(mock['requests']) == (1 if entry == 'landing' else 0)
        if entry == 'landing':
            assert mock['requests'][0]['message'] == '合成缓存升级测试'
        assert mock['legacy_cache_reads'] == 1
    finally:
        mock['legacy_cache'] = False
        context.close()


@pytest.mark.parametrize('entry', ['landing', 'workspace'])
def test_module_failure_offers_recovery_without_submitting_or_replaying(page, server, entry):
    page.goto(server[0] + '/')
    page.route('**/js/response-details.js?*', lambda route: route.fulfill(status=503, body='Unavailable'))
    if entry == 'landing':
        page.locator('#landing-question').fill('合成保留问题，不自动重发')
        page.locator('#landing-send').click()
    else:
        page.goto(server[0] + '/workspace/')
    playwright.expect(page.locator('#notice')).to_contain_text('脚本加载失败')
    assert '问题尚未发送' in page.locator('#notice').inner_text()
    assert not server[1]['requests']
    if entry == 'landing':
        assert page.locator('#message-input').input_value() == '合成保留问题，不自动重发'
    assert page.evaluate('Object.keys(localStorage)') == []
    assert not page.evaluate("sessionStorage.getItem('af_v3_conversation_id')")
    recovery = page.get_by_role('link', name='重新打开工作台', exact=True)
    playwright.expect(recovery).to_be_focused()
    page.unroute('**/js/response-details.js?*')
    recovery.click()
    playwright.expect(page.locator('#message-input')).to_be_editable()
    assert page.locator('#history-view').is_visible()
    assert page.locator('.suggestion').count() == 4
    assert not server[1]['requests']


def test_initialization_failure_is_not_reported_as_unsent(page, server):
    page.goto(server[0] + '/')
    page.route('**/js/app.js?*', lambda route: route.fulfill(status=200, content_type='application/javascript', body='export async function start(){throw new Error("synthetic startup error")}'))
    page.locator('#landing-question').fill('合成状态未知任务')
    page.locator('#landing-send').click()
    playwright.expect(page.locator('#notice')).to_contain_text('任务状态尚未确认')
    assert '问题尚未发送' not in page.locator('#notice').inner_text()
    assert not server[1]['requests']
    assert page.get_by_role('link', name='重新打开工作台', exact=True).is_visible()


def test_late_initial_pageshow_does_not_cancel_workspace_entry(page, server):
    page.goto(server[0] + '/')
    page.evaluate('''() => {
        document.getElementById('ask-entry').click();
        window.dispatchEvent(new PageTransitionEvent('pageshow', {persisted: false}));
    }''')
    playwright.expect(page.locator('#message-input')).to_be_editable(timeout=15000)
    assert page.url.endswith('/workspace/#message-input')
    assert not server[1]['requests']
