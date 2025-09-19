#!/usr/bin/env python3
import socket
import threading
import http.client
import sys

HOST = "0.0.0.0"
PORT = 9999

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

def handle_tunnel_frames(ship_conn, target_sock):
    try:
        while True:
            typ, payload = read_frame(ship_conn)
            if typ is None:
                break
            if typ == MSG_TUNNEL:
                target_sock.sendall(payload)
            elif typ == MSG_TUNNEL_CLOSE:
                break
            else:
                pass
    except Exception:
        pass
    finally:
        try:
            target_sock.shutdown(socket.SHUT_RDWR)
            target_sock.close()
        except:
            pass

def forward_target_to_ship(ship_conn, target_sock):
    try:
        while True:
            chunk = target_sock.recv(4096)
            if not chunk:
                break
            send_frame(ship_conn, MSG_TUNNEL, chunk)
    except Exception:
        pass
    finally:
        try:
            send_frame(ship_conn, MSG_TUNNEL_CLOSE, b'')
        except:
            pass

def process_request_frame(ship_conn, payload):
    try:
        text = payload.decode(errors='ignore')
        first_line = text.split("\r\n", 1)[0]
        parts = first_line.split()
        if len(parts) < 2:
            return b"HTTP/1.1 400 Bad Request\r\nContent-Length:0\r\n\r\n"
        method, uri = parts[0], parts[1]

        if method.upper() == "CONNECT":
            host, port = uri.split(":")
            port = int(port)
            target = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            target.settimeout(10)
            target.connect((host, port))
            resp = b"HTTP/1.1 200 Connection established\r\n\r\n"
            send_frame(ship_conn, MSG_RESPONSE, resp)

            t1 = threading.Thread(target=handle_tunnel_frames, args=(ship_conn, target), daemon=True)
            t2 = threading.Thread(target=forward_target_to_ship, args=(ship_conn, target), daemon=True)
            t1.start(); t2.start()
            t1.join(); t2.join()
            return None
        else:
            headers_part = text.split("\r\n\r\n", 1)[0]
            headers_lines = headers_part.split("\r\n")[1:]
            host = None
            for h in headers_lines:
                if h.lower().startswith("host:"):
                    host = h.split(":",1)[1].strip()
                    break
            if not host:
                if uri.startswith("http://"):
                    host = uri.split("/")[2]
                else:
                    return b"HTTP/1.1 400 Bad Request\r\nContent-Length:0\r\n\r\n"

            if uri.startswith("http://") or uri.startswith("https://"):
                path = "/" + "/".join(uri.split("/")[3:]) if len(uri.split("/"))>3 else "/"
            else:
                path = uri

            body = b''
            if "\r\n\r\n" in text:
                body = text.split("\r\n\r\n",1)[1].encode()

            try:
                conn = http.client.HTTPConnection(host, timeout=15)
                conn.request(method, path, body=body, headers={})
                resp = conn.getresponse()
                resp_body = resp.read()
                resp_bytes = f"HTTP/1.1 {resp.status} {resp.reason}\r\n".encode()
                for k, v in resp.getheaders():
                    resp_bytes += f"{k}: {v}\r\n".encode()
                resp_bytes += b"\r\n" + resp_body
                return resp_bytes
            except Exception:
                return b"HTTP/1.1 502 Bad Gateway\r\nContent-Length:0\r\n\r\n"
    except Exception:
        return b"HTTP/1.1 500 Internal Server Error\r\nContent-Length:0\r\n\r\n"

def accept_loop(server_sock):
    print(f"Offshore listening on {HOST}:{PORT}")
    while True:
        conn, addr = server_sock.accept()
        print("Ship connected from", addr)
        try:
            while True:
                typ, payload = read_frame(conn)
                if typ is None:
                    print("Ship disconnected")
                    break
                if typ == MSG_REQUEST:
                    result = process_request_frame(conn, payload)
                    if result is not None:
                        send_frame(conn, MSG_RESPONSE, result)
                else:
                    pass
        except Exception as e:
            print("Connection handling error:", e)
        finally:
            try:
                conn.close()
            except:
                pass

def main():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind((HOST, PORT))
    s.listen(1)
    accept_loop(s)

if __name__ == "__main__":
    main()
