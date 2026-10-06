"""Real message persistence + history routes, with fake model/tools/verifiers.

All databases live under tmp_path. Never load the project's .env or call a
model, OpenEMR, or tracing service.
"""
import asyncio
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from app.response_details import DETAILS_KEY, get_response_details, messages_for_model, with_response_details

ROOT = Path(__file__).resolve().parents[1]


class FakeGraph:
    def __init__(self):
        self.turn = 0
        self.inputs = []

    async def ainvoke(self, state):
        self.turn += 1
        self.inputs.append(list(state['messages']))
        call = {'name': 'fixture_tool', 'args': {'turn': self.turn}, 'id': f'test-{self.turn}'}
        return {'messages': list(state['messages']) + [
            AIMessage(content='', tool_calls=[call]),
            ToolMessage(content='Synthetic result only', tool_call_id=call['id'], name='fixture_tool'),
            AIMessage(content=f'Synthetic answer {self.turn}', response_metadata={'provider_debug': 'not exposed'}),
        ]}

    async def astream_events(self, state, **kwargs):
        self.turn += 1
        self.inputs.append(list(state['messages']))
        yield {'event': 'on_tool_start', 'name': 'fixture_tool', 'data': {'input': {'turn': self.turn}}}
        yield {'event': 'on_tool_end', 'name': 'fixture_tool', 'data': {'output': 'Synthetic result only'}}
        yield {'event': 'on_chat_model_stream', 'data': {'chunk': SimpleNamespace(content=f'Synthetic answer {self.turn}')}}


@pytest.fixture
def setup(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('LANGCHAIN_TRACING_V2', 'false')
    monkeypatch.setenv('LANGSMITH_TRACING', 'false')
    from app import database
    from app.config import settings
    import langchain_anthropic

    monkeypatch.setattr(settings, 'database_path', str(tmp_path / 'history.db'))
    monkeypatch.setattr(settings, 'api_keys', 'fixture-key')
    monkeypatch.setattr(settings, 'llm_provider', 'anthropic')
    monkeypatch.setattr(settings, 'anthropic_api_key', 'fixture-not-a-real-key')
    database.init_db()

    class FakeModel:
        inputs = []

        def bind_tools(self, tools):
            return self

        def invoke(self, messages):
            self.inputs.append(messages)
            return AIMessage(content='Synthetic model-node answer')

    monkeypatch.setattr(langchain_anthropic, 'ChatAnthropic', lambda **kwargs: FakeModel())
    registry = ModuleType('app.tools.registry')
    registry.get_all_tools = lambda: []
    monkeypatch.setitem(sys.modules, 'app.tools.registry', registry)
    verifier = ModuleType('app.verification.pipeline')
    verifier.run_verification_pipeline = lambda **kwargs: {
        'confidence': 0.6, 'disclaimers': ['Synthetic disclaimer only'],
        'verification': {'drug_safety': {'passed': True, 'flags': []}, 'overall_safe': True},
    }
    monkeypatch.setitem(sys.modules, 'app.verification.pipeline', verifier)
    fhir = ModuleType('app.fhir_client')
    fhir.fhir_client = SimpleNamespace()
    monkeypatch.setitem(sys.modules, 'app.fhir_client', fhir)

    def load(name, path):
        spec = importlib.util.spec_from_file_location(name, ROOT / path)
        module = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, name, module)
        spec.loader.exec_module(module)
        return module

    graph = load('app.agent.graph', 'app/agent/graph.py')
    compiled = graph._agent_graph
    graph._agent_graph = fake = FakeGraph()
    routes = load('app.api._response_details_test', 'app/api/routes.py')
    app = FastAPI()
    app.state.limiter = routes.limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
    app.include_router(routes.router, prefix='/api')
    with TestClient(app) as client:
        client.headers['X-API-Key'] = 'fixture-key'
        yield SimpleNamespace(client=client, db=database, graph=graph, fake=fake, compiled=compiled, model=FakeModel)


def ask(setup, mode, cid=None):
    if mode == 'stream':
        async def collect():
            return [json.loads(packet.removeprefix('data: ').strip())
                    async for packet in setup.graph.run_agent_stream('Synthetic question', cid)]
        packets = asyncio.run(collect())
        assert packets[-1]['event'] == 'done'
        return packets[-1]['data']
    return asyncio.run(setup.graph.run_agent('Synthetic question', cid))


@pytest.mark.parametrize('mode', ['stream', 'normal'])
def test_answer_details_round_trip_through_sqlite_and_history_api(setup, mode):
    response = ask(setup, mode)
    cid = response['conversation_id']
    # New DB connections on every load emulate reading after a service restart.
    saved = setup.db.load_messages(cid)
    expected = get_response_details(saved[-1])
    assert expected is not None
    for key, value in expected.items():
        assert value == response[key]
    loaded = setup.client.get(f'/api/conversations/{cid}').json()['messages']
    assert loaded[-1]['metadata'] == expected
    assert loaded[-1]['content'] == response['response']
    assert 'metadata' not in loaded[0]
    assert 'provider_debug' not in json.dumps(loaded)
    assert 'response' not in expected
    assert setup.client.patch(f'/api/conversations/{cid}', json={'title': '新标题'}).status_code == 200
    assert setup.client.get(f'/api/conversations/{cid}').json()['messages'] == loaded


@pytest.mark.parametrize('mode', ['stream', 'normal'])
def test_multiple_turns_stay_bound_to_their_own_answer(setup, mode):
    first = ask(setup, mode)
    second = ask(setup, mode, first['conversation_id'])
    assert second['tool_calls'] == [{'tool': 'fixture_tool', 'args': {'turn': 2}}]
    loaded = setup.client.get(f"/api/conversations/{first['conversation_id']}").json()['messages']
    answers = [entry for entry in loaded if 'metadata' in entry]
    assert [entry['content'] for entry in answers] == [first['response'], second['response']]
    assert [entry['metadata']['tool_calls'][0]['args']['turn'] for entry in answers] == [1, 2]


@pytest.mark.parametrize('mode', ['stream', 'normal'])
def test_context_limit_does_not_delete_older_history_and_details(setup, mode):
    setup.db.create_conversation('long', '长会话')
    original = []
    for i in range(30):
        original.extend([HumanMessage(content=f'question-{i}'), with_response_details(
            AIMessage(content=f'answer-{i}'), {'confidence': i / 100, 'tool_calls': [], 'verification': {'turn': i}})])
    setup.db.save_messages('long', original)
    ask(setup, mode, 'long')
    saved = setup.db.load_messages('long')
    assert len(setup.fake.inputs[-1]) == setup.graph.MAX_HISTORY_MESSAGES + 2
    assert len(saved) > len(original)
    assert [message.content for message in saved[:60]] == [message.content for message in original]
    assert get_response_details(saved[1])['verification'] == {'turn': 0}
    assert get_response_details(saved[59])['verification'] == {'turn': 29}


def test_legacy_answers_do_not_get_invented_details_and_auth_still_applies(setup):
    setup.db.create_conversation('old')
    setup.db.save_messages('old', [HumanMessage(content='old question'), AIMessage(content='old answer')])
    result = setup.client.get('/api/conversations/old').json()
    assert result['messages'] == [{'role': 'user', 'content': 'old question'}, {'role': 'assistant', 'content': 'old answer'}]
    setup.client.headers.pop('X-API-Key')
    assert setup.client.get('/api/conversations/old').status_code == 401


def test_metadata_is_not_sent_back_to_the_model_or_mutated(setup):
    original = with_response_details(AIMessage(content='old answer', response_metadata={'keep': 'provider data'}), {'verification': {'test': 'details'}})
    clean = messages_for_model([original])
    assert DETAILS_KEY not in clean[0].response_metadata
    assert clean[0].response_metadata == {'keep': 'provider data'}
    assert get_response_details(original) == {'verification': {'test': 'details'}}
    asyncio.run(setup.compiled.ainvoke({'messages': [original, HumanMessage(content='next')], 'tool_calls_log': [], 'confidence': None, 'disclaimers': []}))
    assert all(DETAILS_KEY not in message.response_metadata for message in setup.model.inputs[-1])
    assert get_response_details(original) is not None


@pytest.mark.parametrize('mode', ['stream', 'normal'])
def test_save_failure_returns_answer_with_explicit_warning(setup, monkeypatch, mode):
    def fail(*args):
        raise OSError('synthetic save failure')
    monkeypatch.setattr(setup.graph, 'save_messages', fail)
    response = ask(setup, mode)
    assert response['response'].startswith('Synthetic answer')
    assert any('未能保存' in text for text in response['disclaimers'])
    assert setup.db.load_messages(response['conversation_id']) == []
