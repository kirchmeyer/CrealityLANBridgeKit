#!/usr/bin/env bash
# Fix /info endpoint to return required model/address fields
# Usage: ssh root@192.168.1.100 'bash -s' < scripts/fix_info_json.sh

HOST="${PRINTER_HOST:-192.168.1.100}"
CONF="/etc/nginx/nginx.conf"
TEMP="/tmp/nginx_info_fix.py"

cat > "$TEMP" << 'PYTHON'
import subprocess, sys, os

content = open("/etc/nginx/nginx.conf").read()

old = """        location = /info {
            proxy_pass http://apiserver/printer/info;
            proxy_set_header Host $http_host;
            proxy_set_header X-Real-IP $remote_addr;
            proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
            proxy_set_header X-Scheme $scheme;
        }"""

hostname = subprocess.check_output(
    ["python3", "-c", "import urllib.request, json; "
     "r=urllib.request.urlopen('http://127.0.0.1:7125/printer/info'); "
     "print(json.loads(r.read())['result']['hostname'])"],
    stderr=subprocess.STDOUT
).decode().strip()

new = f"""        location = /info {{
            default_type application/json;
            return 200 '{{"mac":"00:00:00:00:00:00","model":"Creality K2 Plus","modelName":"Creality K2 Plus","model_name":"Creality K2 Plus","name":"{hostname}","address":"192.168.1.100","deviceName":"192.168.1.100","aliasName":"{hostname}","type":0,"online":true,"connectType":1001,"deviceType":0,"video":true,"identity":"192.168.1.100"}}';
        }}"""

if old in content:
    content = content.replace(old, new)
    open("/etc/nginx/nginx.conf", "w").write(content)
    print("OK: modified /info")
else:
    print("ERROR: pattern not found")
    sys.exit(1)
PYTHON

python3 "$TEMP" && nginx -s reload && echo "nginx reloaded"
rm -f "$TEMP"
