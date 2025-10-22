import json
import logging
import re
import requests
import os



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

# External Servers
NLP_SERVER = os.getenv("NLP_SERVER", "http://127.0.0.1:8002/generate")
BACKEND_SERVER = os.getenv("BACKEND_SERVER", "http://127.0.0.1:8000/api/documents/")


def parse_with_nlp(text: str) -> dict:
    """
    Calls NLP server to extract structured document fields
    matching the Django Document model.
    """
    payload = {
        "model": "llama3",
        "prompt": f"""
        Extract all the following fields from this business document OCR text if available.
        Financial numbers MUST NOT contain commas. Add .00 if integer to match DecimalField format.
        Any field related to total, payment, loan amount, equity, amount paid, balance, or total payroll 
        should be stored in the 'total' field.
        DO NOT MAKE UP your own document_type. Only this are valid document types: (invoice, receipt, bill, quotation, payroll, delivery_note, credit_note, debit_note, asset_purchase, bank_statement, short_term_borrowing (if period is less than 1 year), long_term_borrowing (if period is longer than 1 year), tax_filing, equity_injection, purchase_order, expense_claim, etc., lowercase). No capital letters allowed in document_type.
        
        FIELDS TO EXTRACT:

        # common
        - business_name
        - document_type (invoice, receipt, bill, quotation, payroll, delivery_note, credit_note, debit_note, asset_purchase, bank_statement, short_term_borrowing (if period is less than 1 year), long_term_borrowing (if period is longer than 1 year), tax_filing, equity_injection, purchase_order, expense_claim, etc., lowercase). No capital letters allowed in document_type.
        - invoice_number
        - vendor
        - date
        - total 
        - raw_text (verbatim OCR text)
        - items (JSON list)

        # Invoice 
        - invoice_number      
        - customer
        - tax

        # Receipt
        - receipt_number
        - payment_from
        - payment_method
        - balance

        # Bill
        - bill_number
        - billed_to

        # Quotation
        - quotation_number
        - issued_to

        # Payroll
        - payroll_month
        - employee_salaries (list of objects with name and salary)

        # Delivery Note
        - delivery_note_number
        - delivery_date
        - delivered_to
        - received_by

        # Credit / Debit Notes
        - credit_note_number
        - debit_note_number

        # Asset Purchase
        - asset_value
        - asset_description

        # Loans
        - loan_lender
        - loan_terms

        # Equity Injection
        - equity_investor
        - equity_terms

        Respond in **valid JSON only**.
        Remove all commas in numbers. Only plain numbers are allowed. Add .00 to numbers to match DecimalField database.
        Document OCR text:
        {text}
        """,
    }

    response = requests.post(NLP_SERVER, json=payload, timeout=3000, stream=True)
    response.raise_for_status()
    result = response.json()

    llm_text = result.get("response", "").strip()
    logger.info(f"LLM raw result: {llm_text}")

    match = re.search(r"\{.*\}", llm_text, re.DOTALL)
    if match:
        try:
            structured = json.loads(match.group())
            logger.info(f"Structured data: {structured}")
            return structured
        except json.JSONDecodeError:
            logger.warning("Could not decode JSON from NLP output.")
            return {"raw_text": llm_text, "document_type": "unknown"}

    return {"raw_text": llm_text, "document_type": "unknown"}


def save_to_db(data: dict, raw_text: str, 
               token: str,
               identity: dict):
    """
    Send structured data to Backend.
    Missing fields are allowed (null in Django).
    """

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
            logger.error(
                f"Django rejected document (status {response.status_code}): {response.text}"
            )
        else:
            logger.info(
                f"Document saved to backend (status {response.status_code}): {response.text}"
            )
        response.raise_for_status()
        logger.info("Stored parsed document to Django successfully")
        return response.json()
    except Exception as e:
        logger.error(f"Failed to save document to Django: {e}")
        raise




def process_invoice(text: str, token: str, identity: dict):
    """Main entrypoint after OCR extraction"""
    structured_data = parse_with_nlp(text)
    logger.info("Saving to Django ERP...")
    save_to_db(structured_data, text, token, identity)
    logger.info("Document data saved to Django ERP: {json.dumps(payload, indent=2)}")
    return structured_data


def query_nlp(prompt: str, model: str = "llama3") -> str:
    """
    Query local Ollama API with prompt, return completion.
    """
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
