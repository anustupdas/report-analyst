import requests

BASE = "http://localhost:5100"


def health_request():
    return requests.get(f"{BASE}/health", headers={"accept": "application/json"})


def version_request():
    return requests.get(f"{BASE}/version", headers={"accept": "application/json"})


def embed_request(text: str, input_type: str = "passage"):
    url = f"{BASE}/api/v1/embed_text"
    headers = {
        "accept": "application/json",
        "application": "local-script",
        "Content-Type": "application/json",
    }
    payload = {"text": text, "input_type": input_type, "model_name": "gte-multilingual-base"}
    response = requests.post(url, headers=headers, json=payload)
    try:
        print(response.status_code, response.json())
    except ValueError:
        print(response.status_code, response.text)
    return response


if __name__ == "__main__":
    print("health", health_request().json())
    print("version", version_request().json())
    embed_request("Net profit rose in 2024", input_type="passage")
