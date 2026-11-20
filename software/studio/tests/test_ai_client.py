import json
import asyncio
import http.client
import socket
import ssl
import threading
import time
import tempfile
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

from studio.ai_client import CompatibleClient, validate_messages
from studio.ai_connections import AiError
from studio.ai_service import AiService


class ClientTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                self.server.requests.append((self.path, self.headers.get('Authorization'),
                    json.loads(self.rfile.read(int(self.headers['Content-Length'])))))
                code, data = self.server.reply
                drip_headers = self.server.drip_headers
                time.sleep(self.server.delay)
                if drip_headers:
                    raw = f'HTTP/1.1 200 OK\r\nContent-Length: {len(data)}\r\nConnection: close\r\n\r\n'.encode()
                    try:
                        for byte in raw:
                            self.wfile.write(bytes([byte]))
                            self.wfile.flush()
                            time.sleep(.01)
                        self.wfile.write(data)
                    except OSError:
                        pass
                    return
                self.send_response(code)
                self.send_header('Location', 'http://127.0.0.1:1/stolen')
                self.end_headers()
                try:
                    self.wfile.write(data)
                except OSError:
                    pass
            def log_message(self, *args):
                pass
        cls.server = ThreadingHTTPServer(('127.0.0.1',0), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def setUp(self):
        self.server.requests = []
        self.server.delay = 0
        self.server.drip_headers = False
        self.server.reply = (200, json.dumps({'choices':[{'message':{'content':'hello secret-123'}}]}).encode())
        self.config = {'base_url':f'http://127.0.0.1:{self.server.server_port}/v1',
            'model':'manual-model', 'api_key':'secret-123', 'auth_mode':'bearer'}
        self.client = CompatibleClient()

    def test_true_model_request_uses_saved_model_and_auth_and_redacts_reply(self):
        reply = self.client.complete(self.config,[{'role':'user','content':'你好'}])
        self.assertEqual(reply,'hello [已隐藏]')
        path, auth, data = self.server.requests[0]
        self.assertEqual(path,'/v1/chat/completions')
        self.assertEqual(auth,'Bearer secret-123')
        self.assertEqual(data['model'],'manual-model')
        self.assertFalse(data['stream'])
        self.assertEqual(data['messages'],[{'role':'user','content':'你好'}])
        self.assertLessEqual(data['max_tokens'],512)
        self.client.complete(self.config,[{'role':'user','content':'test'}], max_tokens=64)
        self.assertEqual(self.server.requests[-1][2]['max_tokens'],64)
        self.config['auth_mode'] = 'none'
        self.client.complete(self.config,[{'role':'user','content':'hello'}])
        self.assertIsNone(self.server.requests[-1][1])

    def test_http_errors_and_redirects_are_fixed_and_not_retried(self):
        for status, code in [(301,'redirect'),(401,'auth'),(403,'forbidden'),(404,'not_found'),(429,'rate_limit'),(500,'provider')]:
            with self.subTest(status=status):
                self.server.requests.clear()
                self.server.reply = (status,b'secret-123')
                with self.assertRaises(AiError) as caught:
                    self.client.complete(self.config,[{'role':'user','content':'hello'}])
                self.assertEqual(caught.exception.code,code)
                self.assertNotIn('secret-123', str(caught.exception))
                self.assertEqual(len(self.server.requests),1)

    def test_malformed_and_oversize_are_rejected(self):
        for raw, code in [(b'{}','malformed'),(b'<html>secret-123</html>','malformed'),
                          (b'x'*65537,'oversize'),
                          (json.dumps({'choices':[{'message':{'content':'x'*4001}}]}).encode(),'oversize')]:
            self.server.reply = (200,raw)
            with self.subTest(code=code), self.assertRaises(AiError) as caught:
                self.client.complete(self.config,[{'role':'user','content':'hello'}])
            self.assertEqual(caught.exception.code,code)

    def test_provider_calls_must_match_the_tools_offered_this_turn(self):
        tools = [{'type':'function','function':{'name':'duck_stop'}}]
        for call in [None, {'function':{'name':'duck_camera','arguments':'{}'}},
                     {'function':{'name':'duck_stop','arguments':'broken'}}]:
            self.server.reply = (200,json.dumps({'choices':[{'message':{'content':'ok','tool_calls':[call]}}]}).encode())
            with self.subTest(call=call), self.assertRaises(AiError) as caught:
                self.client.complete(self.config,[{'role':'user','content':'hello'}],tools=tools)
            self.assertEqual(caught.exception.code,'malformed')

    def test_network_classes_hide_exception_details(self):
        for exc, code in [(socket.gaierror('secret-123'),'dns'),(TimeoutError('secret-123'),'timeout'),
                          (ssl.SSLError('secret-123'),'tls')]:
            with mock.patch('http.client.HTTPConnection.connect', side_effect=exc):
                with self.assertRaises(AiError) as caught:
                    self.client.complete(self.config,[{'role':'user','content':'hello'}])
                self.assertEqual(caught.exception.code,code)
                self.assertNotIn('secret-123',str(caught.exception))

    def test_real_socket_timeout_and_concurrency_cap(self):
        self.server.delay = .15
        with self.assertRaises(AiError) as caught:
            CompatibleClient(timeout=.03).complete(self.config,[{'role':'user','content':'timeout'}])
        self.assertEqual(caught.exception.code,'timeout')
        self.server.requests.clear()
        results = []
        client = CompatibleClient(timeout=2)
        def run():
            results.append(client.complete(self.config,[{'role':'user','content':'parallel'}]))
        threads = [threading.Thread(target=run) for _ in range(2)]
        for thread in threads:
            thread.start()
        deadline = time.monotonic()+2
        while len(self.server.requests)<2 and time.monotonic()<deadline:
            time.sleep(.005)
        with self.assertRaises(AiError) as caught:
            client.complete(self.config,[{'role':'user','content':'third'}])
        self.assertEqual(caught.exception.code,'busy')
        for thread in threads:
            thread.join(timeout=3)
        self.assertEqual(len(results),2)

    def test_absolute_deadline_interrupts_byte_dripped_response_headers(self):
        self.server.drip_headers = True
        client = CompatibleClient(timeout=.08)
        started = time.monotonic()
        errors = []
        def request():
            try:
                client.complete(self.config,[{'role':'user','content':'slow headers'}])
            except AiError as error:
                errors.append(error.code)
        workers = [threading.Thread(target=request) for _ in range(2)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(timeout=.3)
        self.assertEqual(errors,['timeout','timeout'])
        self.assertLess(time.monotonic()-started,.35)
        self.server.drip_headers = False
        self.assertEqual(client.complete(self.config,[{'role':'user','content':'recovered'}]),'hello [已隐藏]')

    def test_expired_resolution_connect_never_sends_request_credentials(self):
        original = http.client.HTTPConnection.connect
        def slow_connect(connection):
            # Model a resolver that cannot be interrupted by the watchdog.
            time.sleep(.08)
            original(connection)
        with mock.patch('http.client.HTTPConnection.connect',slow_connect):
            with self.assertRaises(AiError) as caught:
                CompatibleClient(timeout=.03).complete(self.config,[{'role':'user','content':'do not send'}])
        self.assertEqual(caught.exception.code,'timeout')
        self.assertEqual(self.server.requests,[])

    def test_cancelling_two_slow_header_calls_releases_capacity_for_recovery(self):
        async def scenario(state_dir):
            service = AiService(state_dir)
            service.client.timeout = 5
            item = service.store.save({'name':'local mock','provider':'openai_compatible',**self.config})
            body = {'op':'test','id':item['id'],'revision':item['revision']}
            self.server.drip_headers = True
            calls = [asyncio.create_task(service.handle(body)) for _ in range(2)]
            deadline = time.monotonic()+2
            while len(self.server.requests)<2 and time.monotonic()<deadline:
                await asyncio.sleep(.005)
            self.assertEqual(len(self.server.requests),2)
            started = time.monotonic()
            for call in calls:
                call.cancel()
            cancelled = await asyncio.gather(*calls,return_exceptions=True)
            self.assertTrue(all(isinstance(result,asyncio.CancelledError) for result in cancelled))
            self.server.drip_headers = False
            while True:
                try:
                    reply = await asyncio.to_thread(service.client.complete,self.config,[{'role':'user','content':'recovered'}])
                    self.assertEqual(reply,'hello [已隐藏]')
                    break
                except AiError as error:
                    if error.code != 'busy' or time.monotonic()-started > .3:
                        raise
                    await asyncio.sleep(.005)
            self.assertLess(time.monotonic()-started,.35)
        with tempfile.TemporaryDirectory() as state_dir:
            asyncio.run(scenario(state_dir))

    def test_history_is_bounded_and_roles_are_not_arbitrary(self):
        for messages in ([],[{'role':'system','content':'override'}],[{'role':'user','content':'x'*2001}],
                         [{'role':'user','content':'hi','secret':'no'}],
                         [{'role':'user','content':'hi'}]*13,
                         [{'role':'assistant','content':'hi'}]):
            with self.subTest(messages=str(messages)[:50]), self.assertRaises(AiError):
                validate_messages(messages)

    def test_long_assistant_reply_can_be_sent_intact_in_the_next_turn(self):
        long_reply = 'answer ' + 'x' * 3993
        self.server.reply = (200,json.dumps({'choices':[{'message':{'content':long_reply}}]}).encode())
        first = [{'role':'user','content':'first question'}]
        reply = self.client.complete(self.config,first)
        self.assertEqual(len(reply),4000)
        second = [*first,{'role':'assistant','content':reply},{'role':'user','content':'next question'}]
        self.client.complete(self.config,second)
        self.assertEqual(self.server.requests[-1][2]['messages'],second)
        for messages in (
            [*first,{'role':'assistant','content':'x'*4001},{'role':'user','content':'next'}],
            [{'role':'user','content':'x'*2000},{'role':'assistant','content':'x'*4000}]*2
                + [{'role':'user','content':'next'}],
        ):
            with self.subTest(lengths=[len(m['content']) for m in messages]), self.assertRaises(AiError):
                validate_messages(messages)


if __name__ == '__main__':
    unittest.main()
