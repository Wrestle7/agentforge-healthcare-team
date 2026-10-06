"""No app.main import, credentials, database, model calls or external services."""
from pathlib import Path
import hashlib
import re
from urllib.parse import urlsplit

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.frontend import mount_frontends

ROOT = Path(__file__).resolve().parents[1]


def client():
    app = FastAPI()

    @app.get('/api/health')
    def health():
        return {'status': 'ok', 'fixture': True}

    mount_frontends(app, ROOT)
    return TestClient(app)


def test_landing_is_default():
    response = client().get('/')
    assert response.status_code == 200
    assert '让复杂的医疗信息' in response.text
    assert 'href="/workspace/#message-input"' in response.text
    assert response.text.count('href="/workspace/') == 6
    assert '<body data-theme="a">' in response.text
    assert 'id="landing-question"' in response.text
    assert '<script defer src="/js/landing.js?v=20261005-boot2"></script>' in response.text
    assert '/js/app.js' not in response.text
    assert "connect-src 'self'" in response.text
    assert response.headers['cache-control'] == 'no-store'
    assert 'text/html' in response.headers['content-type']


def test_workspace_direct_entry_and_redirect():
    c = client()
    response = c.get('/workspace', follow_redirects=False)
    assert response.status_code == 308
    assert response.headers['location'] == '/workspace/'
    response = c.get('/workspace/')
    assert response.status_code == 200
    assert response.content == (ROOT / 'frontend-v3/index.html').read_bytes()
    assert response.headers['cache-control'] == 'no-store'


def test_classic_redirect_and_current_bytes():
    c = client()
    response = c.get('/classic', follow_redirects=False)
    assert response.status_code == 308
    assert response.headers['location'] == '/classic/'
    assert c.get('/classic/').content == (ROOT / 'frontend-v2/index.html').read_bytes()
    for resource in ('js/app.js', 'css/tokens.css', 'assets/favicon.svg'):
        assert c.get('/classic/' + resource).content == (ROOT / 'frontend-v2' / resource).read_bytes()


def test_workspace_assets_and_missing_assets():
    c = client()
    for resource in ('js/app.js', 'js/response-details.js', 'js/verification.js', 'css/response.css', 'js/landing.js', 'js/landing-entry.js', 'js/workspace-entry.js', 'js/patients.js', 'css/patients.css', 'js/theme.js', 'css/theme.css', 'css/workspace.css', 'css/workspace-shell.css', 'css/landing.css', 'vendor/markdown-it.min.js', 'vendor/purify.min.js', 'assets/mark.svg', 'assets/mark-landing.svg'):
        response = c.get('/' + resource)
        assert response.status_code == 200
        assert 'text/html' not in response.headers['content-type']
    assert c.get('/js/missing.js').status_code == 404
    assert c.get('/workspace/missing.js').status_code == 404
    assert c.get('/classic/js/missing.js').status_code == 404
    assert c.get('/.env').status_code == 404
    assert c.get('/classic/%2e%2e/.env').status_code == 404


def test_api_still_has_priority():
    assert client().get('/api/health').json() == {'status': 'ok', 'fixture': True}


def test_display_brand_preserves_landing_headline():
    c = client()
    for path in ('/', '/workspace/', '/classic/'):
        html = c.get(path).text
        assert 'Medical Agent' in html
        assert '你的医疗信息助手' in html
        assert not re.search(r'agent\s*forge', html, re.IGNORECASE)
    landing = c.get('/').text
    assert '<h1 id="landing-title">让复杂的医疗信息，<br>从一个问题开始。</h1>' in landing
    assert '问点什么，从了解开始。' in landing
    assert '不作为诊断或处方' in landing


def test_theme_loads_before_page_styles_without_inline_script():
    for path, version in (('/', '20261005-boot2'), ('/workspace/', '20261005-boot2')):
        html = client().get(path).text
        assert html.index(f'<script src="/js/theme.js?v={version}"></script>') < html.index(f'href="/css/theme.css?v={version}"')
        assert 'unsafe-inline' not in html
    classic = client().get('/classic/').text
    assert '/js/theme.js' not in classic


def test_page_assets_bypass_legacy_unversioned_cache():
    c = client()
    for path, version, count in (('/', '20261005-boot2', 5), ('/workspace/', '20261005-boot2', 7)):
        html = c.get(path).text
        assets = re.findall(r'(?:href|src)="(/(?:css|js)/[^"]+)"', html)
        assert len(assets) == count
        for asset in assets:
            parsed = urlsplit(asset)
            assert parsed.query == f'v={version}'
            response = c.get(asset)
            assert response.status_code == 200
            assert response.content == (ROOT / 'frontend-v3' / parsed.path.lstrip('/')).read_bytes()


def test_vendored_dependency_checksums():
    vendor = ROOT / 'frontend-v3/vendor'
    for line in (vendor / 'SHA256SUMS.txt').read_text().splitlines():
        expected, name = line.split()
        assert hashlib.sha256((vendor / name).read_bytes()).hexdigest().upper() == expected


def test_all_workspace_module_imports_share_the_entry_release():
    entry = (ROOT / 'frontend-v3/index.html').read_text(encoding='utf-8')
    release = re.search(r'/js/workspace-entry.js\?v=([^"\s]+)', entry).group(1)
    modules = ROOT / 'frontend-v3/js'
    for script in modules.glob('*.js'):
        imports = re.findall(r'(?:from\s*|import\s*\()([\'\"])(\./[^\'\"]+)\1', script.read_text(encoding='utf-8'))
        for _, source in imports:
            parsed = urlsplit(source)
            assert parsed.query == f'v={release}', f'{script.name}: unversioned/mixed dependency {source}'
            assert (modules / parsed.path).is_file()


def test_workspace_static_resources_and_304_responses_disable_cache():
    c = client()
    for path in ('/js/app.js?v=20261005-boot2', '/js/verification.js', '/js/startup.js', '/css/response.css', '/vendor/purify.min.js'):
        response = c.get(path)
        assert response.status_code == 200
        assert response.headers['cache-control'] == 'no-store'
        conditional = c.get(path, headers={'If-None-Match': response.headers['etag']})
        assert conditional.status_code == 304
        assert conditional.headers['cache-control'] == 'no-store'
