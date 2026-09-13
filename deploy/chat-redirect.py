"""Keep the former development URL pointed at the single OpenUI service."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class Redirect(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(308)
        self.send_header("Location", "http://100.91.77.114:8768" + self.path)
        self.send_header("Content-Length", "0")
        self.end_headers()

    do_HEAD = do_GET
    do_POST = do_GET


if __name__ == "__main__":
    ThreadingHTTPServer(("100.91.77.114", 8770), Redirect).serve_forever()
