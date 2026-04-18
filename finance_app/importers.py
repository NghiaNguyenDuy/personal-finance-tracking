from __future__ import annotations

import hashlib
import io
import re
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

from .constants import (
    CATEGORY_MAP,
    DEFAULT_SETTINGS,
    HSBC_CREDIT_KEYWORDS,
    HSBC_FEE_KEYWORDS,
    HSBC_INSTALLMENT_KEYWORDS,
    HSBC_PAYMENT_KEYWORDS,
    MERCHANT_CATEGORY_HINTS,
    SOURCE_HSBC,
    SOURCE_TCB_IMAGE,
)

try:
    import pdfplumber
except ImportError:  # pragma: no cover - dependency is optional in tests
    pdfplumber = None

try:
    from PIL import Image, ImageEnhance, ImageFilter, ImageOps
except ImportError:  # pragma: no cover - pillow is expected but kept optional
    Image = None
    ImageEnhance = None
    ImageFilter = None
    ImageOps = None

try:
    import pytesseract
except ImportError:  # pragma: no cover - dependency is optional in tests
    pytesseract = None


HSBC_ROW_PATTERN = re.compile(
    r"^(?P<txn>\d{2}/\d{2})\s+(?P<post>\d{2}/\d{2})\s+(?P<desc>.+?)\s+(?P<amount>[\d,]+\.\d{2}(?:CR)?)$"
)
TIME_PATTERN = re.compile(r"(?P<time>\d{1,2}:\d{2})$")
DATE_PATTERN = re.compile(r"(?P<date>\d{2}/\d{2}/\d{4})")
SHORT_DATE_PATTERN = re.compile(r"(?P<day>\d{2})/(?P<month>\d{2})")
HEADER_MONTH_PATTERN = re.compile(r"(?P<day>\d{1,2})\s*THG\s*(?P<month>\d{1,2})[,]?\s*(?P<year>\d{4})")
ACCOUNT_PATTERN = re.compile(r"TAI\s*KHOAN\s*(?P<account>\d+)")
AMOUNT_PATTERN = re.compile(r"S[O06]\s*TI[E3]N\s*GD[:\s]*([+-])?\s*([\d\.,]+)")
BALANCE_PATTERN = re.compile(r"S[O06]\s*DU[:\s]*([\d\.,]+)")
HSBC_STATEMENT_DATE_PATTERN = re.compile(r"Ngày lập bảng\s*(\d{2}/\d{2}/\d{4})")
HSBC_DUE_DATE_PATTERN = re.compile(r"Vui lòng thanh toán trước\s*(\d{2}/\d{2}/\d{4})")
HSBC_BALANCE_PATTERN = re.compile(r"Dư nợ cuối kỳ VND\s*([\d,]+\.\d{2})")
HSBC_MIN_PAYMENT_PATTERN = re.compile(r"Thanh toán tối thiểu VND\s*([\d,]+\.\d{2})")


def normalize_spaces(value: str) -> str:
    return " ".join(str(value or "").split())


def strip_accents(value: str) -> str:
    return "".join(
        character
        for character in unicodedata.normalize("NFD", str(value or ""))
        if unicodedata.category(character) != "Mn"
    )


def clean_merchant_keyword(value: str) -> str:
    normalized = strip_accents(str(value or "")).upper()
    normalized = re.sub(r"[^A-Z0-9 ]+", " ", normalized)
    words = [word for word in normalized.split() if len(word) > 2 and word not in {"VNM", "VN", "CHI", "MINH"}]
    if not words:
        return ""
    return " ".join(words[:4])


def compute_file_hash(path: str | Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_vnd_number(value: str) -> float:
    cleaned = re.sub(r"[^\d]", "", str(value or ""))
    return float(cleaned or 0)


def fingerprint_row(parts: list[Any]) -> str:
    digest = hashlib.sha256()
    digest.update("|".join(str(part or "") for part in parts).encode("utf-8"))
    return digest.hexdigest()


def parse_amount(value: str) -> float:
    cleaned = str(value or "").replace(",", "").replace("CR", "").replace(" ", "")
    return abs(float(cleaned))


def amount_direction(value: str, default_negative: bool = False) -> str:
    text = str(value or "")
    if text.endswith("CR") or text.startswith("+"):
        return "inflow"
    if text.startswith("-"):
        return "outflow"
    return "outflow" if default_negative else "outflow"


def statement_month_from_filename(path: Path) -> str:
    match = re.search(r"(?P<year>20\d{2})(?P<month>\d{2})", path.stem)
    if not match:
        return ""
    return f"{match.group('year')}-{match.group('month')}"


def resolve_hsbc_date(mmdd: str, statement_date: str) -> str:
    statement_dt = datetime.strptime(statement_date, "%d/%m/%Y")
    match = SHORT_DATE_PATTERN.search(mmdd)
    if not match:
        return ""
    month = int(match.group("month"))
    year = statement_dt.year
    if month > statement_dt.month:
        year -= 1
    return f"{year:04d}-{month:02d}-{int(match.group('day')):02d}"


def classify_hsbc_row(description: str, direction: str) -> str:
    description_upper = strip_accents(description).upper()
    if description_upper.startswith("OTHER BANK CARDHOLDER PAYMENT") or description_upper.startswith("CARDHOLDER PAYMENT"):
        return "payment"
    if any(keyword in description_upper for keyword in HSBC_CREDIT_KEYWORDS):
        return "refund"
    if any(keyword in description_upper for keyword in HSBC_FEE_KEYWORDS):
        return "fee"
    if any(keyword in description_upper for keyword in HSBC_INSTALLMENT_KEYWORDS):
        return "installment"
    if direction == "inflow":
        return "refund"
    return "purchase"


def guess_category(description: str, source_type: str, row_type: str, direction: str) -> tuple[str, str]:
    merchant_key = clean_merchant_keyword(description)
    for keyword, category_pair in MERCHANT_CATEGORY_HINTS.items():
        if keyword in merchant_key:
            return category_pair

    if source_type == SOURCE_HSBC and row_type == "payment":
        return "Debt Payments", "Credit card debt"
    if source_type == SOURCE_HSBC and row_type == "refund":
        return "Others", "Other expense"
    if source_type == SOURCE_HSBC and row_type == "fee":
        return "Debt Payments", "Credit card debt"
    if source_type == SOURCE_TCB_IMAGE and direction == "inflow":
        if "LUONG" in merchant_key or "SALARY" in merchant_key:
            return "Income", "Salary"
        return "Others", "Other expense"
    return "Others", "Other expense"


def suggest_posting(
    *,
    source_type: str,
    description: str,
    amount: float,
    direction: str,
    row_type: str,
    merchant_rules: list[Any],
    settings: dict[str, str],
) -> dict[str, str]:
    description_upper = clean_merchant_keyword(description)
    for rule in merchant_rules:
        if rule["keyword"] and rule["keyword"] in description_upper:
            return {
                "category": rule["category"],
                "subcategory": rule["subcategory"],
                "debit_account": rule["debit_account"],
                "credit_account": rule["credit_account"],
            }

    category, subcategory = guess_category(description, source_type, row_type, direction)
    default_cash = settings.get("default_tcb_cash_account", DEFAULT_SETTINGS["default_tcb_cash_account"])
    default_tcb_offset = settings.get("default_tcb_offset_account", DEFAULT_SETTINGS["default_tcb_offset_account"])
    default_hsbc_liability = settings.get(
        "default_hsbc_liability_account",
        DEFAULT_SETTINGS["default_hsbc_liability_account"],
    )

    if source_type == SOURCE_TCB_IMAGE:
        if direction == "inflow":
            credit_account = "Income:Salary" if category == "Income" else "Equity:General"
            return {
                "category": category,
                "subcategory": subcategory if category != "Income" else CATEGORY_MAP["Income"][0],
                "debit_account": default_cash,
                "credit_account": credit_account,
            }
        return {
            "category": category,
            "subcategory": subcategory,
            "debit_account": default_tcb_offset,
            "credit_account": default_cash,
        }

    if row_type == "payment":
        return {
            "category": "Debt Payments",
            "subcategory": "Credit card debt",
            "debit_account": default_hsbc_liability,
            "credit_account": default_cash,
        }
    if row_type == "refund":
        return {
            "category": category,
            "subcategory": subcategory,
            "debit_account": default_hsbc_liability,
            "credit_account": "Expense",
        }
    return {
        "category": category,
        "subcategory": subcategory,
        "debit_account": "Expense",
        "credit_account": default_hsbc_liability,
    }


def _set_tesseract_cmd(tesseract_cmd: str) -> None:
    if pytesseract is not None and tesseract_cmd:
        pytesseract.pytesseract.tesseract_cmd = tesseract_cmd


def resolve_tesseract_cmd(tesseract_cmd: str = "") -> str:
    candidates = []
    if tesseract_cmd:
        candidates.append(Path(tesseract_cmd))
    candidates.extend(
        [
            Path(r"C:\Users\Admin\AppData\Local\Programs\Tesseract-OCR\tesseract.exe"),
            Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe"),
            Path(r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe"),
        ]
    )
    for candidate in candidates:
        if candidate and candidate.exists():
            return str(candidate)
    return tesseract_cmd


def is_tesseract_available(tesseract_cmd: str = "") -> bool:
    if pytesseract is None:
        return False
    try:
        _set_tesseract_cmd(resolve_tesseract_cmd(tesseract_cmd))
        pytesseract.get_tesseract_version()
        return True
    except Exception:
        return False


def dependency_summary(tesseract_cmd: str = "") -> dict[str, bool]:
    return {
        "pdfplumber": pdfplumber is not None,
        "pytesseract": pytesseract is not None,
        "tesseract_binary": is_tesseract_available(tesseract_cmd),
    }


def extract_image_text(path: str | Path, tesseract_cmd: str = "") -> str:
    if Image is None or pytesseract is None:
        return ""
    if not is_tesseract_available(tesseract_cmd):
        return ""

    _set_tesseract_cmd(resolve_tesseract_cmd(tesseract_cmd))
    image = Image.open(path)
    grayscale = ImageOps.grayscale(image)
    contrasted = ImageEnhance.Contrast(grayscale).enhance(2.0)
    sharpened = contrasted.filter(ImageFilter.SHARPEN)
    return pytesseract.image_to_string(sharpened, lang="eng")


def ocr_pdf_page(page, tesseract_cmd: str = "") -> str:
    if pdfplumber is None or Image is None or pytesseract is None:
        return ""
    if not is_tesseract_available(tesseract_cmd):
        return ""
    _set_tesseract_cmd(resolve_tesseract_cmd(tesseract_cmd))
    pdf_image = page.to_image(resolution=200)
    buffer = io.BytesIO()
    pdf_image.original.save(buffer, format="PNG")
    buffer.seek(0)
    image = Image.open(buffer)
    grayscale = ImageOps.grayscale(image)
    contrasted = ImageEnhance.Contrast(grayscale).enhance(2.0)
    return pytesseract.image_to_string(contrasted, lang="eng")


def parse_hsbc_pdf(path: str | Path, password: str, tesseract_cmd: str = "") -> tuple[dict[str, Any], list[dict[str, Any]]]:
    path = Path(path)
    metadata: dict[str, Any] = {
        "statement_month": statement_month_from_filename(path),
        "statement_date": "",
        "payment_due_date": "",
        "statement_balance": 0.0,
        "minimum_payment": 0.0,
        "page_count": 0,
    }
    rows: list[dict[str, Any]] = []
    if pdfplumber is None:
        metadata["parse_notes"] = "pdfplumber is not installed."
        return metadata, rows

    with pdfplumber.open(str(path), password=password or "") as pdf:
        metadata["page_count"] = len(pdf.pages)
        page_texts: list[str] = []
        extraction_engine = "pdfplumber"
        for page in pdf.pages:
            text = page.extract_text() or ""
            if len(normalize_spaces(text)) < 40:
                ocr_text = ocr_pdf_page(page, tesseract_cmd=tesseract_cmd)
                if ocr_text:
                    text = ocr_text
                    extraction_engine = "pdfplumber+tesseract"
            page_texts.append(text)

        full_text = "\n".join(page_texts)
        metadata["extraction_engine"] = extraction_engine

    statement_date_match = HSBC_STATEMENT_DATE_PATTERN.search(full_text)
    due_date_match = HSBC_DUE_DATE_PATTERN.search(full_text)
    balance_match = HSBC_BALANCE_PATTERN.search(full_text)
    minimum_match = HSBC_MIN_PAYMENT_PATTERN.search(full_text)

    if statement_date_match:
        metadata["statement_date"] = statement_date_match.group(1)
        metadata["statement_month"] = datetime.strptime(metadata["statement_date"], "%d/%m/%Y").strftime("%Y-%m")
    if due_date_match:
        metadata["payment_due_date"] = due_date_match.group(1)
    if balance_match:
        metadata["statement_balance"] = parse_amount(balance_match.group(1))
    if minimum_match:
        metadata["minimum_payment"] = parse_amount(minimum_match.group(1))

    row_index = 0
    for raw_line in full_text.splitlines():
        line = normalize_spaces(raw_line)
        match = HSBC_ROW_PATTERN.match(line)
        if not match:
            continue
        if not metadata["statement_date"]:
            continue

        row_index += 1
        raw_amount = match.group("amount")
        direction = amount_direction(raw_amount)
        row_type = classify_hsbc_row(match.group("desc"), direction)
        rows.append(
            {
                "source_type": SOURCE_HSBC,
                "row_index": row_index,
                "statement_month": metadata["statement_month"],
                "transaction_date": resolve_hsbc_date(match.group("txn"), metadata["statement_date"]),
                "post_date": resolve_hsbc_date(match.group("post"), metadata["statement_date"]),
                "event_time": "",
                "description": match.group("desc").strip(),
                "merchant": match.group("desc").strip(),
                "amount": parse_amount(raw_amount),
                "currency": "VND",
                "direction": direction,
                "running_balance": None,
                "account_ref": "",
                "row_type": row_type,
                "confidence": 0.97,
                "parse_notes": "",
                "raw_text": line,
                "row_fingerprint": fingerprint_row(
                    [
                        metadata["statement_month"],
                        match.group("txn"),
                        match.group("post"),
                        match.group("desc"),
                        raw_amount,
                    ]
                ),
            }
        )

    if not rows:
        metadata["parse_notes"] = "No transaction rows matched the HSBC parser pattern."

    return metadata, rows


def parse_tcb_ocr_text(text: str, *, image_name: str = "") -> tuple[dict[str, Any], list[dict[str, Any]]]:
    metadata: dict[str, Any] = {
        "statement_month": "",
        "statement_date": "",
        "page_count": 1,
        "image_name": image_name,
    }
    rows: list[dict[str, Any]] = []
    if not text.strip():
        metadata["parse_notes"] = "OCR returned no text."
        return metadata, rows

    lines = [normalize_spaces(line) for line in text.splitlines() if normalize_spaces(line)]
    normalized_lines = [strip_accents(line).upper() for line in lines]

    current_header_date = ""
    blocks: list[dict[str, Any]] = []
    current_block: dict[str, Any] | None = None
    orphan_times: list[str] = []

    for original_line, normalized_line in zip(lines, normalized_lines):
        header_match = HEADER_MONTH_PATTERN.search(normalized_line)
        if header_match:
            current_header_date = (
                f"{int(header_match.group('year')):04d}-{int(header_match.group('month')):02d}-{int(header_match.group('day')):02d}"
            )
            continue

        account_match = ACCOUNT_PATTERN.search(normalized_line)
        if account_match:
            if current_block:
                blocks.append(current_block)
            current_block = {
                "account_ref": account_match.group("account"),
                "header_date": current_header_date,
                "raw_lines": [original_line],
                "normalized_lines": [normalized_line],
            }
            continue

        time_only_match = TIME_PATTERN.fullmatch(normalized_line)
        if time_only_match:
            orphan_times.append(time_only_match.group("time"))
            continue

        if "SAO CHEP" in normalized_line:
            continue

        if current_block is None:
            continue

        current_block["raw_lines"].append(original_line)
        current_block["normalized_lines"].append(normalized_line)

    if current_block:
        blocks.append(current_block)

    if len(orphan_times) > len(blocks):
        orphan_times = orphan_times[-len(blocks) :]

    for block, orphan_time in zip(blocks, orphan_times):
        block["event_time"] = orphan_time

    row_index = 0
    last_event_date = ""
    for block in blocks:
        account_ref = block["account_ref"]
        block_text = "\n".join(block["raw_lines"])
        normalized_text = "\n".join(block["normalized_lines"])

        amount_match = AMOUNT_PATTERN.search(normalized_text)
        balance_match = BALANCE_PATTERN.search(normalized_text)
        time_match = TIME_PATTERN.search(normalized_text)
        explicit_date = ""
        description_lines: list[str] = []

        for raw_line, normalized_line in zip(block["raw_lines"], block["normalized_lines"]):
            if "NGAY " in normalized_line:
                date_match = DATE_PATTERN.search(normalized_line)
                if date_match:
                    explicit_date = datetime.strptime(date_match.group("date"), "%d/%m/%Y").strftime("%Y-%m-%d")
                continue
            if TIME_PATTERN.fullmatch(normalized_line):
                time_match = TIME_PATTERN.fullmatch(normalized_line)
                continue
            if "TAI KHOAN" in normalized_line or AMOUNT_PATTERN.search(normalized_line) or BALANCE_PATTERN.search(normalized_line):
                continue
            description_lines.append(raw_line)

        if not amount_match:
            continue

        row_index += 1
        sign = amount_match.group(1) or "+"
        amount_value = parse_vnd_number(amount_match.group(2))
        direction = "outflow" if sign == "-" else "inflow"
        running_balance = None
        if balance_match:
            running_balance = parse_vnd_number(balance_match.group(1))

        event_date = explicit_date or block.get("header_date") or last_event_date
        if event_date and not metadata["statement_date"]:
            metadata["statement_date"] = event_date
            metadata["statement_month"] = event_date[:7]
        if event_date:
            last_event_date = event_date

        description = normalize_spaces(" ".join(description_lines)) or f"TCB notification {row_index}"
        event_time = block.get("event_time") or (time_match.group("time") if time_match else "")
        rows.append(
            {
                "source_type": SOURCE_TCB_IMAGE,
                "row_index": row_index,
                "statement_month": event_date[:7] if event_date else metadata["statement_month"],
                "transaction_date": event_date,
                "post_date": event_date,
                "event_time": event_time,
                "description": description,
                "merchant": description,
                "amount": amount_value,
                "currency": "VND",
                "direction": direction,
                "running_balance": running_balance,
                "account_ref": account_ref,
                "row_type": "outflow" if direction == "outflow" else "inflow",
                "confidence": sum(
                    [
                        1 if amount_match else 0,
                        1 if event_date else 0,
                        1 if event_time else 0,
                        1 if account_ref else 0,
                        1 if description_lines else 0,
                    ]
                )
                / 5.0,
                "parse_notes": "" if event_date else "Event date was inferred from header or missing.",
                "raw_text": block_text,
                "row_fingerprint": fingerprint_row(
                    [image_name, account_ref, event_date, time_match.group("time") if time_match else "", description, amount_value]
                ),
            }
        )

    if not rows:
        metadata["parse_notes"] = "No transaction cards were parsed from OCR text."
    return metadata, rows


def parse_tcb_image(path: str | Path, tesseract_cmd: str = "") -> tuple[dict[str, Any], list[dict[str, Any]]]:
    path = Path(path)
    metadata = {
        "statement_month": "",
        "statement_date": "",
        "page_count": 1,
        "image_name": path.name,
    }
    if Image is None or pytesseract is None:
        metadata["parse_notes"] = "Pillow or pytesseract is not installed."
        return metadata, []
    resolved_tesseract = resolve_tesseract_cmd(tesseract_cmd)
    if not is_tesseract_available(resolved_tesseract):
        metadata["parse_notes"] = "Tesseract binary is not configured. Set tesseract_cmd in Settings to enable OCR."
        return metadata, []

    text = extract_image_text(path, tesseract_cmd=resolved_tesseract)
    parsed_metadata, rows = parse_tcb_ocr_text(text, image_name=path.name)
    metadata.update(parsed_metadata)
    return metadata, rows


def scan_sources(repository, force_reprocess: bool = False) -> dict[str, Any]:
    settings = repository.get_settings()
    batch_id = repository.create_import_batch()
    merchant_rules = repository.get_merchant_rules()
    summary = {
        "batch_id": batch_id,
        "processed": 0,
        "skipped": 0,
        "rows": 0,
        "errors": [],
    }
    tesseract_cmd = resolve_tesseract_cmd(settings.get("tesseract_cmd", ""))

    sources = {
        SOURCE_HSBC: Path(settings["hsbc_folder"]),
        SOURCE_TCB_IMAGE: Path(settings["tcb_image_folder"]),
    }

    for source_type, folder in sources.items():
        if not folder.exists():
            summary["errors"].append(f"Missing folder: {folder}")
            continue

        pattern = "*.pdf" if source_type == SOURCE_HSBC else "*.*"
        for path in sorted(folder.glob(pattern)):
            if path.is_dir():
                continue
            if source_type == SOURCE_TCB_IMAGE and path.suffix.lower() not in {".png", ".jpg", ".jpeg"}:
                continue

            file_hash = compute_file_hash(path)
            existing = repository.get_source_file_by_hash(source_type, file_hash)
            if existing and not force_reprocess:
                summary["skipped"] += 1
                continue

            try:
                if source_type == SOURCE_HSBC:
                    metadata, rows = parse_hsbc_pdf(path, settings.get("hsbc_password", ""), tesseract_cmd=tesseract_cmd)
                else:
                    metadata, rows = parse_tcb_image(path, tesseract_cmd=tesseract_cmd)
            except Exception as exc:  # pragma: no cover - defensive UI path
                metadata = {"statement_month": statement_month_from_filename(path), "parse_notes": str(exc)}
                rows = []

            for row in rows:
                suggestions = suggest_posting(
                    source_type=row["source_type"],
                    description=row["description"],
                    amount=row["amount"],
                    direction=row["direction"],
                    row_type=row["row_type"],
                    merchant_rules=merchant_rules,
                    settings=settings,
                )
                row.update(suggestions)

            parse_status = "parsed" if rows else "warning"
            parse_notes = metadata.get("parse_notes", "")
            if source_type == SOURCE_TCB_IMAGE and not dependency_summary(tesseract_cmd)["tesseract_binary"]:
                parse_status = "dependency_missing"

            source_file_id = repository.upsert_source_file(
                batch_id=batch_id,
                source_type=source_type,
                file_name=path.name,
                file_path=str(path),
                file_hash=file_hash,
                statement_month=metadata.get("statement_month", "") or statement_month_from_filename(path),
                parse_status=parse_status,
                parse_notes=parse_notes,
                extraction_engine=metadata.get("extraction_engine", ""),
                raw_metadata=metadata,
            )
            repository.replace_statement_rows(source_file_id, rows)
            summary["processed"] += 1
            summary["rows"] += len(rows)

    repository.finalize_import_batch(
        batch_id,
        status="completed" if not summary["errors"] else "completed_with_warnings",
        notes="; ".join(summary["errors"]),
    )
    return summary
