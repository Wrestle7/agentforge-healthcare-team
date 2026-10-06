"""Actual rename route + SQLite, isolated from .env, LLM and FHIR clients."""
import importlib.util
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from langchain_core.messages import HumanMessage
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def setup(monkeypatch, tmp_path):
    # Config/SlowAPI must not read the real project's .env.
    monkeypatch.chdir(tmp_path)
    from app import database
    from app.config import settings

    monkeypatch.setattr(settings, 'database_path', str(tmp_path / 'test.db'))
    monkeypatch.setattr(settings, 'api_keys', 'fixture-key')
    database.init_db()
    database.create_conversation('fixture', '原名称')
    database.save_messages('fixture', [HumanMessage(content='聊天内容保持不变')])

    graph = ModuleType('app.agent.graph')
    async def forbidden(*args, **kwargs):
        raise AssertionError('A rename must not invoke the model')
    graph.run_agent = graph.run_agent_stream = forbidden
    fhir = ModuleType('app.fhir_client')
    fhir.fhir_client = SimpleNamespace()
    monkeypatch.setitem(sys.modules, 'app.agent.graph', graph)
    monkeypatch.setitem(sys.modules, 'app.fhir_client', fhir)
    name = 'app.api._rename_routes_test'
    spec = importlib.util.spec_from_file_location(name, ROOT / 'app/api/routes.py')
    routes = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, name, routes)
    spec.loader.exec_module(routes)
    app = FastAPI()
    app.state.limiter = routes.limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
    app.include_router(routes.router, prefix='/api')
    with TestClient(app) as client:
        client.headers['X-API-Key'] = 'fixture-key'
        yield client, database


def test_title_persists_and_messages_unchanged(setup):
    client, database = setup
    response = client.patch('/api/conversations/fixture', json={'title': '  鼻炎咨询 · 复诊  '})
    assert response.status_code == 200
    assert response.json()['title'] == '鼻炎咨询 · 复诊'
    assert response.json()['id'] == 'fixture'
    assert isinstance(response.json()['updated_at'], float)
    assert client.get('/api/conversations/fixture').json()['title'] == '鼻炎咨询 · 复诊'
    assert client.get('/api/conversations').json()[0]['title'] == '鼻炎咨询 · 复诊'
    assert database.load_messages('fixture')[0].content == '聊天内容保持不变'


@pytest.mark.parametrize('title', ['', ' \t\n ', 'a' * 101, 'first\nsecond', 'a\x00b', None, 123])
def test_invalid_title_rejected_without_mutation(setup, title):
    client, database = setup
    assert client.patch('/api/conversations/fixture', json={'title': title}).status_code == 422
    assert database.get_conversation_metadata('fixture')['title'] == '原名称'


def test_boundary_and_plain_text_title(setup):
    client, database = setup
    assert client.patch('/api/conversations/fixture', json={'title': '中' * 100}).status_code == 200
    text = '<img src=x onerror=alert(1)> "\' 中文标题'
    assert client.patch('/api/conversations/fixture', json={'title': text}).json()['title'] == text
    assert database.get_conversation_metadata('fixture')['title'] == text


def test_missing_conversation_does_not_create_one(setup):
    client, database = setup
    assert client.patch('/api/conversations/missing', json={'title': '标题'}).status_code == 404
    assert database.get_conversation_metadata('missing') is None


def test_same_authentication_required(setup):
    client, database = setup
    client.headers.pop('X-API-Key')
    assert client.patch('/api/conversations/fixture', json={'title': '标题'}).status_code == 401
    client.headers['X-API-Key'] = 'wrong-key'
    assert client.patch('/api/conversations/fixture', json={'title': '标题'}).status_code == 401
    assert database.get_conversation_metadata('fixture')['title'] == '原名称'
