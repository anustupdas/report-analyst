from fastapi import status


class EmbeddingServiceError(Exception):
    status_code: int = status.HTTP_500_INTERNAL_SERVER_ERROR
    detail: str = "Embedding failed."

    def __init__(self, detail: str | None = None) -> None:
        self.detail = detail or self.__class__.detail
        super().__init__(self.detail)


class ConfigurationFileNotFoundError(EmbeddingServiceError):
    status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
    detail = "Configuration file not found."


class InvalidRequestInputError(EmbeddingServiceError):
    status_code = status.HTTP_400_BAD_REQUEST
    detail = "Input text is empty or invalid."


class ModelNotConfiguredError(EmbeddingServiceError):
    status_code = status.HTTP_404_NOT_FOUND
    detail = "Requested embedding model is not configured."


class ModelNotReadyError(EmbeddingServiceError):
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    detail = "Embedding model is not loaded."


class EmbeddingComputationError(EmbeddingServiceError):
    status_code = status.HTTP_422_UNPROCESSABLE_CONTENT
    detail = "Failed to compute embeddings."
