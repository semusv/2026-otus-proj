"""Агрегатор API-роутеров."""

from fastapi import APIRouter

from app.api import acts, admin, auth, chat, system

api_router = APIRouter()
api_router.include_router(system.router)
api_router.include_router(auth.router)
api_router.include_router(admin.router)
api_router.include_router(chat.router)
api_router.include_router(acts.router)
