"""Offline UI acceptance tests: temporary HTTP app, synthetic records, no LLM/EHR.

Requires pytest + playwright (see docs/FRONTEND_V3.md). Uses installed Chrome by
default on Windows; set FRONTEND_BROWSER for another executable, or install
Playwright Chromium. Screenshots go to ignored .pytest_cache/frontend-v3/.
"""
import asyncio
import json
import os
import socket
import threading
import time
from pathlib import Path

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
import uvicorn

from app.frontend import mount_frontends

playwright = pytest.importorskip('playwright.sync_api')
ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = Path(os.environ.get('FRONTEND_SCREENSHOTS', ROOT / '.pytest_cache/frontend-v3'))
ANSWER = '## 用药信息核对\n\n以下为**合成测试数据**，不代表任何真实患者。\n\n| 项目 | 记录 |\n| --- | --- |\n| 药物 | 测试药物甲 |\n| 范围 | 内置规则 |\n\n请核对原始记录，并由专业人员确认。'


@pytest.fixture(scope='module')
def server():
    app = FastAPI()
    mock = {'requests': [], 'deleted': False, 'feedback': [], 'mode': 'normal', 'title': '合成数据 · 既有会话', 'renames': [], 'verification': None, 'saved_messages': [], 'conversation_items': None, 'deletions': [], 'history_gate': None, 'history_started': None, 'stream_gates': []}

    @app.get('/js/verification.js')
    def cache_upgrade_fixture(request: Request):
        # A real HTTP cache is exercised by bootstrap tests without Playwright
        # routing (routing disables the browser cache). Normal tests get disk JS.
        if mock.get('legacy_cache') and not request.url.query:
            mock['legacy_cache_reads'] = mock.get('legacy_cache_reads', 0) + 1
            return Response('export function renderVerification() {} // old-export-fixture', media_type='application/javascript', headers={'Cache-Control': 'public, max-age=3600'})
        return FileResponse(ROOT / 'frontend-v3/js/verification.js', headers={'Cache-Control': 'no-store'})

    @app.get('/api/health')
    def health():
        return {'status': 'ok', 'database': 'ok'}

    @app.get('/api/conversations')
    def conversations():
        if mock['mode'] == 'history-error':
            return JSONResponse({'detail': 'fixture'}, status_code=503)
        if mock['conversation_items'] is not None:
            result = [dict(item) for item in mock['conversation_items']]
        else:
            result = [] if mock['deleted'] else [{'id': 'fixture-history', 'title': mock['title'], 'updated_at': 1}]
        gate = mock['history_gate']
        if gate is not None:
            mock['history_started'].set()
            gate.wait(timeout=15)
        return result

    @app.get('/api/conversations/{cid}')
    def conversation(cid: str):
        if mock['deleted'] or cid == 'missing':
            return JSONResponse({'detail': 'Not found'}, status_code=404)
        messages = mock['saved_messages'] if cid == 'fixture-chat' else []
        return {'id': cid, 'title': mock['title'], 'messages': messages or [{'role': 'user', 'content': '历史问题'}, {'role': 'assistant', 'content': '历史回答，仅供界面测试。'}]}

    @app.patch('/api/conversations/{cid}')
    async def rename(cid: str, request: Request):
        payload = await request.json()
        mock['renames'].append(payload)
        if mock['mode'] == 'rename-error':
            return JSONResponse({'detail': 'fixture'}, status_code=503)
        mock['title'] = payload['title'].strip()
        return {'id': cid, 'title': mock['title'], 'updated_at': 2}

    @app.delete('/api/conversations/{cid}')
    def delete(cid: str):
        mock['deletions'].append(cid)
        if mock['conversation_items'] is None:
            mock['deleted'] = True
        else:
            mock['conversation_items'] = [item for item in mock['conversation_items'] if item['id'] != cid]
        return {'status': 'ok'}

    @app.post('/api/feedback')
    async def feedback(request: Request):
        mock['feedback'].append(await request.json())
        return {'status': 'ok'}

    @app.post('/api/chat/stream')
    async def stream(request: Request):
        body = await request.json()
        mock['requests'].append(body)
        mode = mock['mode']
        if mode in ('401', '429', '503'):
            return JSONResponse({'detail': 'fixture'}, status_code=int(mode))

        async def events():
            packets = [('thinking', {'conversation_id': 'fixture-chat'})]
            calls = [] if mode == 'no-tools' else [{'tool': 'patient_summary', 'args': {'patient_identifier': '合成患者'}}, {'tool': 'drug_interaction_check'}]
            if mode == 'single-tool':
                calls = calls[:1]
            packets += [('tool_call', call) for call in calls]
            content = ANSWER * 15 if mode == 'long' else ANSWER
            packets += [('token', {'text': content[:31]}), ('token', {'text': content[31:]})]
            if mode != 'broken':
                metadata = {} if mode == 'no-metadata' else {'confidence': 0.82, 'latency_ms': 2480, 'verification': {'drug_safety': {'passed': True, 'source': 'synthetic fixture'}}, 'disclaimers': ['仅用于测试，不用于诊疗。']}
                if mode != 'no-metadata' and mock['verification'] is not None:
                    metadata['verification'] = mock['verification']
                    metadata['confidence'] = mock['verification'].get('confidence_scoring', {}).get('score', 0.82)
                mock['saved_messages'].extend([
                    {'role': 'user', 'content': body['message']},
                    {'role': 'assistant', 'content': content, 'metadata': {'tool_calls': calls, **metadata}},
                ])
                packets.append(('done', {'response': content, 'conversation_id': 'fixture-chat', 'tool_calls': calls, **metadata}))
            token_index = 0
            for event, data in packets:
                raw = ('data: ' + json.dumps({'event': event, 'data': data}, ensure_ascii=False) + '\r\n\r\n').encode('utf-8')
                # Force UTF-8 and SSE frames across network chunks.
                for offset in range(0, len(raw), 17):
                    yield raw[offset:offset + 17]
                    await asyncio.sleep(0.003)
                if mode == 'slow':
                    await asyncio.sleep(0.4)
                if mode == 'gated' and event == 'token':
                    gate = mock['stream_gates'][token_index]
                    token_index += 1
                    deadline = time.monotonic() + 15
                    while not gate.is_set() and time.monotonic() < deadline:
                        await asyncio.sleep(0.01)
        return StreamingResponse(events(), media_type='text/event-stream')

    mount_frontends(app, ROOT)
    listener = socket.socket()
    listener.bind(('127.0.0.1', 0))
    port = listener.getsockname()[1]
    runtime = uvicorn.Server(uvicorn.Config(app, log_level='error', access_log=False))
    thread = threading.Thread(target=runtime.run, kwargs={'sockets': [listener]}, daemon=True)
    thread.start()
    for _ in range(100):
        if runtime.started:
            break
        time.sleep(0.03)
    assert runtime.started
    yield f'http://127.0.0.1:{port}', mock
    runtime.should_exit = True
    thread.join(timeout=5)
    listener.close()


@pytest.fixture(scope='module')
def browser():
    with playwright.sync_playwright() as runtime:
        executable = os.environ.get('FRONTEND_BROWSER')
        chrome = Path('C:/Program Files/Google/Chrome/Application/chrome.exe')
        if not executable and chrome.is_file():
            executable = str(chrome)
        instance = runtime.chromium.launch(headless=True, **({'executable_path': executable} if executable else {}))
        yield instance
        instance.close()


@pytest.fixture
def page(browser, server):
    url, mock = server
    mock.update(requests=[], deleted=False, feedback=[], mode='normal', title='合成数据 · 既有会话', renames=[], verification=None, saved_messages=[], conversation_items=None, deletions=[], history_gate=None, history_started=None, stream_gates=[])
    context = browser.new_context(viewport={'width': 1440, 'height': 1000})
    tab = context.new_page()
    errors, external = [], []
    tab.on('pageerror', lambda error: errors.append(str(error)))
    def guard(route):
        if not route.request.url.startswith(url):
            external.append(route.request.url)
            route.abort()
        else:
            route.continue_()
    tab.route('**/*', guard)
    tab.goto(url + '/workspace/')
    playwright.expect(tab.locator('#message-input')).to_be_editable(timeout=15000)
    yield tab
    for gate in mock['stream_gates']:
        gate.set()
    if mock['history_gate'] is not None:
        mock['history_gate'].set()
    assert not errors, errors
    assert not external, external
    context.close()


def send(page, text='检查合成测试记录'):
    page.locator('#message-input').fill(text)
    page.locator('#send-button').click()
    playwright.expect(page.locator('#message-input')).to_be_editable(timeout=15000)


def workspace_view(page, button_id):
    """Use the same navigation on desktop and inside the mobile drawer."""
    button = page.locator(button_id)
    if not button.is_visible():
        page.locator('#menu-button').click()
    button.click()


def wait_for_mock_history_request(page, mock):
    # Keep Playwright's synchronous request-routing loop alive while waiting
    # for the mock server thread to receive a follow-up fetch.
    deadline = time.monotonic() + 5
    while not mock['history_started'].is_set() and time.monotonic() < deadline:
        page.wait_for_timeout(20)
    assert mock['history_started'].is_set()


@pytest.mark.parametrize('width', [1440, 1920, 390])
def test_landing_layout_entry_and_back(page, server, width):
    page.set_viewport_size({'width': width, 'height': 1000 if width != 390 else 844})
    api_requests = []
    page.on('request', lambda req: api_requests.append(req.url) if '/api/' in req.url else None)
    page.goto(server[0] + '/')
    playwright.expect(page.locator('#landing-title')).to_be_visible()
    page.reload()
    assert page.title() == 'Medical Agent · 你的医疗信息助手'
    assert page.locator('.landing-brand').inner_text() == 'Medical Agent\n你的医疗信息助手'
    assert page.locator('.landing-brand').get_attribute('aria-label') == 'Medical Agent 项目首页'
    assert page.locator('#landing-title').inner_text() == '让复杂的医疗信息，\n从一个问题开始。'
    assert page.locator('.landing-placeholder').get_attribute('placeholder') == '问点什么，从了解开始。'
    assert not api_requests  # No history, health, model or FHIR on introduction.
    assert page.locator('textarea').count() == 1
    assert page.locator('.landing-capability').count() == 4
    assert page.locator('.landing-brand img').get_attribute('src') == '/assets/mark-landing.svg'
    assert page.locator('#landing-title').evaluate('(node) => getComputedStyle(node).color') == 'rgb(32, 42, 61)'
    assert page.locator('.landing-entry-surface').evaluate('(node) => getComputedStyle(node).backgroundColor') == 'rgb(240, 244, 252)'
    assert page.locator('a[href^="/workspace/"]').count() == 6
    assert page.locator('body').get_attribute('data-theme') == 'a'
    assert page.locator('.landing-entry-arrow').evaluate('(node) => getComputedStyle(node).backgroundColor') == 'rgb(65, 100, 217)'
    assert page.locator('.landing-placeholder').evaluate('(node) => getComputedStyle(node, "::placeholder").color') == 'rgb(94, 109, 131)'
    assert page.locator('.landing-atmosphere').get_attribute('aria-hidden') == 'true'
    assert page.locator('.landing-atmosphere').evaluate('(node) => getComputedStyle(node).pointerEvents') == 'none'
    assert 'AI 辅助参考' in page.locator('.landing-footer').inner_text()
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(ARTIFACTS / f'landing-{width}.png'), full_page=True, animations='disabled')
    page.locator('#landing-composer').hover()
    page.screenshot(path=str(ARTIFACTS / f'landing-hover-{width}.png'), full_page=True, animations='disabled')
    page.locator('#ask-entry').click()
    playwright.expect(page).to_have_url(server[0] + '/workspace/#message-input')
    playwright.expect(page.locator('#message-input')).to_be_editable(timeout=15000)
    playwright.expect(page.locator('#message-input')).to_be_focused()
    assert page.locator('#message-input').input_value() == ''
    assert not server[1]['requests']
    assert page.locator('#welcome').is_visible()
    assert page.locator('.brand img').get_attribute('src') == '/assets/mark-landing.svg'
    if width == 390:
        page.locator('#menu-button').click()  # Branding is in the mobile drawer.
    assert page.title() == '医疗助手 · Medical Agent'
    assert page.locator('.brand').inner_text() == 'Medical Agent\n你的医疗信息助手'
    if width == 390:
        page.screenshot(path=str(ARTIFACTS / 'workspace-brand-mobile.png'))
        page.keyboard.press('Escape')
    page.go_back()
    playwright.expect(page.locator('#ask-entry')).to_be_visible()
    page.go_forward()
    playwright.expect(page.locator('#message-input')).to_be_editable(timeout=15000)
    assert not server[1]['requests']


@pytest.mark.parametrize('theme', ['a', 'b'])
@pytest.mark.parametrize('width', [1440, 1920, 390])
def test_landing_molecular_perimeter_and_motion(page, server, theme, width):
    page.set_viewport_size({'width': width, 'height': 844 if width == 390 else 1000})
    requests = []
    page.on('request', lambda req: requests.append(req.url) if '/api/' in req.url else None)
    page.goto(server[0] + '/')
    if theme == 'b':
        page.locator('[data-theme-choice="b"]').click()
    motifs = page.locator('.molecular-motif:visible .molecular-float')
    assert motifs.count() == (2 if width == 390 else 4)
    assert page.locator('.molecular-field').evaluate('(node) => node.closest("[aria-hidden]").getAttribute("aria-hidden")') == 'true'
    assert page.locator('.molecular-field').evaluate('(node) => getComputedStyle(node).pointerEvents') == 'none'
    assert page.locator('.molecular-field [tabindex], .molecular-field a').count() == 0
    assert page.locator('.molecular-field svg use').evaluate_all('(nodes) => nodes.every(node => node.getAttribute("href").startsWith("#molecular-"))')
    # Sample all phases of each transform-only animation, not merely the first frame.
    # On desktop, decorations must stay clear of the title and central entrance.
    for motif in motifs.all():
        assert motif.evaluate('(node) => getComputedStyle(node).animationName') == 'molecular-drift'
        assert float(motif.evaluate('(node) => getComputedStyle(node).animationDuration').rstrip('s')) >= 30
        transforms = motif.evaluate('''node => {
            const animation = node.getAnimations()[0];
            const duration = animation.effect.getTiming().duration;
            const delay = animation.effect.getTiming().delay;
            const protectedAreas = ['#landing-title', '#ask-entry'].map(selector => document.querySelector(selector).getBoundingClientRect());
            const results = [0, .25, .5, .75, 1].map(phase => {
                animation.currentTime = delay + duration * phase;
                const box = node.getBoundingClientRect();
                return {transform: getComputedStyle(node).transform,
                    overlaps: protectedAreas.some(rect => box.left < rect.right && box.right > rect.left && box.top < rect.bottom && box.bottom > rect.top)};
            });
            return results;
        }''')
        assert transforms[0]['transform'] != transforms[-1]['transform']
        if width != 390:
            assert not any(item['overlaps'] for item in transforms), motif.evaluate('node => node.parentElement.className')
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    page.locator('#motion-toggle').click()
    assert motifs.evaluate_all('(nodes) => nodes.every(node => getComputedStyle(node).animationPlayState === "paused")')
    page.locator('#motion-toggle').click()
    assert motifs.evaluate_all('(nodes) => nodes.every(node => getComputedStyle(node).animationPlayState === "running")')
    page.emulate_media(reduced_motion='reduce')
    assert motifs.evaluate_all('(nodes) => nodes.every(node => getComputedStyle(node).animationName === "none")')
    # Still navigable with decorations present; no automatic API or model request.
    assert not requests
    page.locator('#ask-entry').click()
    playwright.expect(page.locator('#message-input')).to_be_focused(timeout=15000)
    assert not server[1]['requests']


def test_landing_keyboard_reduced_motion_and_links(page, server):
    page.emulate_media(reduced_motion='reduce')
    page.goto(server[0] + '/')
    page.keyboard.press('Tab')
    playwright.expect(page.locator('.landing-skip')).to_be_focused()
    page.keyboard.press('Enter')
    playwright.expect(page.locator('#ask-entry')).to_be_focused()
    assert page.locator('.landing-hero').evaluate('(node) => getComputedStyle(node).animationName') == 'none'
    assert page.locator('.aurora-blue').evaluate('(node) => getComputedStyle(node).animationName') == 'none'
    assert page.locator('.landing-entry-surface').evaluate('(node) => getComputedStyle(node, "::before").animationName') == 'none'
    assert page.locator('#motion-toggle').is_hidden()
    assert page.locator('#ask-entry').evaluate('(node) => getComputedStyle(node).outlineStyle') == 'solid'
    assert page.get_by_role('link', name='经典版', exact=True).get_attribute('href') == '/classic/'
    page.keyboard.press('Enter')
    playwright.expect(page.locator('#message-input')).to_be_editable(timeout=15000)
    playwright.expect(page.locator('#message-input')).to_be_focused()
    page.get_by_role('link', name='返回项目首页', exact=True).click()
    playwright.expect(page.locator('#ask-entry')).to_be_visible()
    page.locator('#ask-entry').click()
    playwright.expect(page).to_have_url(server[0] + '/workspace/#message-input')
    assert not server[1]['requests']


def test_landing_keeps_existing_history_and_does_not_submit(page, server):
    page.get_by_role('button', name='合成数据 · 既有会话', exact=True).click()
    playwright.expect(page.locator('#message-input')).to_be_editable(timeout=15000)
    assert page.evaluate("sessionStorage.getItem('af_v3_conversation_id')") == 'fixture-history'
    page.get_by_role('link', name='返回项目首页', exact=True).click()
    page.locator('#ask-entry').click()
    playwright.expect(page.locator('#message-input')).to_be_focused(timeout=15000)
    assert '历史回答' in page.locator('.markdown').inner_text()
    assert page.evaluate("sessionStorage.getItem('af_v3_conversation_id')") == 'fixture-history'
    assert not server[1]['requests']
    assert page.evaluate('Object.keys(localStorage)') == []
    assert page.evaluate('Object.keys(sessionStorage)') == ['af_v3_conversation_id']


def test_landing_without_javascript_or_external_dependencies(browser, server):
    context = browser.new_context(java_script_enabled=False, reduced_motion='reduce', viewport={'width': 320, 'height': 640})
    tab = context.new_page()
    requests = []
    tab.on('request', lambda req: requests.append(req.url))
    tab.route('**/*', lambda route: route.continue_() if route.request.url.startswith(server[0]) else route.abort())
    try:
        tab.goto(server[0] + '/')
        playwright.expect(tab.locator('#ask-entry')).to_be_visible()
        assert all(url.startswith(server[0]) and '/api/' not in url for url in requests)
        assert tab.locator('#motion-toggle').is_hidden()
        # Without page JS, Chromium can stall Playwright's rAF stability wait.
        # Exercise native link activation with a real pointer click instead.
        box = tab.locator('#ask-entry').bounding_box()
        assert box and box['y'] + box['height'] < 640
        tab.mouse.click(box['x'] + box['width'] / 2, box['y'] + box['height'] / 2)
        playwright.expect(tab).to_have_url(server[0] + '/workspace/#message-input')
        # Navigation still works without JS; the actual chat app requires JS.
        assert tab.locator('#message-input').count() == 1
    finally:
        context.close()


def test_landing_entry_still_opens_workspace_on_history_failure(page, server):
    server[1]['mode'] = 'history-error'
    page.goto(server[0] + '/')
    page.locator('#ask-entry').click()
    playwright.expect(page.locator('#message-input')).to_be_editable(timeout=15000)
    playwright.expect(page.locator('#message-input')).to_be_focused()
    assert '不可用' in page.locator('#history-list').inner_text()
    assert not server[1]['requests']


def test_landing_effects_pause_resume_and_visibility(page, server):
    page.goto(server[0] + '/')
    blue = page.locator('.aurora-blue')
    assert blue.evaluate('(node) => getComputedStyle(node).animationName') == 'landing-aurora-blue'
    first_transform = blue.evaluate('(node) => getComputedStyle(node).transform')
    page.wait_for_function('(first) => getComputedStyle(document.querySelector(".aurora-blue")).transform !== first', arg=first_transform)
    page.locator('#landing-composer').hover()
    assert page.locator('.landing-entry-surface').evaluate('(node) => getComputedStyle(node, "::before").animationPlayState') == 'running'
    page.get_by_role('button', name='暂停动效', exact=True).click()
    assert blue.evaluate('(node) => getComputedStyle(node).animationPlayState') == 'paused'
    page.locator('#landing-composer').hover()
    assert page.locator('.landing-entry-surface').evaluate('(node) => getComputedStyle(node, "::before").animationPlayState') == 'paused'
    page.get_by_role('button', name='恢复动效', exact=True).click()
    assert blue.evaluate('(node) => getComputedStyle(node).animationPlayState') == 'running'
    # Deterministic visibility event, without relying on headless window focus.
    page.evaluate("Object.defineProperty(document, 'hidden', {configurable: true, value: true}); document.dispatchEvent(new Event('visibilitychange'))")
    assert blue.evaluate('(node) => getComputedStyle(node).animationPlayState') == 'paused'
    page.evaluate("delete document.hidden; document.dispatchEvent(new Event('visibilitychange'))")
    assert blue.evaluate('(node) => getComputedStyle(node).animationPlayState') == 'running'
    assert not server[1]['requests']
    assert page.evaluate('Object.keys(localStorage)') == []
    assert page.evaluate('Object.keys(sessionStorage)') == []


def test_landing_reduced_motion_updates_without_reload(page, server):
    page.goto(server[0] + '/')
    page.emulate_media(reduced_motion='reduce')
    playwright.expect(page.locator('#motion-toggle')).to_be_hidden()
    assert page.locator('.aurora-violet').evaluate('(node) => getComputedStyle(node).animationName') == 'none'
    observed = page.evaluate('''() => {
        const link = document.querySelector('#ask-entry');
        let observed;
        link.addEventListener('click', event => {
            observed = {prevented: event.defaultPrevented, leaving: document.body.classList.contains('landing-leaving')};
            event.preventDefault(); // Test observes the native path without navigating.
        }, {once: true});
        link.click();
        return observed;
    }''')
    assert observed == {'prevented': False, 'leaving': False}
    page.emulate_media(reduced_motion='no-preference')
    playwright.expect(page.locator('#motion-toggle')).to_be_visible()
    assert page.locator('.aurora-violet').evaluate('(node) => getComputedStyle(node).animationName') == 'landing-aurora-violet'


def test_landing_transition_single_navigation_and_back_reset(page, server):
    page.goto(server[0] + '/')
    navigations = []
    page.on('request', lambda req: navigations.append(req.url) if req.is_navigation_request() and '/workspace/' in req.url else None)
    observed = page.locator('#ask-entry').evaluate('''link => {
        link.click(); link.click();
        return {leaving: document.body.classList.contains('landing-leaving'), busy: document.querySelector('#overview').getAttribute('aria-busy')};
    }''')
    assert observed == {'leaving': True, 'busy': 'true'}
    playwright.expect(page).to_have_url(server[0] + '/workspace/#message-input')
    playwright.expect(page.locator('#message-input')).to_be_editable(timeout=15000)
    assert len(navigations) == 1
    assert not server[1]['requests']
    page.go_back()
    playwright.expect(page.locator('#ask-entry')).to_be_visible()
    assert not page.locator('body').evaluate("node => node.classList.contains('landing-leaving')")
    assert page.locator('#overview').get_attribute('aria-busy') is None
    assert page.locator('#landing-status').inner_text() == ''


def test_landing_keeps_modified_clicks_native(page, server):
    page.goto(server[0] + '/')
    results = page.evaluate('''() => {
        const link = document.querySelector('#ask-entry');
        return [{ctrlKey: true}, {metaKey: true}, {shiftKey: true}, {altKey: true}, {button: 1}, {target: '_blank'}, {download: true}].map(settings => {
            if (settings.target) link.target = settings.target;
            if (settings.download) link.setAttribute('download', '');
            let prevented;
            link.addEventListener('click', event => {prevented = event.defaultPrevented; event.preventDefault();}, {once: true});
            link.dispatchEvent(new MouseEvent('click', {bubbles: true, cancelable: true, button: 0, ...settings}));
            link.removeAttribute('target'); link.removeAttribute('download');
            return {prevented, leaving: document.body.classList.contains('landing-leaving')};
        });
    }''')
    assert results == [{'prevented': False, 'leaving': False}] * 7
    assert not server[1]['requests']


def test_landing_pointer_depth_and_stable_hit_area(page, server):
    page.goto(server[0] + '/')
    entry = page.locator('#landing-composer')
    entry.hover()  # Wait for the finite arrival animation to finish.
    rect = entry.bounding_box()
    page.mouse.move(rect['x'] + rect['width'] * .85, rect['y'] + rect['height'] * .25)
    page.wait_for_function("parseFloat(document.querySelector('.landing-entry-surface').style.getPropertyValue('--tilt-y')) > 1")
    assert page.locator('body').evaluate("node => node.classList.contains('pointer-active')")
    assert abs(entry.bounding_box()['x'] - rect['x']) < .1
    assert abs(entry.bounding_box()['width'] - rect['width']) < .1
    assert page.locator('.scene-ribbons').is_visible()
    assert page.locator('.scene-orbits').is_hidden()
    page.get_by_role('button', name='暂停动效', exact=True).click()
    assert page.locator('.landing-entry-surface').evaluate("node => node.style.getPropertyValue('--tilt-y')") == ''
    assert page.locator('.landing-entry-surface').evaluate('(node) => getComputedStyle(node).transform') == 'none'
    assert not page.locator('body').evaluate("node => node.classList.contains('pointer-active')")
    assert not server[1]['requests']


def test_landing_reduced_motion_disables_depth_and_all_scene_animations(page, server):
    page.emulate_media(reduced_motion='reduce')
    page.goto(server[0] + '/')
    page.locator('#landing-composer').hover()
    assert page.locator('.landing-entry-surface').evaluate('(node) => getComputedStyle(node).transform') == 'none'
    assert page.locator('.ribbon-signal').first.evaluate('(node) => getComputedStyle(node).animationName') == 'none'
    assert page.locator('#landing-title').evaluate('(node) => getComputedStyle(node).animationName') == 'none'
    assert not page.locator('body').evaluate("node => node.classList.contains('pointer-active')")
    assert not server[1]['requests']


def test_landing_small_screen_has_no_pointer_depth(page, server):
    page.set_viewport_size({'width': 390, 'height': 844})
    page.goto(server[0] + '/')
    page.locator('#landing-composer').hover()
    assert page.locator('.landing-entry-surface').evaluate("node => node.style.getPropertyValue('--tilt-y')") == ''
    assert page.locator('.scene-pointer-glow').is_hidden()
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')


@pytest.mark.parametrize('width', [1440, 1920, 390])
def test_landing_dark_scene_and_one_shared_entry(page, server, width):
    page.set_viewport_size({'width': width, 'height': 1000 if width != 390 else 844})
    page.goto(server[0] + '/')
    assert page.locator('body').get_attribute('data-theme') == 'a'
    page.get_by_role('button', name='B · 深色', exact=True).click()
    assert page.locator('body').get_attribute('data-theme') == 'b'
    assert page.get_by_role('button', name='B · 深色', exact=True).get_attribute('aria-pressed') == 'true'
    assert page.get_by_role('button', name='A · 浅色', exact=True).get_attribute('aria-pressed') == 'false'
    assert page.locator('.scene-ribbons').is_hidden()
    assert page.locator('.scene-orbits').is_visible()
    assert page.locator('.scene-stars').is_visible()
    assert page.locator('body').evaluate('(node) => getComputedStyle(node).backgroundColor') == 'rgb(8, 15, 32)'
    assert page.locator('#landing-title').evaluate('(node) => getComputedStyle(node).color') == 'rgb(237, 243, 255)'
    assert page.locator('a[href^="/workspace/"]').count() == 6
    assert page.locator('#ask-entry').get_attribute('href') == '/workspace/#message-input'
    assert not server[1]['requests']
    assert page.evaluate('Object.keys(localStorage)') == []
    assert page.evaluate('Object.keys(sessionStorage)') == ['af_v3_theme']
    assert page.evaluate("sessionStorage.getItem('af_v3_theme')") == 'b'
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(ARTIFACTS / f'landing-b-{width}.png'), full_page=True, animations='disabled')
    page.locator('#landing-composer').hover()
    page.screenshot(path=str(ARTIFACTS / f'landing-b-hover-{width}.png'), full_page=True, animations='disabled')
    page.locator('#ask-entry').click()
    playwright.expect(page.locator('#message-input')).to_be_editable(timeout=15000)
    playwright.expect(page.locator('#message-input')).to_be_focused()
    assert page.locator('#welcome').is_visible()
    assert not server[1]['requests']


def test_landing_theme_toggle_keyboard_persistence_and_history(page, server):
    page.get_by_role('button', name='合成数据 · 既有会话', exact=True).click()
    playwright.expect(page.locator('#message-input')).to_be_editable(timeout=15000)
    page.get_by_role('link', name='返回项目首页', exact=True).click()
    page.get_by_role('button', name='B · 深色', exact=True).focus()
    page.keyboard.press('Enter')
    assert page.locator('body').get_attribute('data-theme') == 'b'
    page.get_by_role('button', name='A · 浅色', exact=True).click()
    assert page.locator('body').get_attribute('data-theme') == 'a'
    page.get_by_role('button', name='B · 深色', exact=True).click()
    page.reload()
    assert page.locator('body').get_attribute('data-theme') == 'b'
    assert page.get_by_role('button', name='B · 深色', exact=True).get_attribute('aria-pressed') == 'true'
    page.locator('#ask-entry').click()
    playwright.expect(page.locator('#message-input')).to_be_focused(timeout=15000)
    assert '历史回答' in page.locator('.markdown').inner_text()
    assert page.evaluate("sessionStorage.getItem('af_v3_conversation_id')") == 'fixture-history'
    assert page.evaluate('Object.keys(localStorage)') == []
    assert sorted(page.evaluate('Object.keys(sessionStorage)')) == ['af_v3_conversation_id', 'af_v3_theme']
    assert page.locator('html').get_attribute('data-theme') == 'b'
    page.reload()
    playwright.expect(page.locator('#message-input')).to_be_editable(timeout=15000)
    assert page.locator('html').get_attribute('data-theme') == 'b'
    page.get_by_role('link', name='返回项目首页', exact=True).click()
    assert page.get_by_role('button', name='B · 深色', exact=True).get_attribute('aria-pressed') == 'true'
    page.get_by_role('button', name='A · 浅色', exact=True).click()
    page.go_back()
    playwright.expect(page.locator('#message-input')).to_be_editable(timeout=15000)
    assert page.locator('html').get_attribute('data-theme') == 'a'
    assert not server[1]['requests']


def test_landing_dark_motion_pause_and_reduced_motion(page, server):
    page.goto(server[0] + '/')
    page.get_by_role('button', name='B · 深色', exact=True).click()
    orbit = page.locator('.orbit-beam').first
    assert orbit.evaluate('(node) => getComputedStyle(node).animationName') == 'orbit-travel'
    page.get_by_role('button', name='暂停动效', exact=True).click()
    assert orbit.evaluate('(node) => getComputedStyle(node).animationPlayState') == 'paused'
    assert page.locator('.scene-stars').evaluate('(node) => getComputedStyle(node).animationPlayState') == 'paused'
    page.emulate_media(reduced_motion='reduce')
    playwright.expect(page.locator('#motion-toggle')).to_be_hidden()
    assert orbit.evaluate('(node) => getComputedStyle(node).animationName') == 'none'
    assert page.locator('.scene-stars').evaluate('(node) => getComputedStyle(node).animationName') == 'none'
    page.locator('#ask-entry').click()
    playwright.expect(page.locator('#message-input')).to_be_editable(timeout=15000)
    assert not server[1]['requests']


@pytest.mark.parametrize('theme', ['a', 'b'])
@pytest.mark.parametrize('width', [1440, 1920, 390])
def test_workspace_palette_matches_landing(page, server, theme, width):
    page.set_viewport_size({'width': width, 'height': 844 if width == 390 else 1000})
    page.goto(server[0] + '/')
    page.locator(f'[data-theme-choice="{theme}"]').click()
    palette = page.locator('body').evaluate('''node => ({
        background: getComputedStyle(node).backgroundColor,
        ink: getComputedStyle(node).color,
        input: getComputedStyle(document.querySelector('.landing-entry-surface')).backgroundColor,
        primary: getComputedStyle(document.querySelector('.landing-entry-arrow')).backgroundColor
    })''')
    page.locator('#ask-entry').click()
    playwright.expect(page.locator('#message-input')).to_be_focused(timeout=15000)
    assert page.locator('html').get_attribute('data-theme') == theme
    assert page.locator('body').evaluate('node => getComputedStyle(node).backgroundColor') == palette['background']
    assert page.locator('body').evaluate('node => getComputedStyle(node).color') == palette['ink']
    assert page.locator('.composer').evaluate('node => getComputedStyle(node).backgroundColor') == palette['input']
    assert page.locator('.sidebar-new').evaluate('node => getComputedStyle(node).backgroundColor') == palette['primary']
    assert page.locator('.brand img').get_attribute('src') == '/assets/mark-landing.svg'
    assert not server[1]['requests']
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(ARTIFACTS / f'workspace-{theme}-home-{width}.png'))
    send(page)
    assert page.locator('.assistant-label img').get_attribute('src') == '/assets/mark-landing.svg'
    assert 'Medical Agent' in page.locator('.assistant-label').inner_text()
    assert 'AgentForge' not in page.locator('.assistant-label').inner_text()
    page.locator('.tool-trace > summary').click()
    page.screenshot(path=str(ARTIFACTS / f'workspace-{theme}-chat-{width}.png'))
    page.locator('#latest-details').click()
    page.locator('.verification-check > summary').click()
    assert page.locator('.detail-stat').text_content() == '82%'
    page.screenshot(path=str(ARTIFACTS / f'workspace-{theme}-details-{width}.png'))
    page.locator('#close-details').click()
    if width == 390:
        page.locator('#menu-button').click()
    page.locator('#capabilities-button').click()
    assert page.locator('#capabilities-view .capability-row').count() == 14
    surface = 'rgb(16, 27, 48)' if theme == 'b' else 'rgb(255, 255, 255)'
    assert page.locator('.capability-row').first.evaluate('node => getComputedStyle(node).backgroundColor') == surface
    page.screenshot(path=str(ARTIFACTS / f'workspace-{theme}-tools-{width}.png'))
    workspace_view(page, '#history-button')
    assert page.locator('#history-view').is_visible()
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    page.screenshot(path=str(ARTIFACTS / f'workspace-{theme}-history-{width}.png'))
    if width == 390 and not page.locator('.history-rename').is_visible():
        page.locator('#menu-button').click()
    page.locator('.history-rename').click()
    assert page.locator('#rename-dialog').evaluate('node => getComputedStyle(node).backgroundColor') == surface
    assert page.locator('#rename-input').evaluate('node => getComputedStyle(node).backgroundColor') == surface
    page.locator('#cancel-rename').click()
    page.locator('.history-delete').click()
    assert page.locator('#delete-dialog').evaluate('node => getComputedStyle(node).backgroundColor') == surface
    assert page.locator('#confirm-delete').evaluate('node => getComputedStyle(node).backgroundColor') == 'rgb(179, 61, 61)'
    page.locator('#cancel-delete').click()
    assert not server[1]['deleted']
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    assert page.evaluate('Object.keys(localStorage)') == []
    assert sorted(page.evaluate('Object.keys(sessionStorage)')) == ['af_v3_conversation_id', 'af_v3_theme']


@pytest.mark.parametrize('stored', ['b', 'invalid-theme'])
def test_workspace_theme_bootstrap_before_styles(page, server, stored):
    page.evaluate('(theme) => sessionStorage.setItem("af_v3_theme", theme)', stored)
    page.goto(server[0] + '/workspace/')
    playwright.expect(page.locator('#message-input')).to_be_editable(timeout=15000)
    expected = 'b' if stored == 'b' else 'a'
    assert page.locator('html').get_attribute('data-theme') == expected
    assert page.locator('html').evaluate('node => getComputedStyle(node).colorScheme') == ('dark' if expected == 'b' else 'light')
    assert not server[1]['requests']


@pytest.mark.parametrize('theme', ['a', 'b'])
def test_versioned_css_bypasses_legacy_green_response(page, server, theme):
    # Simulate a stale bare URL in an intermediary/browser cache. The page must
    # request the versioned resource, not rely on users clearing all site data.
    legacy_requests = []
    def legacy_css(route):
        legacy_requests.append(route.request.url)
        route.fulfill(status=200, content_type='text/css', body=':root{--green:#24644f}.sidebar-new{background:#24644f!important}.nav-item.active{color:#24644f!important}')
    page.route(server[0] + '/css/workspace.css', legacy_css)
    styles = []
    page.on('request', lambda req: styles.append(req.url) if req.resource_type == 'stylesheet' else None)
    page.goto(server[0] + '/')
    page.locator(f'[data-theme-choice="{theme}"]').click()
    page.locator('#ask-entry').click()
    playwright.expect(page.locator('#message-input')).to_be_focused(timeout=15000)
    assert not legacy_requests
    assert any('/css/workspace.css?v=20261005-boot2' in url for url in styles)
    assert any('/css/workspace-shell.css?v=20261005-boot2' in url for url in styles)
    assert page.locator('.sidebar-new').evaluate('node => getComputedStyle(node).backgroundColor') == 'rgb(65, 100, 217)'
    assert page.locator('.nav-item.active').evaluate('node => getComputedStyle(node).color') != 'rgb(36, 100, 79)'
    assert not server[1]['requests']


def test_theme_storage_disabled_is_nonfatal(page, server):
    page.add_init_script('''Object.defineProperty(window, 'sessionStorage', {
        get() { throw new DOMException('Disabled for test', 'SecurityError'); }
    });''')
    page.goto(server[0] + '/')
    page.locator('[data-theme-choice="b"]').click()
    assert page.locator('html').get_attribute('data-theme') == 'b'
    page.locator('#ask-entry').click()
    playwright.expect(page.locator('#message-input')).to_be_focused(timeout=15000)
    # Cross-page preference is optional; storage-disabled navigation falls back to A.
    assert page.locator('html').get_attribute('data-theme') == 'a'
    assert not server[1]['requests']


@pytest.mark.parametrize('width', [1440, 1920, 390])
def test_layout_and_screenshots(page, width):
    page.set_viewport_size({'width': width, 'height': 1000 if width != 390 else 844})
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(ARTIFACTS / f'home-{width}.png'))
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    send(page)
    assert page.locator('.markdown table').count() == 1
    assert page.locator('.markdown h2').inner_text() == '用药信息核对'
    page.screenshot(path=str(ARTIFACTS / f'chat-{width}.png'))
    page.locator('#latest-details').click()
    assert page.locator('#details-content').get_by_text('系统校验评分', exact=True).count() == 1
    assert page.locator('.detail-stat').text_content() == '82%'
    page.screenshot(path=str(ARTIFACTS / f'details-{width}.png'))
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    page.keyboard.press('Escape')
    assert page.locator('#details-panel').is_hidden()


def test_templates_ime_keyboard_and_multi_turn(page, server):
    mock = server[1]
    page.get_by_role('button', name='患者摘要', exact=False).click()
    assert not mock['requests']
    page.locator('#send-button').click()
    assert '替换' in page.locator('#notice').inner_text()
    assert not mock['requests']
    page.locator('#message-input').fill('中文输入')
    page.locator('#message-input').dispatch_event('compositionstart')
    page.locator('#message-input').dispatch_event('keydown', {'key': 'Enter', 'isComposing': True})
    page.locator('#message-input').dispatch_event('compositionend')
    assert not mock['requests']
    page.locator('#message-input').press('Shift+Enter')
    assert '\n' in page.locator('#message-input').input_value()
    page.locator('#message-input').press('Enter')
    playwright.expect(page.locator('#message-input')).to_be_editable(timeout=15000)
    send(page, '继续核对')
    assert len(mock['requests']) == 2
    assert mock['requests'][1]['conversation_id'] == 'fixture-chat'
    page.locator('.message-actions').last.get_by_role('button', name='有帮助', exact=True).click()
    playwright.expect(page.locator('.message-actions [aria-pressed=true]')).to_be_visible()
    assert mock['feedback'][0]['rating'] == 'up'
    assert page.evaluate('Object.keys(localStorage)') == []
    assert page.evaluate('Object.keys(sessionStorage)') == ['af_v3_conversation_id']


def test_all_tool_shortcuts_fill_and_validate_without_sending(page, server):
    catalog = page.evaluate("async () => { const {tools, toolTemplates} = await import('/js/catalog.js'); return {tools, toolTemplates}; }")
    assert len(catalog['tools']) == 14
    assert catalog['tools'].keys() == catalog['toolTemplates'].keys()
    for name, template in catalog['toolTemplates'].items():
        page.locator('#capabilities-button').click()
        row = page.locator(f'.capability-row[data-tool="{name}"]')
        assert catalog['tools'][name] in row.inner_text()
        assert row.locator('.capability-badge').count() == int('warning' in template)
        row.click()
        assert not page.locator('#capabilities-view').is_visible()
        assert page.locator('#message-input').input_value() == template['prompt']
        assert page.locator('#message-input').evaluate('(n)=>n===document.activeElement')
        assert page.locator('#message-input').evaluate('(n)=>n.value.slice(n.selectionStart,n.selectionEnd)').startswith('【')
        if 'warning' in template:
            assert page.locator('#template-warning').inner_text() == template['warning']
            assert page.locator('#template-warning').is_visible()
        else:
            assert page.locator('#template-warning').is_hidden()
        page.locator('#send-button').click()
        assert '替换' in page.locator('#notice').inner_text()
        assert not server[1]['requests']
    assert page.evaluate('Object.keys(localStorage)') == []
    assert page.evaluate('Object.keys(sessionStorage)') == []


def test_tool_shortcut_remaining_fields_and_warning_lifecycle(page, server):
    page.locator('#capabilities-button').click()
    page.locator('[data-tool="record_vitals"]').click()
    prompt = page.locator('#message-input').input_value().replace('【姓名或 ID】', '合成患者')
    page.locator('#message-input').fill('  ' + prompt)
    page.locator('#message-input').press('Enter')
    assert '【收缩压数值】' in page.locator('#notice').inner_text()
    assert page.locator('#message-input').evaluate('(n)=>n.value.slice(n.selectionStart,n.selectionEnd)') == '【收缩压数值】'
    assert page.locator('#template-warning').is_visible()
    page.locator('#message-input').fill(prompt.replace('【收缩压数值】', '120'))
    page.locator('#send-button').click()
    assert '【舒张压数值】' in page.locator('#notice').inner_text()
    assert not server[1]['requests']  # no real or simulated write has been sent
    page.locator('#message-input').fill('')
    assert page.locator('#template-warning').is_hidden()
    page.locator('.suggestion').filter(has=page.get_by_role('heading', name='预防筛查')).click()
    assert '不是只读查询' in page.locator('#template-warning').inner_text()
    page.locator('.suggestion').filter(has=page.get_by_role('heading', name='患者摘要')).click()
    assert page.locator('#template-warning').is_hidden()
    # Brackets in ordinary prose are not mistaken for known template fields.
    send(page, '请解释【测试主题】')
    assert len(server[1]['requests']) == 1


def test_tool_shortcut_continues_history_with_manual_send(page, server):
    page.locator('.history-select').click()
    playwright.expect(page.locator('#message-input')).to_be_editable()
    page.locator('#capabilities-button').click()
    page.locator('[data-tool="drug_interaction_check"]').click()
    assert '历史回答' in page.locator('.markdown').inner_text()
    assert page.locator('#conversation-title').inner_text() == '合成数据 · 既有会话'
    assert not server[1]['requests']
    text = page.locator('#message-input').input_value().replace('【姓名或 ID】', '合成患者')
    page.locator('#message-input').fill(text)
    page.locator('#send-button').click()
    playwright.expect(page.locator('#message-input')).to_be_editable(timeout=15000)
    assert server[1]['requests'] == [{'message': text, 'conversation_id': 'fixture-history'}]
    assert page.locator('.message.user').last.inner_text() == text


@pytest.mark.parametrize('width', [1440, 1920, 390])
def test_capability_layout_keyboard_and_mobile_focus(page, server, width):
    page.set_viewport_size({'width': width, 'height': 844 if width == 390 else 1000})
    page.locator('#message-input').fill('未发送的草稿')
    if width == 390:
        page.locator('#menu-button').click()
    page.locator('#capabilities-button').click()
    assert page.locator('.capability-row').count() == 14
    assert page.locator('.capability-badge').count() == 5
    assert page.locator('#capabilities-view').evaluate('(n)=>n.scrollWidth <= n.clientWidth')
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(ARTIFACTS / f'capabilities-{width}.png'))
    workspace_view(page, '#home-button')
    assert page.locator('#message-input').input_value() == '未发送的草稿'
    workspace_view(page, '#capabilities-button')
    page.locator('#capability-search').focus()
    page.keyboard.press('Tab')
    assert page.locator('#capabilities-view').evaluate('(node) => node.contains(document.activeElement)')
    page.locator('[data-tool="patient_summary"]').focus()
    assert page.locator('[data-tool="patient_summary"]').evaluate('(n)=>n===document.activeElement')
    page.keyboard.press('Enter')
    assert not page.locator('#capabilities-view').is_visible()
    assert not page.locator('#main').evaluate('(n)=>n.inert')
    assert page.locator('#message-input').evaluate('(n)=>n===document.activeElement')
    assert '【姓名或 ID】' in page.locator('#message-input').input_value()
    assert not server[1]['requests']
    if width == 390:
        assert 'sidebar-open' not in page.locator('#app-shell').get_attribute('class')


def test_workspace_navigation_disabled_until_active_request_finishes(page, server):
    server[1]['mode'] = 'slow'
    page.locator('#message-input').fill('合成测试')
    page.locator('#send-button').click()
    for selector in ('#home-button', '#history-button', '#capabilities-button'):
        assert page.locator(selector).is_disabled()
        page.locator(selector).evaluate('(node) => node.click()')
    assert page.locator('#chat-view').is_visible()
    assert page.locator('#history-view').is_hidden()
    assert page.locator('#capabilities-view').is_hidden()
    assert page.locator('#message-input').input_value() == ''
    playwright.expect(page.locator('#capabilities-button')).to_be_enabled(timeout=15000)
    page.locator('#capabilities-button').click()
    page.locator('[data-tool="record_vitals"]').click()
    assert page.locator('#template-warning').is_visible()
    assert len(server[1]['requests']) == 1


def test_history_delete_confirmation_and_classic(page, server):
    page.get_by_role('button', name='合成数据 · 既有会话', exact=True).click()
    playwright.expect(page.locator('#message-input')).to_be_editable(timeout=15000)
    page.locator('#latest-details').click()
    assert '这条旧消息未保存当轮详情' in page.locator('#details-content').inner_text()
    assert page.locator('#details-content h3').all_text_contents() == ['阅读提示', '校验结果', '系统校验评分', '本轮概况', '工具调用']
    assert page.locator('.detail-stat').text_content() == '未提供'
    page.locator('#close-details').click()
    page.get_by_role('button', name='删除会话：合成数据 · 既有会话').click()
    page.locator('#cancel-delete').click()
    assert not server[1]['deleted']
    page.get_by_role('button', name='删除会话：合成数据 · 既有会话').click()
    page.locator('#confirm-delete').click()
    playwright.expect(page.locator('#delete-dialog')).not_to_be_visible()
    assert server[1]['deleted']
    # Classic contains the original external font URL; block it separately, and
    # do not run the new-UI no-external assertion against unchanged legacy HTML.
    response = page.request.get(server[0] + '/classic/')
    assert response.body() == (ROOT / 'frontend-v2/index.html').read_bytes()


@pytest.mark.parametrize('mode, expected', [('401', '授权'), ('429', '频繁'), ('503', '不可用'), ('broken', '未收到任务完成确认')])
def test_errors_keep_content_and_never_retry(page, server, mode, expected):
    server[1]['mode'] = mode
    send(page)
    assert expected in page.locator('.message-error').inner_text()
    assert len(server[1]['requests']) == 1
    if mode == 'broken':
        assert '合成测试数据' in page.locator('.markdown').inner_text()
    assert page.locator('#send-button').is_disabled()  # input is now empty
    assert not page.locator('[data-new-chat]').first.is_disabled()


def test_busy_locks_navigation_and_tool_truth(page, server):
    server[1]['mode'] = 'slow'
    page.locator('#message-input').fill('合成记录测试')
    page.locator('#send-button').click()
    assert page.locator('[data-new-chat]').first.is_disabled()
    assert page.locator('.history-select').first.is_disabled()
    assert page.locator('.history-rename').first.is_disabled()
    assert page.locator('#send-button').is_disabled()
    playwright.expect(page.locator('#message-input')).to_be_editable(timeout=15000)
    page.locator('.tool-trace > summary').click()
    assert page.locator('.tool-item').count() == 2
    assert '调用参数未提供' in page.locator('.tool-items').inner_text()
    assert '未提供单工具结果状态' in page.locator('.tool-items').inner_text()
    assert '执行成功' not in page.locator('.tool-items').inner_text()


def test_no_tools_and_long_answer(page, server):
    server[1]['mode'] = 'no-tools'
    send(page)
    assert page.locator('.tool-trace').count() == 0
    server[1]['mode'] = 'long'
    send(page, '较长的合成回答')
    assert page.locator('.markdown table').count() == 16
    assert page.locator('#composer').bounding_box()['y'] > 0


def test_markdown_security(page):
    result = page.evaluate('''async () => {
      const {renderMarkdown} = await import('/js/render.js');
      const box = document.createElement('div'); document.body.append(box);
      const payload = '<script>window.__xss=1</script>\\n<img src="https://attacker.invalid/leak" onerror="window.__xss=2">\\n[jump](javascript:alert(1))\\n[data](data:text/html,test)\\n![photo](https://attacker.invalid/image)\\n[ok](https://example.com)\\n<svg onload="window.__xss=3"></svg>';
      renderMarkdown(box, payload);
      const data = {badNodes:box.querySelectorAll('script,img,svg,iframe').length,
        dangerous:[...box.querySelectorAll('a')].some(a=>/^(javascript|data):/i.test(a.getAttribute('href')||'')),
        safe:box.querySelector('a[href="https://example.com"]')?.rel,
        executed:!!window.__xss};
      box.remove(); return data;
    }''')
    assert result == {'badNodes': 0, 'dangerous': False, 'safe': 'noopener noreferrer', 'executed': False}


def test_sse_unit_cases(page):
    result = page.evaluate('''async () => {
      const {consumeSSE} = await import('/js/api.js');
      const raw = ': comment\\r\\ndata: {"event":"token",\\r\\ndata: "data":{"text":"中文"}}\\r\\n\\r\\ndata: {"event":"done","data":{"response":"完成"}}';
      const bytes = new TextEncoder().encode(raw), events = [];
      const stream = new ReadableStream({start(c){for (const value of bytes)c.enqueue(new Uint8Array([value]));c.close();}});
      await consumeSSE(stream, (e,d)=>events.push([e,d]));
      let missing='', malformed='';
      for (const [name,text] of [['missing','data: {"event":"token","data":{"text":"部分"}}\\n\\n'],['malformed','data: not-json\\n\\n']]) {
        try {await consumeSSE(new ReadableStream({start(c){c.enqueue(new TextEncoder().encode(text));c.close();}}),()=>{});}
        catch(e){if(name==='missing')missing=e.message;else malformed=e.message;}
      }
      return {events,missing,malformed};
    }''')
    assert result['events'] == [['token', {'text': '中文'}], ['done', {'response': '完成'}]]
    assert '未收到任务完成确认' in result['missing']
    assert '响应格式异常' in result['malformed']


def test_mobile_focus_and_navigation(page):
    page.set_viewport_size({'width': 390, 'height': 844})
    page.locator('#menu-button').click()
    assert page.locator('#main').evaluate('(n)=>n.inert')
    page.locator('#sidebar a[href="/classic/"]').focus()
    page.keyboard.press('Tab')
    assert page.locator('.brand').evaluate('(n)=>n===document.activeElement')
    page.keyboard.press('Escape')
    assert not page.locator('#main').evaluate('(n)=>n.inert')
    send(page)
    page.locator('#latest-details').click()
    assert page.locator('#details-panel').get_attribute('aria-modal') == 'true'
    page.keyboard.press('Escape')
    assert page.locator('#latest-details').evaluate('(n)=>n===document.activeElement')


def test_missing_metadata_is_not_fabricated(page, server):
    server[1]['mode'] = 'no-metadata'
    send(page)
    page.locator('#latest-details').click()
    assert page.locator('.detail-stat').text_content() == '未提供'
    assert '未提供' in page.locator('.detail-label').text_content()
    assert '%' not in page.locator('#details-content').inner_text()


def verification_fixture():
    return {
        'drug_safety': {'passed': True, 'flags': []},
        'allergy_safety': {'passed': True, 'flags': []},
        'confidence_scoring': {'score': 0.6, 'factors': {'tools_used': 0.5, 'data_richness': 0.5, 'response_hedging': 1.0, 'tool_error_rate': 0.5}},
        'claim_verification': {'passed': True, 'grounded_claims': 0, 'ungrounded_claims': 0, 'total_claims': 0, 'grounding_rate': 1.0, 'details': []},
        'phi_detection': {'passed': True, 'flags': []},
        'dosage_check': {'passed': True, 'flags': []},
        'overall_safe': True,
    }


@pytest.mark.parametrize('width', [1440, 1920, 390])
def test_verification_layered_layout_with_chinese_labels(page, server, width):
    server[1]['verification'] = verification_fixture()
    server[1]['mode'] = 'no-tools'
    page.set_viewport_size({'width': width, 'height': 844 if width == 390 else 1000})
    send(page)
    page.locator('#latest-details').click()
    report = page.locator('.verification-report')
    assert report.locator('.verification-check').count() == 7
    assert report.locator('.check-name').all_text_contents() == ['药物安全校验', '过敏风险检查', '评分构成', '事实依据核对', '敏感信息检查', '剂量检查', '综合规则状态']
    assert report.locator('.verification-check').evaluate_all('(items)=>items.map(n=>n.dataset.check)') == list(server[1]['verification'])
    assert report.locator('details[open]').count() == 0
    assert report.locator('.verification-overview, .verification-status, .verification-raw').count() == 0
    assert page.locator('.details-header .eyebrow').inner_text() == '回答详情'
    # Complete score/tool values remain available in the collapsed technical area.
    assert page.locator('.detail-stat').text_content() == '60%'
    assert '未报告工具调用' in page.locator('#details-content').text_content()
    assert not page.locator('.detail-technical').evaluate('(n)=>n.open')
    page.locator('.detail-section').filter(has=report).scroll_into_view_if_needed()
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(ARTIFACTS / f'verification-{width}.png'))
    assert page.locator('#details-content').evaluate('(n)=>n.scrollWidth <= n.clientWidth')
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    report.locator('[data-check="claim_verification"] > summary').click()
    claims = json.loads(report.locator('[data-check="claim_verification"] .check-raw > pre').text_content())
    assert claims == {'是否通过': True, '有依据的陈述数': 0, '未匹配到依据的陈述数': 0, '陈述总数': 0, '依据匹配比例': 1.0, '详细信息': []}
    report.locator('[data-check="confidence_scoring"] > summary').click()
    scoring = json.loads(report.locator('[data-check="confidence_scoring"] .check-raw > pre').text_content())
    assert scoring == {'评分': 0.6, '评分因素': {'工具使用情况': 0.5, '数据丰富度': 0.5, '不确定性措辞': 1.0, '工具结果情况': 0.5}}
    overall = report.locator('[data-check="overall_safe"]')
    overall.locator(':scope > summary').focus()
    page.keyboard.press('Enter')
    assert overall.evaluate('(n)=>n.open')
    assert overall.locator('.check-value').inner_text() == 'true'
    assert overall.locator('.check-raw > pre').text_content() == 'true'
    page.keyboard.press('Enter')
    assert not overall.evaluate('(n)=>n.open')


def test_verification_translation_keeps_all_values_and_long_lists(page, server):
    data = verification_fixture()
    data['overall_safe'] = False
    data['phi_detection']['flags'] = [{'pattern': 'Phone', 'description': '合成隐私提示', 'match': '合成匹配片段', 'severity': 'moderate'}]
    data['dosage_check'] = {'passed': False, 'flags': [{'note': '合成剂量提示，请核对'}]}
    data['claim_verification'] = {'passed': True, 'grounded_claims': 1, 'ungrounded_claims': 1, 'total_claims': 2, 'grounding_rate': 0.5,
                                  'details': [{'claim': '合成陈述甲', 'grounded': True, 'source_tool': 'patient_summary'}, {'claim': '合成陈述乙', 'grounded': False, 'source_tool': None}]}
    data['drug_safety']['flags'] = [{'issue': f'Synthetic flag {i}', 'severity': 'high', 'drugs': ['test-a', 'test-b']} for i in range(8)]
    server[1]['verification'] = data
    send(page)
    page.locator('#latest-details').click()
    report = page.locator('.verification-report')
    assert report.locator('details[open]').count() == 0  # original interaction, no auto-expansion
    def leaves(value):
        if isinstance(value, dict):
            return [leaf for item in value.values() for leaf in leaves(item)]
        if isinstance(value, list):
            return [leaf for item in value for leaf in leaves(item)]
        return [value]
    for key, value in data.items():
        displayed = json.loads(report.locator(f'[data-check="{key}"] .check-raw > pre').text_content())
        assert leaves(displayed) == leaves(value)
    assert len(json.loads(report.locator('[data-check="drug_safety"] .check-raw > pre').text_content())['提示列表']) == 8
    assert report.locator('[data-check="overall_safe"] pre').text_content() == 'false'


def render_verification_fixture(page, data):
    page.evaluate('''async data => {
        const {renderVerification} = await import('/js/verification.js');
        document.getElementById('verification-fixture')?.remove();
        const box = document.createElement('div'); box.id = 'verification-fixture';
        document.getElementById('details-content').append(box);
        renderVerification(box, data);
    }''', data)
    return page.locator('#verification-fixture')


def test_verification_missing_and_unusual_values_are_not_reinterpreted(page):
    for data in [None, {}, [], 'unexpected']:
        report = render_verification_fixture(page, data)
        assert report.inner_text() == '未提供校验结果。'
    report = render_verification_fixture(page, {
        'overall_safe': 'true', 'drug_safety': {'passed': 'false'}, 'allergy_safety': None,
        'confidence_scoring': {'score': 99, 'factors': {'tools_used': '1', 'data_richness': -1}},
        'claim_verification': {'total_claims': '0'}, 'phi_detection': [], 'dosage_check': False,
    })
    assert report.locator('.verification-check').count() == 7
    assert json.loads(report.locator('[data-check="overall_safe"] pre').text_content()) == 'true'
    assert json.loads(report.locator('[data-check="drug_safety"] pre').text_content()) == {'是否通过': 'false'}
    assert json.loads(report.locator('[data-check="allergy_safety"] pre').text_content()) is None
    assert json.loads(report.locator('[data-check="confidence_scoring"] pre').text_content()) == {'评分': 99, '评分因素': {'工具使用情况': '1', '数据丰富度': -1}}
    assert report.locator('.verification-check[open]').count() == 0


def test_verification_safe_text_and_unknown_fields_remain_in_place(page):
    payload = '<img src="https://attacker.invalid/leak" onerror="window.__checkXss=1">'
    data = {'drug_safety': {'passed': True, 'flags': [payload]}, 'future_check': {'note': payload},
            'claim_verification': {'passed': False, 'details': [{'claim': payload, 'grounded': False, 'source_tool': payload}]}}
    report = render_verification_fixture(page, data)
    assert report.locator('.verification-check').count() == 3
    assert report.locator('.check-name').all_text_contents() == ['药物安全校验', 'future_check', '事实依据核对']
    assert json.loads(report.locator('[data-check="drug_safety"] pre').text_content()) == {'是否通过': True, '提示列表': [payload]}
    assert json.loads(report.locator('[data-check="future_check"] pre').text_content()) == {'备注': payload}
    assert report.locator('img, script, a, iframe').count() == 0
    assert not page.evaluate('!!window.__checkXss')
    # Translating field labels must not overwrite an existing Chinese key, nor
    # alter English JSON-like text embedded inside a string value.
    report = render_verification_fixture(page, {'drug_safety': {'passed': False, '是否通过': '原字段', 'note': '"passed": true'}})
    body = report.locator('pre').text_content()
    assert '"是否通过": false' in body
    assert '"是否通过": "原字段"' in body
    assert '\\"passed\\": true' in body


def test_history_restores_per_answer_details_after_reload(page, server):
    server[1]['verification'] = verification_fixture()
    send(page)
    page.locator('#latest-details').click()
    assert page.locator('.verification-check').count() == 7
    page.reload()
    playwright.expect(page.locator('#latest-details')).to_be_enabled()
    page.locator('#latest-details').click()
    assert '已恢复当轮保存的回答详情' in page.locator('#details-content').inner_text()
    assert page.locator('.verification-check').count() == 7
    assert page.locator('.detail-stat').text_content() == '60%'
    assert '2.5 秒' in page.locator('.detail-label').text_content()
    assert page.locator('.tool-trace').count() == 1
    page.locator('#close-details').click()
    server[1]['mode'] = 'no-tools'
    server[1]['verification'] = {'confidence_scoring': {'score': 0.3}, 'overall_safe': False}
    send(page, '新的合成测试问题')
    page.reload()
    playwright.expect(page.locator('#latest-details')).to_be_enabled()
    page.locator('#latest-details').click()
    assert page.locator('.verification-check').count() == 2
    assert page.locator('.detail-stat').text_content() == '30%'
    assert '未报告工具调用' in page.locator('#details-content').text_content()
    page.locator('#close-details').click()
    page.locator('.message-actions').first.get_by_role('button', name='查看详情', exact=True).click()
    assert page.locator('.verification-check').count() == 7
    assert page.locator('.detail-stat').text_content() == '60%'
    assert len(server[1]['requests']) == 2
    assert page.evaluate('Object.keys(localStorage)') == []
    assert page.evaluate('Object.keys(sessionStorage)') == ['af_v3_conversation_id']


def test_single_tool_copy_and_missing_history(page, server):
    server[1]['mode'] = 'single-tool'
    send(page)
    page.locator('.tool-trace > summary').click()
    assert page.locator('.tool-item').count() == 1
    page.context.grant_permissions(['clipboard-read', 'clipboard-write'])
    page.get_by_role('button', name='复制', exact=True).click()
    playwright.expect(page.get_by_role('button', name='已复制', exact=True)).to_be_visible()
    # Windows clipboard normalizes line endings to CRLF.
    assert page.evaluate('navigator.clipboard.readText()').replace('\r\n', '\n') == ANSWER
    server[1]['mode'] = 'history-error'
    page.locator('#refresh-history').click()
    playwright.expect(page.locator('#history-list')).to_contain_text('不可用')
    page.evaluate("sessionStorage.setItem('af_v3_conversation_id', 'missing')")
    page.reload()
    playwright.expect(page.locator('#notice')).to_contain_text('不存在或已删除')
    assert page.evaluate("sessionStorage.getItem('af_v3_conversation_id')") is None


def test_rename_persists_updates_current_title_and_search(page, server):
    page.get_by_role('button', name='合成数据 · 既有会话', exact=True).click()
    playwright.expect(page.locator('#message-input')).to_be_editable()
    page.locator('.history-rename').click()
    assert page.locator('#rename-input').input_value() == '合成数据 · 既有会话'
    page.locator('#rename-input').fill('  鼻炎复诊记录  ')
    page.locator('#rename-input').press('Enter')
    playwright.expect(page.locator('#rename-dialog')).not_to_be_visible()
    assert page.locator('#conversation-title').inner_text() == '鼻炎复诊记录'
    assert page.locator('.history-select').inner_text() == '鼻炎复诊记录'
    assert server[1]['renames'] == [{'title': '鼻炎复诊记录'}]
    assert page.locator('.markdown').inner_text() == '历史回答，仅供界面测试。'
    page.reload()
    playwright.expect(page.locator('#conversation-title')).to_have_text('鼻炎复诊记录')
    page.locator('#history-search').fill('复诊')
    assert page.locator('.history-select').count() == 1
    page.locator('#history-search').fill('不存在的标题')
    assert page.locator('.history-select').count() == 0


def test_rename_cancel_validation_failure_and_safe_text(page, server):
    page.locator('.history-rename').click()
    page.locator('#rename-input').fill('取消的修改')
    page.locator('#cancel-rename').click()
    assert not server[1]['renames']
    page.locator('.history-rename').click()
    page.locator('#rename-input').fill('  ')
    assert page.locator('#save-rename').is_disabled()
    assert page.locator('#rename-input').get_attribute('maxlength') == '100'
    page.locator('#rename-input').fill('新名称')
    server[1]['mode'] = 'rename-error'
    page.locator('#save-rename').click()
    playwright.expect(page.locator('#rename-error')).to_contain_text('不可用')
    assert page.locator('#rename-dialog').is_visible()
    assert page.locator('.history-select').inner_text() == '合成数据 · 既有会话'
    assert page.locator('#rename-input').input_value() == '新名称'
    server[1]['mode'] = 'normal'
    payload = '<img src=x onerror="window.__renameXss=1">'
    page.locator('#rename-input').fill(payload)
    page.locator('#save-rename').click()
    playwright.expect(page.locator('#rename-dialog')).not_to_be_visible()
    assert page.locator('.history-select').inner_text() == payload
    assert page.locator('#history-list img').count() == 0
    assert not page.evaluate('!!window.__renameXss')


def test_rename_mobile_entry(page):
    page.set_viewport_size({'width': 390, 'height': 844})
    page.locator('#menu-button').click()
    playwright.expect(page.locator('.history-rename')).to_be_visible()
    page.locator('.history-rename').click()
    page.locator('#rename-input').fill('手机端重命名')
    page.locator('#save-rename').click()
    playwright.expect(page.locator('#rename-dialog')).not_to_be_visible()
    assert page.locator('.history-select').inner_text() == '手机端重命名'


@pytest.mark.parametrize('width', [1440, 390])
def test_workspace_views_preserve_draft_and_current_conversation(page, server, width):
    page.set_viewport_size({'width': width, 'height': 844 if width == 390 else 1000})
    assert page.locator('#welcome').is_visible()
    assert page.locator('#home-button').get_attribute('aria-current') == 'page'
    page.locator('#message-input').fill('尚未发送的草稿')
    workspace_view(page, '#history-button')
    assert page.locator('#history-view').is_visible()
    assert page.locator('#history-button').get_attribute('aria-current') == 'page'
    assert page.locator('#home-button').get_attribute('aria-current') != 'page'
    workspace_view(page, '#capabilities-button')
    assert page.locator('#capabilities-view').is_visible()
    assert not page.locator('#main').evaluate('(node) => node.inert')
    workspace_view(page, '#home-button')
    assert page.locator('#welcome').is_visible()
    assert page.locator('#message-input').input_value() == '尚未发送的草稿'
    assert not server[1]['requests']

    workspace_view(page, '#history-button')
    page.locator('.conversation-open').click()
    playwright.expect(page.locator('#chat-view')).to_be_visible()
    playwright.expect(page.locator('#message-input')).to_be_editable()
    assert page.locator('#history-view').is_hidden()
    page.locator('#message-input').fill('当前会话的追问草稿')
    workspace_view(page, '#capabilities-button')
    workspace_view(page, '#history-button')
    workspace_view(page, '#home-button')
    assert page.locator('#conversation-title').inner_text() == '合成数据 · 既有会话'
    assert page.locator('.markdown').inner_text() == '历史回答，仅供界面测试。'
    assert page.locator('#message-input').input_value() == '当前会话的追问草稿'
    assert page.evaluate("sessionStorage.getItem('af_v3_conversation_id')") == 'fixture-history'
    assert not server[1]['requests']
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')

    page.locator('[data-new-chat]:visible').first.click()
    assert page.locator('#welcome').is_visible()
    assert page.locator('#message-input').input_value() == ''
    assert page.locator('.message').count() == 0
    assert page.evaluate("sessionStorage.getItem('af_v3_conversation_id')") is None


def test_conversation_manager_search_sort_and_empty_results(page, server):
    # Deliberately unsorted API data; timestamps are Unix seconds.
    server[1]['conversation_items'] = [
        {'id': 'fixture-c', 'title': 'Gamma 合成会话', 'updated_at': 30},
        {'id': 'fixture-a', 'title': 'Alpha 合成会话', 'updated_at': 10},
        {'id': 'fixture-b', 'title': 'Beta 合成会话', 'updated_at': 20},
    ]
    workspace_view(page, '#history-button')
    page.locator('#refresh-conversations').click()
    playwright.expect(page.locator('.conversation-row')).to_have_count(3)
    entries = page.locator('.conversation-open')
    playwright.expect(entries).to_have_text(['Gamma 合成会话', 'Beta 合成会话', 'Alpha 合成会话'])
    page.locator('#conversation-sort').select_option('oldest')
    playwright.expect(entries).to_have_text(['Alpha 合成会话', 'Beta 合成会话', 'Gamma 合成会话'])
    page.locator('#conversation-sort').select_option('title')
    playwright.expect(entries).to_have_text(['Alpha 合成会话', 'Beta 合成会话', 'Gamma 合成会话'])
    page.locator('#conversation-search').fill('  BETA  ')
    playwright.expect(entries).to_have_text(['Beta 合成会话'])
    page.locator('#conversation-search').fill('没有这样的合成会话')
    playwright.expect(entries).to_have_count(0)
    assert page.locator('#conversation-list').inner_text().strip()
    page.locator('#conversation-search').fill('')
    playwright.expect(entries).to_have_count(3)
    assert not server[1]['requests']


@pytest.mark.parametrize('width', [1440, 390])
def test_conversation_manager_rename_delete_sync_and_keyboard(page, server, width):
    page.set_viewport_size({'width': width, 'height': 844 if width == 390 else 1000})
    workspace_view(page, '#history-button')
    page.locator('.conversation-open').focus()
    page.keyboard.press('Enter')
    playwright.expect(page.locator('#chat-view')).to_be_visible()
    playwright.expect(page.locator('#message-input')).to_be_editable()
    workspace_view(page, '#history-button')
    page.locator('.conversation-rename').click()
    page.locator('#rename-input').fill('管理页合成会话')
    page.locator('#rename-input').press('Enter')
    playwright.expect(page.locator('#rename-dialog')).to_be_hidden()
    playwright.expect(page.locator('.conversation-open')).to_have_attribute('aria-label', '打开会话：管理页合成会话')
    assert page.locator('.history-select').text_content() == '管理页合成会话'
    assert page.locator('#conversation-title').text_content() == '管理页合成会话'
    assert server[1]['renames'] == [{'title': '管理页合成会话'}]
    assert page.locator('#history-view').evaluate('(node) => node.contains(document.activeElement)')
    page.locator('#refresh-conversations').click()
    playwright.expect(page.locator('.conversation-open')).to_have_attribute('aria-label', '打开会话：管理页合成会话')

    page.locator('.conversation-delete').click()
    page.locator('#cancel-delete').click()
    assert not server[1]['deletions']
    assert page.locator('.conversation-row').count() == 1
    page.locator('.conversation-delete').click()
    page.locator('#confirm-delete').click()
    playwright.expect(page.locator('#delete-dialog')).to_be_hidden()
    playwright.expect(page.locator('.conversation-row')).to_have_count(0)
    assert page.locator('.history-select').count() == 0
    assert server[1]['deletions'] == ['fixture-history']
    assert page.evaluate("sessionStorage.getItem('af_v3_conversation_id')") is None
    workspace_view(page, '#home-button')
    assert page.locator('#welcome').is_visible()
    assert page.locator('.message').count() == 0
    assert not server[1]['requests']


def test_conversation_manager_refresh_error_and_recovery(page, server):
    workspace_view(page, '#history-button')
    server[1]['mode'] = 'history-error'
    page.locator('#refresh-conversations').click()
    playwright.expect(page.locator('#history-view')).to_contain_text('不可用')
    server[1]['mode'] = 'normal'
    page.locator('#refresh-conversations').click()
    playwright.expect(page.locator('.conversation-open')).to_have_text('合成数据 · 既有会话')
    assert not server[1]['requests']


@pytest.mark.parametrize('width', [1440, 390])
def test_capability_center_search_categories_and_reset(page, server, width):
    page.set_viewport_size({'width': width, 'height': 844 if width == 390 else 1000})
    workspace_view(page, '#capabilities-button')
    rows = page.locator('#capability-list .capability-row')
    assert rows.count() == 14
    page.locator('#capability-search').fill('  PATIENT_SUMMARY  ')
    playwright.expect(rows).to_have_count(1)
    assert rows.first.get_attribute('data-tool') == 'patient_summary'
    page.locator('#capability-search').fill('用药相互作用')
    playwright.expect(rows).to_have_count(1)
    assert rows.first.get_attribute('data-tool') == 'drug_interaction_check'
    page.locator('#capability-search').fill('参考范围')
    playwright.expect(rows).to_have_count(1)
    assert rows.first.get_attribute('data-tool') == 'lab_results_analysis'
    page.locator('#capability-search').fill('')
    page.locator('#capability-filters [data-category="medication"]').click()
    assert 0 < rows.count() < 14
    assert page.locator('#capability-list [data-tool="drug_interaction_check"]').count() == 1
    assert page.locator('#capability-list [data-tool="record_vitals"]').count() == 0
    page.locator('#capability-search').fill('PATIENT_SUMMARY')
    playwright.expect(rows).to_have_count(0)
    assert page.locator('#capability-list').inner_text().strip()
    page.locator('#clear-capability-filters').click()
    playwright.expect(rows).to_have_count(14)
    assert page.locator('#capability-search').input_value() == ''
    assert page.locator('#capability-filters [data-category="all"]').get_attribute('aria-pressed') == 'true'
    assert page.locator('.capability-badge').count() == 5
    page.locator('#capability-search').fill('不存在的能力名称')
    playwright.expect(rows).to_have_count(0)
    page.locator('#clear-capability-filters').click()
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    assert not server[1]['requests']


@pytest.mark.parametrize('view', ['history', 'capabilities'])
def test_workspace_hash_entry_reload_and_assistant_return(page, server, view):
    page.locator('.history-select').click()
    playwright.expect(page.locator('#message-input')).to_be_editable()
    page.goto(server[0] + f'/workspace/#{view}')
    playwright.expect(page.locator(f'#{view}-view')).to_be_visible()
    assert page.locator('#chat-view').is_hidden()
    page.reload()
    playwright.expect(page.locator(f'#{view}-view')).to_be_visible()
    page.goto(server[0] + '/workspace/#message-input')
    playwright.expect(page.locator('#chat-view')).to_be_visible()
    playwright.expect(page.locator('#message-input')).to_be_focused()
    assert page.locator('#conversation-title').inner_text() == '合成数据 · 既有会话'
    assert page.locator('.markdown').inner_text() == '历史回答，仅供界面测试。'
    assert not server[1]['requests']
    assert page.evaluate('Object.keys(localStorage)') == []
    assert page.evaluate('Object.keys(sessionStorage)') == ['af_v3_conversation_id']


def test_delayed_history_refresh_cannot_revert_successful_rename(page, server):
    mock = server[1]
    workspace_view(page, '#history-button')
    mock['history_gate'] = threading.Event()
    mock['history_started'] = threading.Event()
    page.locator('#refresh-conversations').click()
    wait_for_mock_history_request(page, mock)
    page.locator('.conversation-rename').click()
    page.locator('#rename-input').fill('刷新期间确认的新名称')
    page.locator('#rename-input').press('Enter')
    playwright.expect(page.locator('#rename-dialog')).to_be_hidden()
    playwright.expect(page.locator('.conversation-open')).to_have_attribute('aria-label', '打开会话：刷新期间确认的新名称')
    with page.expect_response(lambda response: response.url.endswith('/api/conversations') and response.request.method == 'GET') as pending:
        mock['history_gate'].set()
    assert pending.value.json()[0]['title'] == '合成数据 · 既有会话'
    # Let the completed fetch update the page before checking the persisted UI.
    page.evaluate('() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))')
    assert page.locator('.conversation-open').get_attribute('aria-label') == '打开会话：刷新期间确认的新名称'
    assert page.locator('.history-select').inner_text() == '刷新期间确认的新名称'
    assert mock['renames'] == [{'title': '刷新期间确认的新名称'}]
    assert not mock['requests']


def test_delete_keeps_workspace_locked_until_delayed_list_reload_finishes(page, server):
    mock = server[1]
    page.locator('.history-select').click()
    playwright.expect(page.locator('#message-input')).to_be_editable()
    workspace_view(page, '#history-button')
    mock['history_gate'] = threading.Event()
    mock['history_started'] = threading.Event()
    page.locator('.conversation-delete').click()
    page.locator('#confirm-delete').click()
    wait_for_mock_history_request(page, mock)
    assert mock['deletions'] == ['fixture-history']
    assert page.locator('#delete-dialog').is_hidden()
    assert page.locator('.conversation-row').count() == 0
    assert page.evaluate("sessionStorage.getItem('af_v3_conversation_id')") is None
    for selector in ('#home-button', '#history-button', '#capabilities-button'):
        assert page.locator(selector).is_disabled()
        page.locator(selector).evaluate('(node) => node.click()')
    assert page.locator('[data-new-chat]').first.is_disabled()
    assert page.locator('#confirm-delete').is_disabled()
    page.locator('#confirm-delete').evaluate('(node) => node.click()')
    assert page.locator('#history-view').is_visible()
    assert page.locator('#message-input').get_attribute('readonly') is not None
    with page.expect_response(lambda response: response.url.endswith('/api/conversations') and response.request.method == 'GET'):
        mock['history_gate'].set()
    playwright.expect(page.locator('#home-button')).to_be_enabled()
    assert page.locator('#conversation-search').evaluate('(node) => node === document.activeElement')
    assert mock['deletions'] == ['fixture-history']
    assert not mock['requests']


@pytest.mark.parametrize('width', [1440, 390])
def test_open_conversation_from_manager_returns_keyboard_focus_to_composer(page, server, width):
    page.set_viewport_size({'width': width, 'height': 844 if width == 390 else 1000})
    workspace_view(page, '#history-button')
    page.locator('.conversation-open').focus()
    page.keyboard.press('Enter')
    playwright.expect(page.locator('#message-input')).to_be_focused()
    playwright.expect(page.locator('#message-input')).to_be_editable()
    assert page.locator('#chat-view').is_visible()
    assert page.locator('#history-view').is_hidden()
    assert not page.locator('#main').evaluate('(node) => node.inert')
    page.keyboard.type('Keyboard draft')
    assert page.locator('#message-input').input_value() == 'Keyboard draft'
    assert not server[1]['requests']
