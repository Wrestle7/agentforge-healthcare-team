"""Phase A: presentation fidelity, disclosure state and offline visual acceptance."""
import json
import threading

import pytest

from tests.test_frontend_browser import (
    ANSWER, ARTIFACTS, browser, page, playwright, send, server, verification_fixture,
)


def details(page):
    page.locator('#latest-details').click()
    return page.locator('#details-content')


def test_answer_first_compact_process_and_actions(page, server):
    send(page)
    response = page.locator('.message.assistant')
    assert response.locator('.response-state').inner_text() == '回答已返回'
    assert response.locator('.tool-trace').evaluate('(n)=>!n.open')
    assert response.locator('.markdown').evaluate('''node => Boolean(
        node.compareDocumentPosition(node.parentElement.querySelector('.message-process')) & Node.DOCUMENT_POSITION_FOLLOWING)''')
    assert response.locator('.tool-items pre, .tool-items code').count() == 0
    page.locator('.tool-trace > summary').click()
    assert '2 次工具调用' in response.locator('.tool-trace > summary').inner_text()
    assert '执行成功' not in response.locator('.tool-trace').inner_text()
    assert response.get_by_role('button', name='复制', exact=True).is_enabled()
    response.get_by_role('button', name='有帮助', exact=True).click()
    playwright.expect(response.get_by_role('button', name='有帮助', exact=True)).to_have_attribute('aria-pressed', 'true')
    assert server[1]['feedback'] == [{'conversation_id': 'fixture-chat', 'rating': 'up'}]
    trigger = response.get_by_role('button', name='查看详情', exact=True)
    trigger.click()
    assert trigger.get_attribute('aria-expanded') == 'true'
    page.keyboard.press('Escape')
    assert trigger.get_attribute('aria-expanded') == 'false'
    playwright.expect(trigger).to_be_focused()


def test_layers_link_to_real_flags_and_preserve_parameters(page, server):
    report = verification_fixture()
    report['drug_safety'] = {'passed': False, 'flags': [{'issue': '仅作布局测试的提示', 'severity': 'test-only'}]}
    report['overall_safe'] = False
    server[1]['verification'] = report
    send(page, '本轮合成问题，用于关联详情')
    box = details(page)
    assert box.locator('.detail-question').inner_text() == '本轮合成问题，用于关联详情'
    assert '第 1 条回答' in box.locator('.detail-context-heading').inner_text()
    assert box.locator('.detail-disclaimer').inner_text() == '仅用于测试，不用于诊疗。'
    assert box.locator('.reported-notice').count() == 2
    assert not box.locator('.detail-technical').evaluate('(n)=>n.open')
    box.locator('[data-check-target="drug_safety"]').click()
    check = box.locator('[data-check="drug_safety"]')
    assert check.evaluate('(n)=>n.open')
    playwright.expect(check.locator(':scope > summary')).to_be_focused()
    assert '接口报告未通过' in check.inner_text()
    assert '仅作布局测试的提示' in check.locator('.check-body').inner_text()
    box.locator('.detail-technical > summary').click()
    assert box.locator('.detail-stat').inner_text() == '60%'
    assert '2.5 秒' in box.locator('.detail-label').inner_text()
    assert '不是医疗准确率' in box.inner_text()
    tool = box.locator('.detail-tool').first
    tool.locator('summary').click()
    assert json.loads(tool.locator('pre').inner_text()) == {'tool': 'patient_summary', 'args': {'patient_identifier': '合成患者'}}
    box.locator('.detail-raw > summary').click()
    original = json.loads(box.locator('.detail-raw > pre').inner_text())
    assert original['response'] == ANSWER
    assert original['verification'] == report
    assert original['confidence'] == 0.6
    assert original['latency_ms'] == 2480
    assert len(original['tool_calls']) == 2
    assert len(server[1]['requests']) == 1  # disclosures never call the model


def test_stream_preserves_details_nodes_scroll_and_focus(page, server):
    mock = server[1]
    mock['mode'] = 'gated'
    mock['stream_gates'] = [threading.Event(), threading.Event()]
    page.locator('#message-input').fill('合成流式交互测试')
    page.locator('#send-button').click()
    playwright.expect(page.locator('.assistant-label .response-state')).to_have_text('正在生成')
    box = details(page)
    assert '最终校验详情尚未返回' in box.inner_text()
    box.locator('.detail-technical > summary').click()
    tool = box.locator('.detail-tool').first
    tool.locator('summary').click()
    box.locator('.detail-technical > summary').focus()
    initial = box.evaluate('''node => {
        window.__retainedDisclosure = node.querySelector('.detail-technical');
        node.scrollTop = 180; return node.scrollTop;
    }''')
    mock['stream_gates'][0].set()
    playwright.expect(page.locator('.markdown table')).to_be_visible()
    assert box.evaluate('node => node.querySelector(".detail-technical") === window.__retainedDisclosure')
    assert box.evaluate('node => node.scrollTop') == initial
    playwright.expect(box.locator('.detail-technical > summary')).to_be_focused()
    mock['stream_gates'][1].set()
    playwright.expect(page.locator('#message-input')).to_be_editable()
    assert box.locator('.detail-technical').evaluate('(n)=>n.open')
    assert box.locator('.detail-tool').first.evaluate('(n)=>n.open')
    playwright.expect(box.locator('.detail-technical > summary')).to_be_focused()
    assert box.evaluate('node => node.scrollTop') == initial
    assert box.locator('.response-state').inner_text() == '回答已返回'
    assert len(mock['requests']) == 1


def test_selected_answer_never_changes_during_followup(page, server):
    server[1]['verification'] = {'confidence_scoring': {'score': 0.6}, 'overall_safe': True}
    send(page, '合成第一问')
    box = details(page)
    box.locator('[data-check="overall_safe"] > summary').click()
    server[1]['verification'] = {'confidence_scoring': {'score': 0.3}, 'overall_safe': False}
    send(page, '合成第二问')
    assert box.locator('.detail-question').inner_text() == '合成第一问'
    assert box.locator('.detail-stat').text_content() == '60%'
    assert box.locator('[data-check="overall_safe"]').evaluate('(n)=>n.open')
    page.locator('#latest-details').click()
    assert box.locator('.detail-question').inner_text() == '合成第二问'
    assert '第 2 条回答' in box.locator('.detail-context-heading').inner_text()
    assert box.locator('.detail-stat').text_content() == '30%'
    assert box.locator('details[open]').count() == 0
    page.locator('#close-details').click()
    page.reload()
    playwright.expect(page.locator('#latest-details')).to_be_enabled()
    details(page)
    assert box.locator('.response-state').inner_text() == '历史回答'
    assert box.locator('.detail-question').inner_text() == '合成第二问'
    assert len(server[1]['requests']) == 2


@pytest.mark.parametrize('mode', ['401', '429', '503', 'broken', 'no-metadata'])
def test_incomplete_responses_are_explicit_without_retries(page, server, mode):
    server[1]['mode'] = mode
    send(page)
    box = details(page)
    if mode != 'no-metadata':
        assert box.locator('.response-state').inner_text() == '本轮中断'
        assert '不会自动重新提交' in box.inner_text()
    assert box.locator('.detail-missing').is_visible()
    assert box.locator('.verification-report').inner_text() == '未提供校验结果。'
    assert box.locator('.detail-stat').text_content() == '未提供'
    assert box.locator('.reported-notice').count() == 0
    if mode == 'broken':
        assert '合成测试数据' in page.locator('.markdown').inner_text()
    assert len(server[1]['requests']) == 1


def test_raw_extra_fields_xss_and_strict_status_types(page):
    payload = '<img src="https://attacker.invalid/leak" onerror="window.detailXss=1">'
    meta = {'confidence': 0.6, 'latency_ms': 0, 'token_usage': {'total_tokens': 123}, 'future_field': {'passed': False},
            'disclaimers': [payload], 'tool_calls': [{'tool': 'future_tool', 'args': {'note': payload}, 'extra': 'keep-me'}],
            'verification': {'drug_safety': {'passed': 'false', 'flags': []}, 'overall_safe': 'false',
                             'confidence_scoring': {'score': 0.01, 'unknown': payload}, 'unknown_check': {'data': payload}}}
    page.evaluate('''async meta => {
        const {renderResponseDetails} = await import('/js/response-details.js');
        renderResponseDetails(document.getElementById('details-content'),
            {meta, phase:'done', history:false, calls:meta.tool_calls}, {question:'Synthetic only'});
    }''', meta)
    box = page.locator('#details-content')
    assert box.locator('.reported-notice').count() == 0  # no coercion or score-based risk inference
    assert box.locator('.check-state.is-flagged, .check-state.is-reported').count() == 0
    assert box.locator('.detail-stat').text_content() == '60%'  # do not recompute from internal score
    assert json.loads(box.locator('.detail-raw > pre').text_content()) == meta
    assert json.loads(box.locator('.detail-tool pre').text_content()) == meta['tool_calls'][0]
    assert box.locator('script,img,iframe,a').count() == 0
    assert not page.evaluate('!!window.detailXss')
    assert page.evaluate('Object.keys(localStorage)') == []
    assert page.evaluate('Object.keys(sessionStorage)') == []


@pytest.mark.parametrize('theme', ['a', 'b'])
@pytest.mark.parametrize('width', [1440, 1920, 390])
def test_response_visual_states(page, server, theme, width):
    page.set_viewport_size({'width': width, 'height': 844 if width == 390 else 1000})
    page.evaluate('(theme)=>sessionStorage.setItem("af_v3_theme", theme)', theme)
    page.reload()
    playwright.expect(page.locator('#message-input')).to_be_editable()
    data = verification_fixture()
    data['drug_safety'] = {'passed': False, 'flags': [{'issue': '合成界面提示：请核对原始记录', 'severity': 'synthetic'}]}
    data['overall_safe'] = False
    server[1]['verification'] = data
    send(page, '查看合成记录的摘要与检查结果')
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(ARTIFACTS / f'answer-{theme}-{width}.png'))
    box = details(page)
    page.screenshot(path=str(ARTIFACTS / f'answer-details-{theme}-{width}.png'))
    box.locator('[data-check-target="drug_safety"]').click()
    page.screenshot(path=str(ARTIFACTS / f'answer-check-{theme}-{width}.png'))
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    assert box.evaluate('node=>node.scrollWidth <= node.clientWidth')
    page.keyboard.press('Escape')
    composer = page.locator('#composer').bounding_box()
    assert composer['y'] + composer['height'] <= page.viewport_size['height']
    assert page.locator('#message-scroll').bounding_box()['height'] > 150
