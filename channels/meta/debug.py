from fastapi import APIRouter, Request

router = APIRouter(tags=["Meta Debug"])


@router.get("/debug/meta")
def debug_meta_get():
    print("DEBUG META GET RECEIVED")
    return {
        "status": "ok",
        "method": "GET",
        "message": "Meta debug endpoint is working",
    }


@router.post("/debug/meta")
async def debug_meta_post(request: Request):
    payload = await request.json()
    print("DEBUG META POST RECEIVED")
    print(payload)

    return {
        "status": "ok",
        "method": "POST",
        "payload": payload,
    }