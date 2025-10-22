import json
import logging
import re
import requests
import os
import time
from dotenv import load_dotenv
from pathlib import Path

# ✅ Load .env
load_dotenv()

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
# Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("autobooks.log", encoding="utf-8"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# External Servers
NLP_SERVER = os.getenv("NLP_SERVER", "http://127.0.0.1:8002/generate")
BACKEND_SERVER = os.getenv("BACKEND_SERVER", "http://127.0.0.1:8000/api/documents/")


def parse_with_nlp(text: str) -> dict:
    """
    Calls Gemini/Vertex first, then NLP server to extract structured document fields
    matching the Django Document model.
    """
    prompt = f"""Extract all the following fields from this business document OCR text if available.
Financial numbers MUST NOT contain commas. Add .00 if integer to match DecimalField format.
Any field related to total, payment, loan amount, equity, amount paid, balance, or total payroll 
should be stored in the 'total' field.
DO NOT MAKE UP your own document_type. Only these are valid document types: 
(invoice, receipt, bill, quotation, payroll, delivery_note, credit_note, debit_note, 
asset_purchase, bank_statement, short_term_borrowing, long_term_borrowing, tax_filing, 
equity_injection, purchase_order, expense_claim, etc., lowercase).

Document OCR text:
{text}"""

    # --- Gemini / Vertex ---
    try:
        from vertexai import init as vertex_init
        from vertexai.generative_models import GenerativeModel, GenerationConfig, SafetySetting

        vertex_init(project=GCP_PROJECT_ID, location="us-central1")
        gemini_model = GenerativeModel("gemini-2.5-flash")

        # ✅ Valid safety categories only
        safety_settings = [
            SafetySetting(category="HARM_CATEGORY_DANGEROUS_CONTENT", threshold="BLOCK_LOW_AND_ABOVE"),
            SafetySetting(category="HARM_CATEGORY_HARASSMENT", threshold="BLOCK_LOW_AND_ABOVE"),
            SafetySetting(category="HARM_CATEGORY_SEXUALLY_EXPLICIT", threshold="BLOCK_LOW_AND_ABOVE"),
            SafetySetting(category="HARM_CATEGORY_HATE_SPEECH", threshold="BLOCK_LOW_AND_ABOVE"),
        ]

        gen_cfg = GenerationConfig(temperature=0.2, top_p=0.9, max_output_tokens=1024)

        start_time = time.time()
        response = gemini_model.generate_content(
            [prompt],
            generation_config=gen_cfg,
            safety_settings=safety_settings
        )
        latency = time.time() - start_time

        gemini_text = getattr(response, "text", "").strip()
        if gemini_text:
            logger.info(f"✅ Gemini insight generated in {latency:.2f}s")
            match = re.search(r"\{.*\}", gemini_text, re.DOTALL)
            if match:
                try:
                    structured = json.loads(match.group())
                    logger.info(f"Structured data from Gemini: {structured}")
                    return structured
                except json.JSONDecodeError:
                    logger.warning("Could not decode JSON from Gemini output.")
    except Exception as e:
        logger.warning(f"❌ Gemini failed: {e}")

    # --- NLP Server Fallback ---
    try:
        payload = {
            "model": "llama3",
            "prompt": prompt,
        }
        response = requests.post(NLP_SERVER, json=payload, timeout=3000, stream=True)
        response.raise_for_status()
        result = response.json()
        llm_text = result.get("response", "").strip()

        match = re.search(r"\{.*\}", llm_text, re.DOTALL)
        if match:
            try:
                structured = json.loads(match.group())
                logger.info(f"Structured data from NLP server: {structured}")
                return structured
            except json.JSONDecodeError:
                logger.warning("Could not decode JSON from NLP output.")
                return {"raw_text": llm_text, "document_type": "unknown"}

        return {"raw_text": llm_text, "document_type": "unknown"}

    except Exception as e:
        logger.error(f"❌ NLP Server failed: {e}")
        return {"raw_text": text, "document_type": "unknown"}


def save_to_db(data: dict, raw_text: str, token: str, identity: dict):
    payload = {
        "business_name": data.get("business_name"),
        "invoice_number": data.get("invoice_number"),
        "vendor": data.get("vendor"),
        "bill_number": data.get("bill_number"),
        "date": data.get("date"),
        "tax": data.get("tax"),
        "total": data.get("total"),
        "customer": data.get("customer"),
        "items": data.get("items", []),
        "receipt_number": data.get("receipt_number"),
        "amount_paid": data.get("amount_paid"),
        "payment_method": data.get("payment_method"),
        "balance": data.get("balance"),
        "billed_to": data.get("billed_to"),
        "quotation_number": data.get("quotation_number"),
        "issued_to": data.get("issued_to"),
        "payroll_month": data.get("payroll_month"),
        "employee_salaries": data.get("employee_salaries"),
        "delivery_date": data.get("delivery_date"),
        "delivery_note_number": data.get("delivery_note_number"),
        "delivered_to": data.get("delivered_to"),
        "credit_note_number": data.get("credit_note_number"),
        "debit_note_number": data.get("debit_note_number"),
        "asset_value": data.get("asset_value"),
        "asset_description": data.get("asset_description"),
        "loan_lender": data.get("loan_lender"),
        "loan_terms": data.get("loan_terms"),
        "equity_investor": data.get("equity_investor"),
        "equity_terms": data.get("equity_terms"),
        "received_by": data.get("received_by"),
        "document_type": data.get("document_type", "unknown"),
        "raw_text": raw_text,
        "user_id": identity.get("user_id"),
        "business": identity.get("user_id")
    }

    if identity:
        payload["uploaded_by"] = identity.get("username") or identity.get("email")

    headers = {"Content-Type": "application/json"}
    if not token:
        headers["Authorization"] = f"Bearer {token}"

    try:
        response = requests.post(BACKEND_SERVER, json=payload, headers=headers, timeout=3050)
        if response.status_code >= 400:
            logger.error(f"Django rejected document (status {response.status_code}): {response.text}")
        else:
            logger.info(f"Document saved to backend (status {response.status_code}): {response.text}")
        response.raise_for_status()
        logger.info("Stored parsed document to Django successfully")
        return response.json()
    except Exception as e:
        logger.error(f"Failed to save document to Django: {e}")
        raise


def process_invoice(text: str, token: str, identity: dict):
    structured_data = parse_with_nlp(text)
    logger.info("Saving to Django ERP...")
    save_to_db(structured_data, text, token, identity)
    logger.info("Document data saved to Django ERP: {json.dumps(payload, indent=2)}")
    return structured_data


def query_nlp(prompt: str, model: str = "llama3") -> str:
    try:
        payload = {
            "model": model,
            "prompt": prompt,
            "max_tokens": 512,
            "temperature": 0.2
        }
        response = requests.post(NLP_SERVER, json=payload, timeout=3000, stream=True)
        response.raise_for_status()
        result = response.json()
        llm_text = result.get("response", "").strip()
        return llm_text
    except Exception as e:
        logger.error(f"❌ Ollama query failed: {e}")
        return f"Ollama query failed: {e}"
