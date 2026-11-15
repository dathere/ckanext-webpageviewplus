"""A tiny page server for check_gotenberg_redirects.sh.

  fixture.py black            every path: an all-black page (the "secret"
                              on the private network)
  fixture.py redirect <url>   /301, /302, /303, /307, /308: that redirect
                              to <url>; any other path: an all-black page
"""
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer

BLACK = (b"<!doctype html><html><body style='margin:0;background:#000;height:100vh'>"
         b"</body></html>")
MODE = sys.argv[1]
TARGET = sys.argv[2] if len(sys.argv) > 2 else ""


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        code = self.path.strip("/")
        if MODE == "redirect" and code in ("301", "302", "303", "307", "308"):
            self.send_response(int(code))
            self.send_header("Location", TARGET)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(BLACK)))
        self.end_headers()
        self.wfile.write(BLACK)

    def log_message(self, *args):
        pass


HTTPServer(("", 8000), Handler).serve_forever()
