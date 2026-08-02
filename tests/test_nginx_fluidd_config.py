from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / 'printer' / 'nginx.compat.example.conf'


def test_fluidd_static_root_and_moonraker_api_proxying_are_configured():
    text = CONFIG.read_text(encoding='utf-8')

    assert 'root /usr/share/fluidd;' in text
    assert 'try_files $uri $uri/ /index.html;' in text
    assert 'location = /status { proxy_pass http://127.0.0.1:9001; }' in text
    assert 'location / { proxy_pass http://127.0.0.1:9001; }' not in text
    assert 'proxy_http_version 1.1;' in text
    assert 'proxy_set_header Upgrade $http_upgrade;' in text
    assert 'proxy_set_header Connection $connection_upgrade;' in text
    assert 'location /server/ {' in text
    assert 'proxy_pass http://127.0.0.1:7126;' in text
    assert 'location /api/ {' in text
    assert 'location /printer/ {' in text
    assert 'location /machine/ {' in text
    assert 'location = /websocket {' in text
    assert 'proxy_pass http://127.0.0.1:7126/websocket;' in text
    assert 'location = /api/streams { proxy_pass http://127.0.0.1:1984; }' in text
    assert 'location = /api/stream.m3u8 {' in text
    assert 'proxy_pass http://127.0.0.1:1984/api/stream.m3u8?src=$stream_source;' in text
    assert 'location = /api/stream.ts {' in text
    assert 'proxy_pass http://127.0.0.1:1984/api/stream.ts?src=$stream_source;' in text
    assert 'location = /api/ws {' in text
    assert 'location = /api/webrtc {\n        proxy_http_version 1.1;\n        proxy_set_header Upgrade $http_upgrade;\n        proxy_set_header Connection $connection_upgrade;\n        proxy_pass http://127.0.0.1:1984;\n    }' in text
    assert 'location = /webrtc {\n        proxy_http_version 1.1;\n        proxy_set_header Upgrade $http_upgrade;\n        proxy_set_header Connection $connection_upgrade;\n        proxy_pass http://127.0.0.1:1984;\n    }' in text
    assert 'location = /camera { proxy_pass http://127.0.0.1:1984; }' in text
    assert 'location = /live {' in text
    assert 'location = /camera { proxy_pass http://127.0.0.1:1984; }' in text
    assert 'location = /api/rest/print/cluster/devices/edit { proxy_pass http://127.0.0.1:9001; }' in text
    assert 'location = /api/rest/print/cluster/addSingleTask { proxy_pass http://127.0.0.1:9001; }' in text
    assert 'location = /api/frame.jpeg {' in text
    assert 'location = /api/frame.jpg {' in text
    assert 'location = /api/frame {' in text
    assert 'location = /hls/playlist.m3u8 {' in text
    assert 'location ^~ /hls/ {' in text
    assert 'location ^~ /webcam/ {' in text
    assert 'location = /webcam/api/ws {' in text
    assert 'proxy_pass http://127.0.0.1:1984/api/ws;' in text
    assert 'proxy_pass http://127.0.0.1:1984;' in text


def test_deploy_nginx_config_exposes_webcam_websocket_proxy():
    text = (ROOT / 'nginx_printer.conf').read_text(encoding='utf-8')

    assert 'location = /webcam/api/ws {' in text
    assert 'proxy_pass http://127.0.0.1:1984/api/ws;' in text


def test_fluidd_api_routes_are_not_shadowed_by_compat_regex():
    text = CONFIG.read_text(encoding='utf-8')

    assert 'location ~ ^/(call|webrtc|live|camera|ws|call/.*) {' in text
    assert 'location ~ ^/(call|webrtc|live|camera|ws|api/ws|api/webrtc|api/streams|api/stream\\.m3u8|api/stream\\.ts|call/.*)' not in text


def test_compat_config_exposes_https_on_port_443():
    text = CONFIG.read_text(encoding='utf-8')

    assert 'listen 443 ssl;' in text
    assert 'ssl_certificate /etc/nginx/conf.d/nrvous.io.crt;' in text
    assert 'ssl_certificate_key /etc/nginx/conf.d/nrvous.io.key;' in text
