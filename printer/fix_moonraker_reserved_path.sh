#!/bin/sh

SOURCE_INFO=/usr/share/moonraker/utils/source_info.py
BACKUP=${SOURCE_INFO}.bak.bridge

[ -f "$SOURCE_INFO" ] || exit 0

package_path=$(/usr/share/moonraker-env/bin/python - <<'PY' 2>/dev/null
from moonraker.utils import source_info
print(source_info.package_path())
PY
)
[ "$package_path" = /usr/share/moonraker ] || exit 0

if grep -q '^    return package_path()$' "$SOURCE_INFO"; then
    exit 0
fi
grep -q '^    return package_path().parent$' "$SOURCE_INFO" || exit 0

[ -f "$BACKUP" ] || cp "$SOURCE_INFO" "$BACKUP"
sed -i 's/^    return package_path().parent$/    return package_path()/' "$SOURCE_INFO"
/usr/share/moonraker-env/bin/python -m py_compile "$SOURCE_INFO"
printf 'patched\n'