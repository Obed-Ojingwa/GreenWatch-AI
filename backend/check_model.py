import getpass
import httpx

key = getpass.getpass("Gemini API key (input hidden): ")
response = httpx.get(
    "https://generativelanguage.googleapis.com/v1beta/models",
    headers={"x-goog-api-key": key},
    timeout=20,
)
if response.is_error:
    print(f"Model list request failed: HTTP {response.status_code}")
    raise SystemExit(1)

models = response.json().get("models", [])
for model in models:
    methods = model.get("supportedGenerationMethods", [])
    if "generateContent" in methods and "flash" in model.get("baseModelId", "").lower():
        print(model.get("baseModelId", model.get("name", "").removeprefix("models/")))
