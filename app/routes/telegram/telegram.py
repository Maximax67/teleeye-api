from fastapi import APIRouter

from app.routes.telegram import bots, files, chats, users


router = APIRouter(prefix="/telegram")

router.include_router(chats.router)
router.include_router(files.router)
router.include_router(bots.router)
router.include_router(users.router)
