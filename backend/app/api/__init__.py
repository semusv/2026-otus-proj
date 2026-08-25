"""Агрегатор API-роутеров."""

from fastapi import APIRouter

from app.api import auth, system

api_router = APIRouter()
api_router.include_router(system.router)
api_router.include_router(auth.router)
