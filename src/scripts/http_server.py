"""HTTP listeners that bind without a reverse DNS lookup."""
from http.server import ThreadingHTTPServer as _ThreadingHTTPServer
from socketserver import TCPServer


class NumericThreadingHTTPServer(_ThreadingHTTPServer):
    """Use the bound address as server_name; retain normal socket bind errors."""

    request_queue_size = 128

    def server_bind(self):
        # HTTPServer's reverse lookup can block macOS startup on an unavailable DNS service.
        TCPServer.server_bind(self)
        self.server_name, self.server_port = self.server_address[:2]
