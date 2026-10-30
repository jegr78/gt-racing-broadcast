#!/usr/bin/env python3
"""HTTP listener startup must not depend on reverse DNS."""
from contextlib import closing
import errno
import http.client
from http.server import BaseHTTPRequestHandler
import importlib.util
import json
from pathlib import Path
import socket
import sys
import threading
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src/scripts'))
sys.path.insert(0, str(ROOT / 'src/ui'))
import ui_server

spec = importlib.util.spec_from_file_location('relay_http_startup', ROOT / 'src/relay/racecast-feeds.py')
relay = importlib.util.module_from_spec(spec)
spec.loader.exec_module(relay)


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        status = 200 if self.path == '/status' else 404
        body = json.dumps({'port': self.server.server_port} if status == 200 else {'error': 'not_found'}).encode()
        self.send_response(status)
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass  # Keep the request assertions readable.


def check_dns_free(server_type, host='127.0.0.1'):
    with patch.object(socket, 'getfqdn', side_effect=AssertionError('HTTP bind attempted reverse DNS')), \
         patch.object(socket, 'gethostbyaddr', side_effect=AssertionError('HTTP bind attempted reverse DNS')):
        with server_type((host, 0), Handler) as server:
            address, port = server.server_address
            assert address == host and port > 0, 'bind must retain the requested address and assign a real port'
            assert server.server_name == host and server.server_port == port, 'HTTP metadata must describe the bound socket'
            worker = threading.Thread(target=server.serve_forever)
            worker.start()
            try:
                with closing(http.client.HTTPConnection('127.0.0.1', port, timeout=3)) as client:
                    client.request('GET', '/status')
                    response = client.getresponse()
                    assert response.status == 200 and json.loads(response.read()) == {'port': port}
                    client.request('GET', '/missing')
                    response = client.getresponse()
                    assert response.status == 404 and json.loads(response.read()) == {'error': 'not_found'}
            finally:
                server.shutdown()
                worker.join(timeout=3)
                assert not worker.is_alive(), 'HTTP test listener did not stop'


def t_relay_listener_never_resolves_reverse_dns():
    check_dns_free(relay.ThreadingHTTPServer)


def t_production_relay_listener_never_resolves_reverse_dns():
    check_dns_free(relay.QuietThreadingHTTPServer)


def t_control_center_listener_never_resolves_reverse_dns():
    check_dns_free(ui_server.UiHTTPServer)


def t_explicit_wildcard_bind_keeps_its_address():
    check_dns_free(relay.ThreadingHTTPServer, '0.0.0.0')


def t_occupied_port_still_fails():
    with socket.socket() as holder:
        # Windows SO_REUSEADDR permits a second listener unless the holder is exclusive.
        if hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
            holder.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        holder.bind(('127.0.0.1', 0))
        holder.listen()
        for server_type in (relay.ThreadingHTTPServer, relay.QuietThreadingHTTPServer, ui_server.UiHTTPServer):
            try:
                with server_type(holder.getsockname(), Handler):
                    raise AssertionError('HTTP listener silently accepted an occupied port')
            except OSError as exc:
                assert exc.errno in {errno.EADDRINUSE, errno.EACCES}, exc


def t_socket_bind_error_is_propagated_unchanged():
    for code in (errno.EADDRINUSE, errno.EACCES):
        failure = OSError(code, 'fixture port is occupied')
        for server_type in (relay.ThreadingHTTPServer, relay.QuietThreadingHTTPServer, ui_server.UiHTTPServer):
            with patch('http_server.TCPServer.server_bind', side_effect=failure):
                try:
                    with server_type(('127.0.0.1', 0), Handler):
                        raise AssertionError('HTTP listener swallowed a socket bind failure')
                except OSError as exc:
                    assert exc is failure, 'HTTP listener must preserve the original socket bind error'


if __name__ == '__main__':
    for name, fn in sorted(list(globals().items())):
        if name.startswith('t_') and callable(fn):
            fn()
            print('ok', name)
    print('ALL PASS')
