import requests


def health_request():
    url = "http://localhost:5000/health"
    return requests.get(url, headers={"accept": "application/json"})


def version_request():
    url = "http://localhost:5000/version"
    return requests.get(url, headers={"accept": "application/json"})


def text_extraction_request(file_ref: str, use_ocr: bool = False):
    url = "http://localhost:5000/api/v1/extract/file"
    headers = {
        "accept": "application/json",
        "application": "local-script",
        "Content-Type": "application/json",
    }
    payload = {"file_ref": file_ref, "use_ocr": use_ocr}
    response = requests.post(url, headers=headers, json=payload)
    try:
        print(response.status_code, response.json())
    except ValueError:
        print(response.status_code, response.text)
    return response


if __name__ == "__main__":
    print("health", health_request().json())
    print("version", version_request().json())
    text_extraction_request("sample.pdf", use_ocr=False)
