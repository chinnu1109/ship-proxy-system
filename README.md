# Ship Proxy System (local demo)

Run offshore:
  python3 offshore_server.py

Run ship:
  python3 ship_proxy.py

Test:
  curl -x http://localhost:8080 http://example.com -v
  curl -x http://localhost:8080 https://example.com -v

Docker (optional):
  docker-compose up --build
