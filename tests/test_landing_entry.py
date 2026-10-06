"""Explicit landing submission; synthetic SSE only, no business app imports."""
import pytest

from tests.test_frontend_browser import browser, page, playwright, server, send


def open_landing(page, server):
    page.goto(server[0] + '/')
    playwright.expect(page.locator('#landing-question')).to_be_editable()


def submit_landing(page, server, text='合成测试问题，不含真实患者'):
    open_landing(page, server)
    page.locator('#landing-question').fill(text)
    page.locator('#landing-send').click()
    playwright.expect(page.locator('#message-input')).to_be_editable(timeout=20000)
    playwright.expect(page.locator('.message.assistant .message-actions button').first).to_be_visible()


def test_input_is_real_blank_and_ime_do_not_submit(page, server):
    open_landing(page, server)
    playwright.expect(page.locator('#landing-send')).to_be_disabled()
    page.locator('#landing-question').click()
    page.locator('#landing-question').fill('合成问题')
    page.locator('#landing-question').dispatch_event('compositionstart')
    page.locator('#landing-question').press('Enter')
    assert not server[1]['requests']
    assert page.url == server[0] + '/'
    page.locator('#landing-question').dispatch_event('compositionend')
    page.locator('#landing-question').press('Shift+Enter')
    assert '\n' in page.locator('#landing-question').input_value()
    assert not server[1]['requests']


def test_submit_starts_new_context_once_then_follows_up(page, server):
    page.evaluate("sessionStorage.setItem('af_v3_conversation_id', 'fixture-history')")
    open_landing(page, server)
    page.locator('#landing-question').fill('合成首问')
    page.evaluate("document.querySelector('#landing-question-panel').requestSubmit(); document.querySelector('#landing-question-panel').requestSubmit()")
    playwright.expect(page.locator('#message-input')).to_be_editable(timeout=20000)
    assert server[1]['requests'] == [{'message': '合成首问', 'conversation_id': None}]
    assert '历史回答' not in page.locator('#messages').inner_text()
    playwright.expect(page.locator('.markdown table')).to_be_visible()
    send(page, '合成追问')
    assert server[1]['requests'][-1] == {'message': '合成追问', 'conversation_id': 'fixture-chat'}


def test_question_never_persisted_or_replayed_by_reload_back_forward(page, server):
    text = '合成专用标记 <script>window.injected=1</script>'
    submit_landing(page, server, text)
    assert text in page.locator('.user-content').inner_text()
    assert page.evaluate('window.injected') is None
    assert page.evaluate('history.state') is None
    assert page.evaluate('window.name') == ''
    assert page.evaluate('Object.keys(localStorage)') == []
    assert page.evaluate('Object.keys(sessionStorage)') == ['af_v3_conversation_id']
    assert page.url == server[0] + '/workspace/#message-input'
    page.reload()
    playwright.expect(page.locator('#message-input')).to_be_editable()
    assert len(server[1]['requests']) == 1
    page.go_back()
    playwright.expect(page.locator('#landing-question')).to_be_editable()
    assert page.locator('#landing-question').input_value() == ''
    page.go_forward()
    playwright.expect(page.locator('#message-input')).to_be_editable()
    assert len(server[1]['requests']) == 1


@pytest.mark.parametrize('mode', ['401', '429', 'broken'])
def test_stream_failure_is_explicit_and_not_resubmitted(page, server, mode):
    server[1]['mode'] = mode
    submit_landing(page, server)
    playwright.expect(page.locator('.message-error')).to_be_visible()
    assert len(server[1]['requests']) == 1
    assert page.url.endswith('/workspace/#message-input')
    page.reload()
    playwright.expect(page.locator('#message-input')).to_be_editable()
    assert len(server[1]['requests']) == 1


def test_shell_failure_keeps_draft_without_chat_request(page, server):
    open_landing(page, server)
    page.route('**/workspace/', lambda route: route.fulfill(status=503, body='Unavailable'))
    page.locator('#landing-question').fill('合成保留输入')
    page.locator('#landing-send').click()
    playwright.expect(page.locator('#entry-error')).to_contain_text('问题尚未发送')
    assert page.locator('#landing-question').input_value() == '合成保留输入'
    assert not server[1]['requests']
    playwright.expect(page.locator('#landing-send')).to_be_enabled()
    page.unroute('**/workspace/')
    page.locator('#landing-send').click()
    playwright.expect(page.locator('#message-input')).to_be_editable(timeout=20000)
    assert len(server[1]['requests']) == 1


@pytest.mark.parametrize('width', [1440, 1920, 390])
def test_dark_handoff_preserves_theme_and_removes_decorations(page, server, width):
    page.set_viewport_size({'width': width, 'height': 844 if width == 390 else 1000})
    open_landing(page, server)
    page.locator('[data-theme-choice="b"]').click()
    page.locator('#landing-question').fill('合成深色界面提问')
    page.locator('#landing-send').click()
    playwright.expect(page.locator('#message-input')).to_be_editable(timeout=20000)
    assert page.locator('body').get_attribute('data-theme') == 'b'
    assert page.locator('.landing-atmosphere').count() == 0
    assert not page.locator('html').evaluate('node => node.classList.contains("effects-ready")')
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')


def test_entry_modes_keyboard_and_business_links_do_not_submit(page, server):
    open_landing(page, server)
    page.locator('#landing-question').fill('还未提交的草稿')
    page.locator('#ask-tab').focus()
    page.keyboard.press('ArrowRight')
    playwright.expect(page.locator('#patients-tab')).to_be_focused()
    playwright.expect(page.locator('#landing-patient-panel')).to_be_visible()
    page.keyboard.press('ArrowLeft')
    assert page.locator('#landing-question').input_value() == '还未提交的草稿'
    assert page.locator('.landing-capability').evaluate_all('(nodes) => nodes.map(n => n.hash)') == ['#patients-overview', '#patients-medications', '#patients-labs', '#patients-history']
    assert not server[1]['requests']


def test_back_during_running_handoff_preserves_url_and_single_task(page, server):
    server[1]['mode'] = 'slow'
    open_landing(page, server)
    page.locator('#landing-question').fill('合成慢任务')
    page.locator('#landing-send').click()
    playwright.expect(page.locator('#composer')).to_have_attribute('aria-busy', 'true')
    page.evaluate('history.back()')
    playwright.expect(page).to_have_url(server[0] + '/workspace/#message-input')
    playwright.expect(page.locator('#message-input')).to_be_editable(timeout=20000)
    assert len(server[1]['requests']) == 1


def test_back_during_module_loading_cannot_strand_shell_at_landing_url(page, server):
    open_landing(page, server)
    pending = []
    page.route('**/js/app.js?*', lambda route: pending.append(route))
    page.locator('#landing-question').fill('合成模块加载延迟')
    page.locator('#landing-send').click()
    playwright.expect(page.locator('#notice')).to_contain_text('正在打开工作台')
    playwright.expect(page.locator('#message-input')).not_to_be_editable()
    page.evaluate('history.back()')
    playwright.expect(page).to_have_url(server[0] + '/workspace/#message-input')
    assert not server[1]['requests']
    assert len(pending) == 1
    pending.pop().continue_()
    playwright.expect(page.locator('#message-input')).to_be_editable(timeout=20000)
    playwright.expect(page.locator('.message.assistant .message-actions button').first).to_be_visible()
    assert server[1]['requests'] == [{'message': '合成模块加载延迟', 'conversation_id': None}]
    page.evaluate('history.back()')
    playwright.expect(page.locator('#landing-question')).to_be_editable()
    assert len(server[1]['requests']) == 1
