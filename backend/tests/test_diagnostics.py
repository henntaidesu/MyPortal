import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi import FastAPI
from fastapi.responses import RedirectResponse, StreamingResponse
from fastapi.testclient import TestClient
from app import diagnostics

class DiagnosticsTests(unittest.TestCase):
    def test_streaming_and_redirect_logs_hide_credentials(self):
        app = FastAPI()
        app.add_middleware(diagnostics.RequestDiagnostics)
        @app.get('/api/proxy/card/login')
        def login():
            return RedirectResponse('https://portal.example.com/api/oidc/authorize?code=PRIVATE_CODE&state=PRIVATE_STATE')
        @app.get('/api/proxy/card/stream')
        def stream():
            return StreamingResponse(iter([b'first', b'second']))
        output = io.StringIO()
        with contextlib.redirect_stdout(output), TestClient(app) as client:
            response = client.get('/api/proxy/card/login?client_secret=PRIVATE_SECRET&code=PRIVATE_CODE&base=https%3A%2F%2Fuser%3APRIVATE_PASSWORD%40portal.example.com%2Fapp%2F%3Ftoken%3DPRIVATE_TOKEN',
                headers={'cookie': 'session=PRIVATE_COOKIE', 'authorization': 'Bearer PRIVATE_BEARER'}, follow_redirects=False)
            streamed = client.get('/api/proxy/card/stream')
        self.assertEqual(response.status_code, 307)
        self.assertEqual(streamed.content, b'firstsecond')
        self.assertEqual(len(response.headers['x-portal-trace']), 12)
        log = output.getvalue()
        self.assertIn('request.begin', log)
        self.assertIn('response.start', log)
        self.assertIn('request.end', log)
        self.assertNotIn('PRIVATE_', log)

    def test_errors_are_logged_before_response_without_exception_secrets(self):
        app = FastAPI()
        app.add_middleware(diagnostics.RequestDiagnostics)
        @app.get('/api/proxy/card/error')
        def error():
            raise RuntimeError('PRIVATE_SECRET')
        output = io.StringIO()
        with contextlib.redirect_stdout(output), TestClient(app, raise_server_exceptions=False) as client:
            self.assertEqual(client.get('/api/proxy/card/error').status_code, 500)
        self.assertIn('request.error', output.getvalue())
        self.assertIn('RuntimeError', output.getvalue())
        self.assertNotIn('PRIVATE_SECRET', output.getvalue())

    def test_file_output_and_unrelated_requests(self):
        with tempfile.TemporaryDirectory() as folder:
            try:
                with contextlib.redirect_stdout(io.StringIO()):
                    diagnostics.configure(folder)
                    diagnostics.event('test.saved')
                self.assertIn('test.saved', (Path(folder) / 'logs/proxy-diagnostics.log').read_text(encoding='utf-8'))
            finally:
                for handler in list(diagnostics._file.handlers):
                    handler.close()
                    diagnostics._file.removeHandler(handler)
        app = FastAPI()
        app.add_middleware(diagnostics.RequestDiagnostics)
        @app.get('/healthz')
        def health():
            return {'ok': True}
        output = io.StringIO()
        with contextlib.redirect_stdout(output), TestClient(app) as client:
            self.assertTrue(client.get('/healthz').json()['ok'])
        self.assertEqual(output.getvalue(), '')
