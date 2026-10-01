from fastapi import status


class TextExtractionError(Exception):
    status_code: int = status.HTTP_500_INTERNAL_SERVER_ERROR
    detail: str = "Text extraction failed."

    def __init__(self, detail: str | None = None) -> None:
        self.detail = detail or self.__class__.detail
        super().__init__(self.detail)


class ConfigurationFileNotFoundError(TextExtractionError):
    status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
    detail = "Configuration file not found."


class MistralApiKeyMissingError(TextExtractionError):
    status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
    detail = "MISTRAL_API_KEY is missing."


class FileDownloadError(TextExtractionError):
    status_code = status.HTTP_404_NOT_FOUND
    detail = "File could not be found or downloaded."


class S3ConnectionError(TextExtractionError):
    status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
    detail = "S3 connection is not available."


class UnsupportedFormatError(TextExtractionError):
    status_code = status.HTTP_415_UNSUPPORTED_MEDIA_TYPE
    detail = "Unsupported file format."


class FileNotFoundOnDiskError(TextExtractionError):
    status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
    detail = "File not found on disk during processing."


class InvalidFileContentError(TextExtractionError):
    status_code = status.HTTP_400_BAD_REQUEST
    detail = "Invalid file input or content."


class TextExtractionFailedError(TextExtractionError):
    status_code = status.HTTP_422_UNPROCESSABLE_CONTENT
    detail = "Text could not be extracted from the file."


class MistralDisabledError(TextExtractionError):
    status_code = status.HTTP_422_UNPROCESSABLE_CONTENT
    detail = "Mistral OCR is disabled. Enable USE_MISTRAL / mistral.enabled to process images or force OCR."


class OCRRateLimitError(TextExtractionError):
    status_code = status.HTTP_429_TOO_MANY_REQUESTS
    detail = "OCR request rate limit exceeded."


class MistralAPIError(TextExtractionError):
    status_code = status.HTTP_502_BAD_GATEWAY
    detail = "Failed to process OCR request via Mistral API."


class StorageConfigError(TextExtractionError):
    status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
    detail = "Storage is misconfigured."
