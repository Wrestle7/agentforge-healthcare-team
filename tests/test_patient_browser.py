"""Patient workspace UI checks using only synthetic, intercepted GET responses.

Reuse the isolated static-app/browser fixtures; no production app, database,
model, FHIR server, or container is started or contacted.
"""
from copy import deepcopy
import json
import re
import time
from urllib.parse import parse_qs, urlsplit

import pytest

from tests.test_frontend_browser import (
    ARTIFACTS, browser, page, playwright, server, workspace_view,
)


PATIENTS = [
    {'id': 'synthetic-alpha', 'name': '合成患者 Alpha', 'birth_date': '1980-01-02', 'gender': 'female'},
    {'id': 'synthetic-beta', 'name': '合成患者 Beta', 'birth_date': '1990-03-04', 'gender': 'male'},
]


def section_payload(patient_id, section):
    suffix = patient_id.rsplit('-', 1)[-1]
    parts = {
        'medications': {
            'medications': [{'id': f'med-{suffix}', 'medication': f'Synthetic medicine {suffix}', 'medication_code': 'fixture', 'status': 'active', 'intent': 'order', 'authored_on': '2026-01-01', 'dosage': 'Synthetic dosage only'}],
            'allergies': [{'id': f'allergy-{suffix}', 'substance': f'Synthetic allergy {suffix}', 'type': 'allergy', 'category': ['medication'], 'criticality': 'low', 'clinical_status': 'active', 'reactions': ['Synthetic rash']}],
        },
        'labs': {
            'observations': [{'id': f'lab-{suffix}', 'test_name': f'Synthetic lab {suffix}', 'test_code': 'fixture', 'value': 8.4, 'unit': 'fixture-unit', 'status': 'final', 'date': '2026-01-02', 'reference_range': '4–10', 'interpretation': 'normal', 'category': ['laboratory'], 'components': []}],
        },
        'history': {
            'conditions': [{'id': f'condition-{suffix}', 'display': f'Synthetic condition {suffix}', 'code': 'fixture', 'system': 'synthetic', 'clinical_status': 'active', 'verification_status': 'confirmed', 'onset': '2026-01-03'}],
            'encounters': [{'id': f'encounter-{suffix}', 'type': 'Synthetic visit', 'status': 'finished', 'start': '2026-01-03', 'end': '2026-01-03', 'reason': f'Synthetic encounter {suffix}'}],
        },
    }
    return {'patient_id': patient_id, 'section': section, 'source': 'mock', 'parts': {
        name: {'status': 'ok', 'items': items, 'has_more': False}
        for name, items in parts[section].items()
    }}


@pytest.fixture
def patients(page, server):
    state = {
        'requests': [], 'items': deepcopy(PATIENTS), 'list_error': False,
        'profile_errors': set(), 'overrides': {}, 'hold': set(), 'pending': [],
    }

    def respond(route):
        request = route.request
        parsed = urlsplit(request.url)
        query = parse_qs(parsed.query, keep_blank_values=True)
        state['requests'].append({'method': request.method, 'path': parsed.path, 'query': query})
        assert request.method == 'GET', 'The patient workspace must only issue read requests'
        status = 200
        if parsed.path == '/api/patients':
            if state['list_error']:
                status, body = 503, {'detail': 'Synthetic patient list unavailable'}
            else:
                text = query.get('q', [''])[0].strip().casefold()
                items = [item for item in state['items'] if text in f"{item['name']} {item['id']}".casefold()]
                body = {'items': items, 'query': query.get('q', [''])[0], 'limit': 20, 'has_more': False, 'source': 'mock'}
        else:
            segments = parsed.path.split('/')[3:]
            patient_id = segments[0]
            profile = next((item for item in state['items'] if item['id'] == patient_id), None)
            if profile is None:
                status, body = 404, {'detail': 'Synthetic patient missing'}
            elif len(segments) == 1:
                if patient_id in state['profile_errors']:
                    status, body = 503, {'detail': 'Synthetic profile unavailable'}
                else:
                    body = {**profile, 'source': 'mock'}
            else:
                section = segments[1]
                body = deepcopy(state['overrides'].get((patient_id, section), section_payload(patient_id, section)))
        if parsed.path in state['hold']:
            state['pending'].append((route, status, deepcopy(body)))
            return
        route.fulfill(status=status, content_type='application/json', body=json.dumps(body, ensure_ascii=False))

    page.route(re.compile(r'/api/patients(?:[/?]|$)'), respond)
    yield state
    for route, _, _ in state['pending']:
        try:
            route.abort()
        except playwright.Error:
            pass  # The page may already have cancelled an obsolete fetch.
    assert all(request['method'] == 'GET' for request in state['requests'])
    assert not server[1]['requests'], 'Patient browsing must never submit a model request'
    assert not server[1]['renames'] and not server[1]['deletions'] and not server[1]['feedback']


def open_patients(page):
    workspace_view(page, '#patients-button')
    playwright.expect(page.locator('#patients-view')).to_be_visible()
    playwright.expect(page.locator('.patient-option')).to_have_count(2)


def choose_patient(page, patient_id='synthetic-alpha'):
    page.locator(f'.patient-option[data-patient-id="{patient_id}"]').click()
    playwright.expect(page.locator('#patient-identity')).to_contain_text(patient_id)
    playwright.expect(page.locator('#patient-panel')).to_have_attribute('aria-busy', 'false')


def choose_section(page, section):
    page.locator(f'#patient-tabs [data-patient-section="{section}"]').click()


def clinical_paths(patients):
    return [request['path'] for request in patients['requests'] if request['path'].count('/') == 4]


def wait_for_pending(page, patients):
    deadline = time.monotonic() + 5
    while not patients['pending'] and time.monotonic() < deadline:
        page.wait_for_timeout(20)
    assert patients['pending'], 'Expected a held synthetic patient response'


def test_patient_entry_search_and_explicit_selection_are_lazy(page, server, patients):
    page.reload()
    playwright.expect(page.locator('#message-input')).to_be_editable()
    assert not patients['requests']
    page.locator('#message-input').fill('Preserved synthetic draft')
    open_patients(page)
    assert patients['requests'] == [{'method': 'GET', 'path': '/api/patients', 'query': {'q': [''], 'limit': ['20']}}]
    assert page.locator('#patient-identity').is_hidden()

    for query, patient_id in (('Alpha', 'synthetic-alpha'), ('synthetic-beta', 'synthetic-beta')):
        page.locator('#patient-search').fill(query)
        page.locator('#patient-search-submit').click()
        playwright.expect(page.locator('.patient-option')).to_have_count(1)
        playwright.expect(page.locator('.patient-option')).to_have_attribute('data-patient-id', patient_id)
        assert patients['requests'][-1]['query'] == {'q': [query], 'limit': ['20']}
        assert not clinical_paths(patients)
    choose_patient(page, 'synthetic-beta')
    assert patients['requests'][-1]['path'] == '/api/patients/synthetic-beta'
    assert not clinical_paths(patients)
    playwright.expect(page.locator('#patient-identity')).to_contain_text('合成患者 Beta')
    playwright.expect(page.locator('#patients-view')).to_contain_text('演示数据')

    choose_section(page, 'medications')
    playwright.expect(page.locator('#patient-panel')).to_contain_text('Synthetic medicine beta')
    assert clinical_paths(patients) == ['/api/patients/synthetic-beta/medications']
    choose_section(page, 'labs')
    playwright.expect(page.locator('#patient-panel')).to_contain_text('Synthetic lab beta')
    choose_section(page, 'history')
    playwright.expect(page.locator('#patient-panel')).to_contain_text('Synthetic condition beta')
    playwright.expect(page.locator('#patient-panel')).to_contain_text('Synthetic encounter beta')
    assert clinical_paths(patients) == [f'/api/patients/synthetic-beta/{section}' for section in ('medications', 'labs', 'history')]
    playwright.expect(page.locator('#patient-identity')).to_contain_text('synthetic-beta')
    workspace_view(page, '#home-button')
    assert page.locator('#message-input').input_value() == 'Preserved synthetic draft'


def test_patient_hash_entries_only_load_candidates_until_selection(page, server, patients):
    for section in ('overview', 'medications', 'labs', 'history'):
        page.goto(server[0] + f'/workspace/#patients-{section}')
        playwright.expect(page.locator('#patients-view')).to_be_visible()
        # A same-document hash switch intentionally keeps the chosen patient.
        # Reload to exercise a fresh deep-link entry with no stored selection.
        patients['requests'].clear()
        page.reload()
        playwright.expect(page.locator('.patient-option')).to_have_count(2)
        assert len(patients['requests']) == 1
        assert patients['requests'][0]['path'] == '/api/patients'
        assert not clinical_paths(patients)
        assert page.locator('#patient-identity').is_hidden()
        choose_patient(page)
        if section != 'overview':
            playwright.expect(page.locator('#patient-panel')).to_contain_text(f'Synthetic {dict(medications="medicine", labs="lab", history="condition")[section]} alpha')
            assert clinical_paths(patients) == [f'/api/patients/synthetic-alpha/{section}']
        else:
            assert not clinical_paths(patients)
        assert page.url.endswith(f'#patients-{section}')


def test_patient_search_empty_and_list_error_retry(page, patients):
    patients['list_error'] = True
    workspace_view(page, '#patients-button')
    retry = page.locator('[data-patient-retry="list"]')
    playwright.expect(retry).to_be_visible()
    assert page.locator('.patient-option').count() == 0
    patients['list_error'] = False
    retry.click()
    playwright.expect(page.locator('.patient-option')).to_have_count(2)
    page.locator('#patient-search').fill('不存在的合成患者')
    page.locator('#patient-search').press('Enter')
    playwright.expect(page.locator('.patient-option')).to_have_count(0)
    assert page.locator('#patient-list').inner_text().strip()
    assert patients['requests'][-1]['query']['q'] == ['不存在的合成患者']
    assert not clinical_paths(patients)


def test_patient_profile_failure_retries_before_clinical_fetch(page, server, patients):
    patients['profile_errors'].add('synthetic-alpha')
    page.goto(server[0] + '/workspace/#patients-labs')
    playwright.expect(page.locator('.patient-option')).to_have_count(2)
    page.locator('.patient-option[data-patient-id="synthetic-alpha"]').click()
    retry = page.locator('[data-patient-retry="detail"]')
    playwright.expect(retry).to_be_visible()
    assert not clinical_paths(patients)
    patients['profile_errors'].clear()
    retry.click()
    playwright.expect(page.locator('#patient-panel')).to_contain_text('Synthetic lab alpha')
    assert clinical_paths(patients) == ['/api/patients/synthetic-alpha/labs']


def test_patient_section_partial_failure_keeps_available_data_and_retries(page, patients):
    payload = section_payload('synthetic-alpha', 'medications')
    payload['parts']['allergies'] = {'status': 'unavailable', 'items': [], 'message': 'Synthetic upstream unavailable', 'has_more': False}
    patients['overrides'][('synthetic-alpha', 'medications')] = payload
    open_patients(page)
    choose_patient(page)
    choose_section(page, 'medications')
    playwright.expect(page.locator('#patient-panel')).to_contain_text('Synthetic medicine alpha')
    retry = page.locator('#patient-panel [data-patient-retry="section"]').first
    playwright.expect(retry).to_be_visible()
    assert 'Synthetic allergy alpha' not in page.locator('#patient-panel').inner_text()
    patients['overrides'].clear()
    retry.click()
    playwright.expect(page.locator('#patient-panel')).to_contain_text('Synthetic allergy alpha')
    assert clinical_paths(patients) == ['/api/patients/synthetic-alpha/medications'] * 2


def test_patient_empty_clinical_records_are_explicit(page, patients):
    payload = section_payload('synthetic-alpha', 'labs')
    payload['parts']['observations']['items'] = []
    patients['overrides'][('synthetic-alpha', 'labs')] = payload
    open_patients(page)
    choose_patient(page)
    choose_section(page, 'labs')
    playwright.expect(page.locator('#patient-panel')).to_contain_text(re.compile('暂无|未找到|没有|未记录|未返回|无.*记录'))
    assert page.locator('#patient-panel [data-patient-retry="section"]').count() == 0
    playwright.expect(page.locator('#patient-identity')).to_contain_text('synthetic-alpha')


def test_patient_change_ignores_late_previous_patient_response(page, patients):
    open_patients(page)
    choose_patient(page)
    patients['hold'].add('/api/patients/synthetic-alpha/medications')
    choose_section(page, 'medications')
    wait_for_pending(page, patients)
    page.locator('#patient-change').click()
    playwright.expect(page.locator('.patient-option')).to_have_count(2)
    assert page.locator('#patient-identity').is_hidden()
    choose_patient(page, 'synthetic-beta')
    choose_section(page, 'medications')
    playwright.expect(page.locator('#patient-panel')).to_contain_text('Synthetic medicine beta')
    pending, patients['pending'] = patients['pending'], []
    for route, status, body in pending:
        try:
            route.fulfill(status=status, content_type='application/json', body=json.dumps(body, ensure_ascii=False))
        except playwright.Error:
            pass  # Aborting the obsolete request also satisfies isolation.
    page.evaluate('() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))')
    playwright.expect(page.locator('#patient-identity')).to_contain_text('synthetic-beta')
    panel = page.locator('#patient-panel').inner_text()
    assert 'Synthetic medicine beta' in panel
    assert 'Synthetic medicine alpha' not in panel
    assert 'synthetic-alpha' not in page.url


def test_patient_records_are_plain_text_and_not_persisted(page, patients):
    unsafe_name = '<img src=x onerror="window.patientInjected=true"> Synthetic PHI name'
    unsafe_medication = '<a href="https://invalid.example/phi">Synthetic PHI medicine</a>'
    patients['items'][0]['name'] = unsafe_name
    payload = section_payload('synthetic-alpha', 'medications')
    payload['parts']['medications']['items'][0]['medication'] = unsafe_medication
    patients['overrides'][('synthetic-alpha', 'medications')] = payload
    storage = page.evaluate('({local: {...localStorage}, session: {...sessionStorage}})')
    open_patients(page)
    playwright.expect(page.locator('.patient-option').first).to_contain_text(unsafe_name)
    assert page.locator('#patient-list img').count() == 0
    choose_patient(page)
    playwright.expect(page.locator('#patient-identity')).to_contain_text(unsafe_name)
    assert page.locator('#patient-identity img').count() == 0
    choose_section(page, 'medications')
    playwright.expect(page.locator('#patient-panel')).to_contain_text(unsafe_medication)
    assert page.locator('#patient-panel a[href="https://invalid.example/phi"]').count() == 0
    assert page.evaluate('window.patientInjected === undefined')
    assert page.evaluate('({local: {...localStorage}, session: {...sessionStorage}})') == storage
    assert 'synthetic-alpha' not in page.url and 'PHI' not in page.url
    page.reload()
    playwright.expect(page.locator('.patient-option')).to_have_count(2)
    assert page.locator('#patient-identity').is_hidden()


def test_patient_enum_labels_preserve_unknown_codes_and_clinical_text(page, patients):
    patients['items'][0]['gender'] = 'UnKnOwN'
    medications = section_payload('synthetic-alpha', 'medications')
    medicine = medications['parts']['medications']['items'][0]
    medications['parts']['medications']['items'] = [
        {**medicine, 'id': f'enum-med-{index}', 'medication': f'order allergy Unknown medicine {index}',
         'medication_code': 'order', 'dosage': 'plan medication Unknown dosage', 'intent': intent,
         'status': 'Unknown' if index == 2 else 'Vendor-Status' if index == 3 else 'active'}
        for index, intent in enumerate(('order', 'plan', 'proposal', 'Vendor-Intent'))
    ]
    allergy = medications['parts']['allergies']['items'][0]
    medications['parts']['allergies']['items'] = [
        {**allergy, 'id': f'enum-allergy-{index}', 'substance': f'medication allergy substance {index}',
         'type': allergy_type, 'category': ['medication', 'food', 'environment', 'biologic', 'Vendor-Category'],
         'criticality': 'unknown', 'reactions': ['allergy medication Unknown reaction']}
        for index, allergy_type in enumerate(('allergy', 'intolerance'))
    ]
    history = section_payload('synthetic-alpha', 'history')
    encounter = history['parts']['encounters']['items'][0]
    history['parts']['encounters']['items'] = [
        {**encounter, 'id': f'enum-encounter-{index}', 'status': status,
         'reason': 'planned order allergy free-text reason'}
        for index, status in enumerate(('finished', 'planned', 'in-progress', 'Vendor-Encounter'))
    ]
    patients['overrides'][('synthetic-alpha', 'medications')] = medications
    patients['overrides'][('synthetic-alpha', 'history')] = history

    def displayed_fields(card):
        return dict(zip(card.locator('dt').all_text_contents(), card.locator('dd').all_text_contents()))

    open_patients(page)
    choose_patient(page)
    assert displayed_fields(page.locator('.patient-overview-card'))['性别'] == '未提供'
    choose_section(page, 'medications')
    medicines = page.locator('.patient-record-medications')
    playwright.expect(medicines).to_have_count(4)
    for index, label in enumerate(('医嘱', '计划', '建议', 'Vendor-Intent')):
        card = medicines.nth(index)
        assert card.locator('h3').inner_text() == f'order allergy Unknown medicine {index}'
        fields = displayed_fields(card)
        assert fields['医嘱意图'] == label
        assert fields['药品编码'] == 'order'
        assert fields['登记用法'] == 'plan medication Unknown dosage'
    assert displayed_fields(medicines.nth(2))['状态'] == '未提供'
    assert displayed_fields(medicines.nth(3))['状态'] == 'Vendor-Status'
    allergies = page.locator('.patient-record-allergies')
    playwright.expect(allergies).to_have_count(2)
    for index, label in enumerate(('过敏', '不耐受')):
        card = allergies.nth(index)
        assert card.locator('h3').inner_text() == f'medication allergy substance {index}'
        fields = displayed_fields(card)
        assert fields['类型'] == label
        assert fields['分类'] == '药物、食物、环境、生物制品、Vendor-Category'
        assert fields['严重性分级'] == '未提供'
        assert fields['反应记录'] == 'allergy medication Unknown reaction'
    choose_section(page, 'history')
    encounters = page.locator('.patient-record-encounters')
    playwright.expect(encounters).to_have_count(4)
    for index, label in enumerate(('已结束', '已计划', '进行中', 'Vendor-Encounter')):
        fields = displayed_fields(encounters.nth(index))
        assert fields['状态'] == label
        assert fields['就诊原因'] == 'planned order allergy free-text reason'


@pytest.mark.parametrize('theme', ['a', 'b'])
@pytest.mark.parametrize('width', [1440, 1920, 390])
def test_patient_keyboard_layout_and_theme_screenshots(page, server, patients, theme, width):
    page.set_viewport_size({'width': width, 'height': 844 if width == 390 else 1000})
    page.goto(server[0] + '/')
    page.locator(f'[data-theme-choice="{theme}"]').click()
    page.locator('#ask-entry').click()
    playwright.expect(page.locator('#message-input')).to_be_focused(timeout=15000)
    if width == 390:
        page.locator('#menu-button').click()
    page.locator('#patients-button').focus()
    page.keyboard.press('Enter')
    playwright.expect(page.locator('.patient-option')).to_have_count(2)
    assert not page.locator('#main').evaluate('(node) => node.inert')
    page.locator('#patient-search').focus()
    page.keyboard.type('synthetic-beta')
    page.keyboard.press('Enter')
    playwright.expect(page.locator('.patient-option')).to_have_count(1)
    page.locator('.patient-option').focus()
    page.keyboard.press('Enter')
    playwright.expect(page.locator('#patient-identity')).to_contain_text('synthetic-beta')
    page.locator('#patient-tabs [data-patient-section="medications"]').focus()
    page.keyboard.press('Enter')
    playwright.expect(page.locator('#patient-panel')).to_contain_text('Synthetic medicine beta')
    assert page.locator('html').get_attribute('data-theme') == theme
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    assert page.locator('#patients-view').evaluate('(node) => node.contains(document.activeElement)')
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(ARTIFACTS / f'patients-{theme}-{width}.png'), full_page=True, animations='disabled')
