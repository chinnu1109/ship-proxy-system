#!/usr/bin/env python3
import socket
import threading
import queue
import time
import os
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn
import sys

OFFSHORE_HOST = os.environ.get('OFFSHORE_HOST', '127.0.0.1')
OFFSHORE_PORT = int(os.environ.get('OFFSHORE_PORT', '9999'))
PROXY_HOST = os.environ.get('PROXY_HOST', '0.0.0.0')
PROXY_PORT = int(os.environ.get('PROXY_PORT', '8080'))

MSG_REQUEST = 0
MSG_RESPONSE = 1
MSG_TUNNEL = 2
MSG_TUNNEL_CLOSE = 3

def recv_all(sock, n):
    data = b''
    while len(data) < n:
        part = sock.recv(n - len(data))
        if not part:
            return None
        data += part
    return data

def read_frame(conn):
    header = recv_all(conn, 5)
    if not header:
        return None, None
    length = int.from_bytes(header[:4], 'big')
    typ = header[4]
    payload = recv_all(conn, length)
    if payload is None:
        return None, None
    return typ, payload

def send_frame(conn, typ, payload):
    header = len(payload).to_bytes(4, 'big') + bytes([typ])
    conn.sendall(header + payload)

offshore_sock = None
offshore_lock = threading.Lock()
conn_ready = threading.Event()
request_q = queue.Queue()

def connect_offshore(retries=10, backoff=2):
    global offshore_sock
    for attempt in range(retries):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.connect((OFFSHORE_HOST, OFFSHORE_PORT))
            offshore_sock = s
            conn_ready.set()
            print("Connected to offshore:", OFFSHORE_HOST, OFFSHORE_PORT)
            return True
        except Exception as e:
            print(f"Connect failed (attempt {attempt+1}/{retries}): {e}")
            time.sleep(backoff)
    return False

class ProxyHandler(BaseHTTPRequestHandler):
    def do_CONNECT(self):
        target = self.path  # host:port
        raw_req = f"{self.command} {self.path} {self.request_version}\r\n"
        for h, v in self.headers.items():
            raw_req += f"{h}: {v}\r\n"
        raw_req += "\r\n"
        raw_req_b = raw_req.encode()

        resp_event = threading.Event()
        resp_holder = {}
        request_q.put((self.connection, self.client_address, raw_req_b, resp_event, resp_holder))

        resp_event.wait()
        resp = resp_holder.get('response', b'')
        try:
            self.connection.sendall(resp)
        except Exception:
            return

        if resp.startswith(b"HTTP/1.1 200"):
            def client_to_ship():
                try:
                    while True:
                        data = self.connection.recv(4096)
                        if not data:
                            break
                        with offshore_lock:
                            send_frame(offshore_sock, MSG_TUNNEL, data)
                except Exception:
                    pass
                finally:
                    with offshore_lock:
                        try:
                            send_frame(offshore_sock, MSG_TUNNEL_CLOSE, b'')
                        except:
                            pass

            def ship_to_client_reader():
                try:
                    while True:
                        typ, payload = read_frame(offshore_sock)
                        if typ is None:
                            break
                        if typ == MSG_TUNNEL:
                            self.connection.sendall(payload)
                        elif typ == MSG_TUNNEL_CLOSE:
                            break
                        else:
                            pass
                except Exception:
                    pass

            t1 = threading.Thread(target=client_to_ship, daemon=True)
            t2 = threading.Thread(target=ship_to_client_reader, daemon=True)
            t1.start(); t2.start()
            t1.join(); t2.join()
        else:
            return

    def do_COMMAND(self):
        raw_request = f"{self.command} {self.path} {self.request_version}\r\n"
        for h, v in self.headers.items():
            raw_request += f"{h}: {v}\r\n"
        raw_request += "\r\n"
        cl = self.headers.get('Content-Length')
        body = b''
        if cl:
            try:
                length = int(cl)
                body = self.rfile.read(length)
                raw_request = raw_request.encode() + body
            except:
                raw_request = raw_request.encode()
        else:
            raw_request = raw_request.encode()

        resp_event = threading.Event()
        resp_holder = {}
        request_q.put((self.connection, self.client_address, raw_request, resp_event, resp_holder))

        resp_event.wait()
        response_bytes = resp_holder.get('response', b'')
        try:
            self.connection.sendall(response_bytes)
        except Exception:
            pass

    def do_GET(self): return self.do_COMMAND()
    def do_POST(self): return self.do_COMMAND()
    def do_PUT(self): return self.do_COMMAND()
    def do_DELETE(self): return self.do_COMMAND()
    def do_OPTIONS(self): return self.do_COMMAND()
    def do_HEAD(self): return self.do_COMMAND()
    def do_PATCH(self): return self.do_COMMAND()

class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True

def worker_loop():
    global offshore_sock
    while True:
        item = request_q.get()
        if item is None:
            break
        client_conn, client_addr, raw_req, resp_event, resp_holder = item
        conn_ready.wait()
        try:
            with offshore_lock:
                send_frame(offshore_sock, MSG_REQUEST, raw_req)
                while True:
                    typ, payload = read_frame(offshore_sock)
                    if typ is None:
                        resp_holder['response'] = b"HTTP/1.1 502 Bad Gateway\r\nContent-Length:0\r\n\r\n"
                        resp_event.set()
                        break
                    if typ == MSG_RESPONSE:
                        resp_holder['response'] = payload
                        resp_event.set()
                        break
                    else:
                        pass
        except Exception:
            resp_holder['response'] = b"HTTP/1.1 502 Bad Gateway\r\nContent-Length:0\r\n\r\n"
            resp_event.set()
        finally:
            request_q.task_done()

def start_proxy():
    connected = connect_offshore(retries=10, backoff=2)
    if not connected:
        print("Unable to connect to offshore - exiting")
        sys.exit(1)

    t = threading.Thread(target=worker_loop, daemon=True)
    t.start()

    server = ThreadedHTTPServer((PROXY_HOST, PROXY_PORT), ProxyHandler)
    print(f"Ship proxy listening on {PROXY_HOST}:{PROXY_PORT}")
    server.serve_forever()

if __name__ == "__main__":
    start_proxy()
