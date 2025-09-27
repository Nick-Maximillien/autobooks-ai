import os
import requests
from jose import jwt, JWTError, ExpiredSignatureError
import logging
from fastapi import HTTPException

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("autobooks.log", encoding="utf-8"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

BACKEND_API = os.getenv("BACKEND_API", "http://127.0.0.1:8000")
SECRET_KEY = "django-insecure-4y7^s$z!34z!-66i=i+ckyy6_n0&js*pf8+vtb+@76tf186+!m"
ALGORITHM = "HS256"


if not SECRET_KEY:
    raise RuntimeError("SECRET_KEY is missing or not loaded from .env")
logger.info(f"Loaded SECRET_KEY length: {len(SECRET_KEY)}")


# Token decoder

def decode_token(token: str, refresh_token: str = None):
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return {"username": payload.get("username"), "email": payload.get("email"), "user_id": payload.get("user_id")}
    except ExpiredSignatureError:
        if refresh_token:
            try:
                response = requests.post(
                    f"{BACKEND_API}/token/refresh/",
                    json={"refresh": refresh_token}
                )
                if response.status_code == 200:
                    new_access = response.json().get("access")
                    payload = jwt.decode(new_access, SECRET_KEY, algorithms=[ALGORITHM])
                    return {
                        "username": payload.get("username"),
                        "email": payload.get("email"),
                        "user_id": payload.get("user_id")
                    }
                else:
                    logger.error(f"❌ Refresh failed: {response.text}")
            except Exception as e:
                logger.error(f"❌ Refresh error: {e}")
        raise HTTPException(status_code=401, detail="Token has expired.")
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid token.")




       