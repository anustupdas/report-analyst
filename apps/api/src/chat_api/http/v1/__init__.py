from fastapi import APIRouter

from chat_api.http.v1.routes import router as v1_routes
from chat_api.http.v1.vector_routes import router as vector_routes

api_v1_router = APIRouter(prefix="/api/v1")
api_v1_router.include_router(v1_routes)
api_v1_router.include_router(vector_routes)
