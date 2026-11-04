import getpass
import json
import urllib.error
import urllib.request

diagnostic_key = getpass.getpass("Render diagnostic key: ")
request = urllib.request.Request(
    "https://greenwatch-ai-670g.onrender.com/api/meta/diagnostics",
    headers={"X-Meta-Diagnostic-Key": diagnostic_key},
    method="POST",
)

try:
    with urllib.request.urlopen(request, timeout=30) as response:
        print("HTTP:", response.status)
        print(json.dumps(json.load(response), indent=2))
except urllib.error.HTTPError as error:
    print("HTTP:", error.code)
    print(error.read().decode("utf-8", errors="replace"))








# python -c "import secrets; print(secrets.token_hex(32))"  9f3fe9e7e9be3792db94eafe9ac6b2cd8868b1549c510209b0de7579e75d965a