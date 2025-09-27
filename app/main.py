import logging
import uuid
from pydantic import BaseModel
from pathlib import Path
from fastapi import FastAPI, UploadFile, File, HTTPException, Header, Form, Request
import requests
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import os


from app.ocr import extract_text
from app.parse import process_invoice, query_nlp
from app.utils import decode_token

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("autobooks.log", encoding="utf-8"),  # save logs to file
        logging.StreamHandler()  # also print to console
    ]
)
logger = logging.getLogger(__name__)

# Ensure receipts folder exists

app = FastAPI()

# Allow frontend origin
ALLOWED_ORIGINS = [origin.strip() for origin in os.getenv("ALLOWED_ORIGINS", "http://localhost:3000").split(",")]
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

BASE_DIR = Path(__file__).resolve().parent.parent
RECEIPTS_DIR = BASE_DIR / "receipts"
RECEIPTS_DIR.mkdir(exist_ok=True)


Backend_API = os.getenv("DJANGO_API", "http://localhost:8000")


class CopilotRequest(BaseModel):
    message: str


@app.post("/copilot")
async def copilot_endpoint(req: CopilotRequest, request: Request):
    logger.info("Received copilot request")
    logger.info(f"User message: {req.message}")
    auth_header = request.headers.get("Authorization")
    refresh_token = request.headers.get("X-Refresh-Token")
    if not auth_header:
        logger.warning("Missing Auth header")
        raise HTTPException(status_code=401, detail="Missing auth header")
    token = auth_header.split(" ")[1]
    logger.info("Auth header present, decoding token...")

    user = decode_token(token, refresh_token)
    logger.info(f"Decoded user: {user}")
    user_id = user.get("user_id")

    headers = {"Authorization": f"Bearer {token}"}

    try:
        balance = requests.get(f"{Backend_API}/balance-sheet/", headers=headers, timeout=5).json()
        logger.info(f"Balance Sheet: {balance}")
        profit_loss = requests.get(f"{Backend_API}/pnl/", headers=headers, timeout=5).json()
        logger.info(f"Profit & Loss: {profit_loss}")
        cashflow = requests.get(f"{Backend_API}/cashflow/", headers=headers, timeout=5).json()
        logger.info(f"Payroll: {cashflow}")
    except Exception as e:
        logger.error(f"Failed to fetch data from backend: {e}")
        raise HTTPException(status_code=502, detail=f"Django request failed: {e}")

    prompt = f"""
You are the business copilot for user {user.get('username')} ({user.get('email')}).
Use the user's financial data to answer questions and give advice.

Balance Sheet: {balance}
Profit and Loss: {profit_loss}
Cash Flow: {cashflow}



User Message: {req.message}
"""
    logger.info("Sending prompt to NLP...")

    response_text = query_nlp(prompt)

    logger.info("NLP responded successfully!")
    logger.info(f"NLP raw response: {response_text}")

    return {"reply": response_text}



@app.post("/upload")
async def upload_receipt(
    receipt: UploadFile = File(...), 
    authorization: str = Header(...), 
    x_refresh_token: str = Header(None), 
    user_id: str = Form(None)):
    """
    Upload a receipt -> Save -> OCR -> NLP parse -> save to ledger.db
    """
                # Save file
    try:
        logger.info("Upload request received")

        # Decode token
        token = authorization.replace("Bearer ", "").strip()
        if not token:
            raise HTTPException(status_code=401, detail="Missing or invalid token")
        identity = decode_token(token, x_refresh_token) or {}
        if not identity.get("user_id") and user_id:
            identity["user_id"] = user_id
        if not identity.get("user_id"):
            raise HTTPException(status_code=401, detail="User ID missing")
        logger.info(f"Authenticated user: {identity}")

        # Save file
        file_path = f"receipts/{receipt.filename}"
        with open(file_path, "wb") as f:
            f.write(await receipt.read())
        logger.info(f"Saved receipt to: {file_path}")

                # OCR 
        logger.info("Starting OCR...")
        text = extract_text(file_path)
        if not text.strip():
            logger.warning("OCR returned EMPTY text!")
        else:
            logger.info(f"OCR extracted text (first 200 chars): {text[:200]}...")
            
                # NLP + DB
        logger.info("🔍 Starting invoice parsing...")
        structured_data = process_invoice(text, token, identity)
        logger.info(f"Parsed invoice data: {structured_data}")


        return JSONResponse(
            content={
                "status": "success",
                "structured_data": structured_data,
                "file_path": str(file_path),
                "identity": identity,
            }
        )
    except Exception as e:
        logger.error(f"Error processing receipt: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

    
