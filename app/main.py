import logging
import uuid
import os
import requests
import time
from pathlib import Path
from pydantic import BaseModel
from fastapi import FastAPI, UploadFile, File, HTTPException, Header, Form, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware

from app.ocr import extract_text
from app.parse import process_invoice, query_nlp
from app.utils import decode_token

# ✅ Vertex AI imports
from dotenv import load_dotenv
load_dotenv()
from vertexai import init as vertex_init
from vertexai.generative_models import GenerativeModel, GenerationConfig

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

# FastAPI init
app = FastAPI()


# Get allowed origins safely
ALLOWED_ORIGINS = os.getenv("ALLOWED_ORIGINS", "")
ALLOWED_ORIGINS = [origin.strip() for origin in ALLOWED_ORIGINS.split(",") if origin]

if not ALLOWED_ORIGINS:
    logger.warning("⚠️ ALLOWED_ORIGINS is empty! No frontend will be able to access the backend.")
else:
    logger.info(f"✅ CORS allowed origins: {ALLOWED_ORIGINS}")

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,       # Allow only specified origins
    allow_credentials=True,              # Required if frontend sends cookies or auth headers
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],  # Explicitly allow methods
    allow_headers=["*"],                  # Allow all headers
    expose_headers=["*"],                 # Optional: expose headers to frontend
)


BASE_DIR = Path(__file__).resolve().parent.parent
RECEIPTS_DIR = BASE_DIR / "receipts"
RECEIPTS_DIR.mkdir(exist_ok=True)



Backend_API = os.getenv("DJANGO_API", "http://localhost:8000")
GCP_PROJECT_ID = os.getenv("GCP_PROJECT_ID")
GOOGLE_APPLICATION_CREDENTIALS = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")

# ✅ Check Render-mounted secret
sa_path = Path("/etc/secrets/gcp_sa.json")
if sa_path.exists():
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = str(sa_path)
    GOOGLE_APPLICATION_CREDENTIALS = str(sa_path)

# ✅ Normalize Windows path if running locally
if GOOGLE_APPLICATION_CREDENTIALS and "\\" in GOOGLE_APPLICATION_CREDENTIALS:
    GOOGLE_APPLICATION_CREDENTIALS = GOOGLE_APPLICATION_CREDENTIALS.replace("\\", "/")
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = GOOGLE_APPLICATION_CREDENTIALS

# ✅ Initialize Vertex AI
if GCP_PROJECT_ID:
    try:
        import vertexai
        vertexai.init(project=GCP_PROJECT_ID, location="us-central1")
        print(f"✅ Vertex AI initialized for project {GCP_PROJECT_ID}")
    except Exception as init_err:
        print(f"⚠️ Vertex AI init failed: {init_err}")

# Gemini model configuration
gemini_model = GenerativeModel("gemini-2.5-flash")
gen_cfg = GenerationConfig(
    temperature=0.3,
    top_p=0.9,
    max_output_tokens=600
)


class CopilotRequest(BaseModel):
    message: str


def sanitize_prompt(prompt: str) -> str:
    """
    Reduce chance of Gemini blocking structured data by summarizing long numeric blobs.
    """
    if len(prompt) > 7000:
        prompt = prompt[:7000] + "\n\n[...truncated large data...]"
    # Replace massive JSON or repetitive numeric sequences
    prompt = prompt.replace("{", "\n{").replace("}", "}\n")
    return prompt


def query_gemini_direct(prompt: str) -> str:
    """
    Query Gemini Vertex AI directly (separate from parser.py).
    Includes a safe fallback if content is blocked.
    """
    try:
        prompt = sanitize_prompt(prompt)
        start_time = time.time()
        response = gemini_model.generate_content([prompt], generation_config=gen_cfg)
        latency = time.time() - start_time

        try:
            gemini_text = response.text.strip()
        except Exception as inner:
            logger.warning(f"⚠️ Gemini returned no text, likely blocked. Retrying with summary prompt. ({inner})")
            short_prompt = (
                "Summarize and analyze this user's financial report briefly in plain English. "
                "Skip numeric tables if too long.\n\n" + prompt[:4000]
            )
            response = gemini_model.generate_content([short_prompt], generation_config=gen_cfg)
            gemini_text = getattr(response, "text", "(empty Gemini response)").strip()

        logger.info(f"✅ Gemini responded in {latency:.2f}s")
        return gemini_text or "(empty Gemini response)"
    except Exception as e:
        logger.error(f"❌ Gemini query failed: {e}", exc_info=True)
        return f"Gemini query failed: {e}"


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

    logger.info("Sending prompt to Gemini Vertex AI...")
    response_text = query_gemini_direct(prompt)
    logger.info(f"Gemini response: {response_text}")

    return {"reply": response_text}


@app.post("/upload")
async def upload_receipt(
    receipt: UploadFile = File(...),
    authorization: str = Header(...),
    x_refresh_token: str = Header(None),
    user_id: str = Form(None)
):
    """
    Upload a receipt -> Save -> OCR -> NLP parse -> save to ledger.db
    """
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
