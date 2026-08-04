from fastapi import APIRouter, Depends, Request

from backend.services.auth_service import get_current_user

router = APIRouter(tags=["Meta Debug"])


@router.get("/debug/meta")
def debug_meta_get(current_user: dict = Depends(get_current_user)):
    print("DEBUG META GET RECEIVED")
    return {
        "status": "ok",
        "method": "GET",
        "message": "Meta debug endpoint is working",
    }


@router.post("/debug/meta")
async def debug_meta_post(request: Request, current_user: dict = Depends(get_current_user)):
    payload = await request.json()
    print("DEBUG META POST RECEIVED")
    print(payload)

    return {
        "status": "ok",
        "method": "POST",
        "payload": payload,
    }
