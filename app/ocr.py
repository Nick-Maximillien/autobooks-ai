import logging
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
EASYOCR_REPO = ROOT / "easyocr"   
sys.path.insert(0, str(EASYOCR_REPO))

import easyocr

print("EasyOCR loaded from:", easyocr.__file__)

WEIGHTS_DIR = ROOT / "weights"

reader = easyocr.Reader(
    ['en'],
    gpu=False,
    model_storage_directory=WEIGHTS_DIR,
    download_enabled=False,
    detect_network="craft",
    recog_network="english_g2",
    detector=True,
    recognizer=True,
    verbose=True,
    cudnn_benchmark=False,
    quantize=False
)

def extract_text(file_path: str):
    try:
        logging.info(f"OCR: starting on {file_path}")
        logging.info(f"OCR using weights dir: {WEIGHTS_DIR}")
        results = reader.readtext(file_path, detail=1)
        logging.info(f"OCR results: {results}")
        text = "\n".join([r[1] for r in results])
        return text
    except Exception as e:
        logging.error(f"OCR failed: {e}", exc_info=True)
        return ""
