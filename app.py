#!/usr/bin/env python3
from __future__ import annotations

import csv
import base64
import io
import json
import mimetypes
import re
import shutil
import subprocess
import sys
import tempfile
import unicodedata
import zipfile
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta
from email.parser import BytesParser
from email.policy import default as default_policy
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Iterable
from urllib.parse import urlparse
import xml.etree.ElementTree as ET

try:
    import webview  # type: ignore
except ImportError:
    webview = None


ROOT = Path(__file__).resolve().parent
STATIC_DIR = ROOT / "static"
INDEX_FILE = ROOT / "index.html"
OCR_SCRIPT = ROOT / "scripts" / "ocr.swift"
DATA_DIR = ROOT / "data"
OVERRIDES_FILE = DATA_DIR / "overrides.json"
HOST = "127.0.0.1"
PORT = 8765
TESSERACT_BIN = "tesseract"
REFERENCE_FILE_CANDIDATES = [
    Path("/Users/lmv/Downloads/picpay-cartao-credito-atual.xlsx"),
]

DATE_PATTERNS = [
    "%d/%m/%Y",
    "%d/%m/%y",
    "%d-%m-%Y",
    "%d-%m-%y",
    "%Y-%m-%d",
    "%Y/%m/%d",
    "%d.%m.%Y",
    "%d.%m.%y",
]

AMOUNT_RE = re.compile(r"[-+]?\s*(?:R\$\s*)?(?:\d{1,3}(?:[.\s]\d{3})+|\d+)(?:[.,]\d{2})")
DATE_RE = re.compile(r"\b\d{2}[\/\-.]\d{2}(?:[\/\-.]\d{2,4})?\b")
TIME_RE = re.compile(r"\b\d{1,2}(?:h|:)\d{2}\b", re.IGNORECASE)
IGNORE_TOKENS = {
    "saldo",
    "saldo anterior",
    "saldo final",
    "saldo disponivel",
    "saldo disponível",
    "total",
    "pagamento efetuado",
    "pagamento recebido",
}
SCREEN_IGNORE_TOKENS = {
    "movimentacoes",
    "movimentações",
    "buscar",
    "credito",
    "crédito",
    "debito",
    "débito",
    "ver minhas faturas",
    "saldo em conta",
    "recentes",
    "futuras",
    "hoje",
    "ontem",
}
GENERIC_OPERATION_TOKENS = {
    "compra a vista",
    "compra à vista",
    "compra realizada",
    "pix enviado",
    "com saldo",
}


@dataclass
class ParsedRecord:
    source_file: str
    source_type: str
    description: str
    amount: str
    due_date: str
    category: str = ""
    subcategory: str = ""
    account: str = ""
    card: str = ""
    observations: str = ""
    launch_date: str = ""
    raw_text: str = ""
    confidence: float = 0.0


def titleize_candidate(value: str) -> str:
    text = re.sub(r"[_\-]+", " ", value).strip()
    return " ".join(part.capitalize() for part in text.split())


def normalize_text(value: str) -> str:
    value = value.replace("\xa0", " ").strip()
    if not value:
        return ""
    normalized = unicodedata.normalize("NFKD", value)
    return "".join(ch for ch in normalized if not unicodedata.combining(ch)).lower().strip()


def repair_text(value: str) -> str:
    text = value.replace("\xa0", " ").strip()
    if not text:
        return ""
    suspicious = ("Ã", "Â", "√", "∫", "ß", "™", "©")
    if any(token in text for token in suspicious):
        try:
            repaired = text.encode("mac_roman", errors="ignore").decode("utf-8", errors="ignore").strip()
            if repaired:
                return repaired
        except UnicodeError:
            return text
    return text


def clean_description(value: str) -> str:
    value = repair_text(value)
    value = re.sub(r"\s+", " ", value).strip(" -\t")
    value = re.sub(r"^[\W_]+", "", value)
    return value


def parse_amount(value: str) -> str:
    normalized = value.strip().upper().replace("R$", "").replace("RS", "").replace(" ", "")
    if not normalized:
        return ""
    match = re.search(r"[-+]?(?:\d{1,3}(?:[.\s]\d{3})+|\d+)(?:[.,]\d{2})", normalized)
    if not match:
        return ""
    raw = match.group(0)
    sign = "-"
    if raw.startswith("+"):
        sign = ""
        raw = raw[1:]
    elif raw.startswith("-"):
        raw = raw[1:]
    else:
        sign = ""
    if "," in raw and "." in raw:
        raw = raw.replace(".", "").replace(",", ".")
    elif "," in raw:
        raw = raw.replace(",", ".")
    try:
        amount = float(raw)
    except ValueError:
        return ""
    return f"{-amount if sign == '-' else amount:.2f}"


def format_amount(value: str) -> str:
    try:
        return f"{float(value):.2f}"
    except (TypeError, ValueError):
        return ""


def parse_date(value: str) -> str:
    text = value.strip()
    if not text:
        return ""
    if re.fullmatch(r"\d+(?:\.0+)?", text):
        try:
            serial = int(float(text))
            if serial > 59:
                serial -= 1
            base = datetime(1899, 12, 31)
            dt = base.fromordinal(base.toordinal() + serial)
            return dt.strftime("%d/%m/%Y")
        except ValueError:
            pass
    for fmt in DATE_PATTERNS:
        try:
            dt = datetime.strptime(text, fmt)
            if dt.year < 100:
                dt = dt.replace(year=2000 + dt.year)
            return dt.strftime("%d/%m/%Y")
        except ValueError:
            continue
    return ""


def adjust_to_business_day(value: str) -> str:
    parsed = parse_date(value)
    if not parsed:
        return ""
    dt = datetime.strptime(parsed, "%d/%m/%Y")
    while dt.weekday() >= 5:
        dt += timedelta(days=1)
    return dt.strftime("%d/%m/%Y")


def sniff_delimiter(sample: str) -> str:
    for delimiter in [",", ";", "\t", "|"]:
        if sample.count(delimiter) >= 2:
            return delimiter
    return ","


def excel_column_name(index: int) -> str:
    label = ""
    number = index
    while number:
        number, remainder = divmod(number - 1, 26)
        label = chr(65 + remainder) + label
    return label


def worksheet_rows_from_xlsx(path: Path) -> list[list[str]]:
    ns = {"main": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    with zipfile.ZipFile(path) as archive:
        shared_strings: list[str] = []
        if "xl/sharedStrings.xml" in archive.namelist():
            shared_root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
            for item in shared_root.findall("main:si", ns):
                text = "".join(node.text or "" for node in item.findall(".//main:t", ns))
                shared_strings.append(text)

        sheet_names = sorted(
            name
            for name in archive.namelist()
            if name.startswith("xl/worksheets/sheet") and name.endswith(".xml")
        )
        rows: list[list[str]] = []
        for sheet_name in sheet_names:
            sheet_root = ET.fromstring(archive.read(sheet_name))
            for row in sheet_root.findall(".//main:row", ns):
                cells: dict[int, str] = {}
                for cell in row.findall("main:c", ns):
                    ref = cell.attrib.get("r", "")
                    letters = "".join(ch for ch in ref if ch.isalpha())
                    column = 0
                    for char in letters:
                        column = column * 26 + (ord(char.upper()) - 64)
                    value_node = cell.find("main:v", ns)
                    inline_node = cell.find("main:is/main:t", ns)
                    value = ""
                    if inline_node is not None and inline_node.text:
                        value = inline_node.text
                    elif value_node is not None and value_node.text:
                        if cell.attrib.get("t") == "s":
                            index = int(value_node.text)
                            value = shared_strings[index] if index < len(shared_strings) else ""
                        else:
                            value = value_node.text
                    if column:
                        cells[column] = value
                if not cells:
                    continue
                row_values = [cells.get(position, "") for position in range(1, max(cells) + 1)]
                rows.append([repair_text(value) for value in row_values])
        return rows


def detect_header_index(rows: list[list[str]]) -> int | None:
    synonyms = {
        "date": {"data", "dt", "lancamento", "vencimento", "movimento", "dt. lancamento"},
        "description": {"descricao", "historico", "memo", "nome", "detalhe", "estabelecimento"},
        "amount": {"valor", "amount", "total", "vlr"},
        "debit": {"debito", "saidas", "saida", "withdrawal"},
        "credit": {"credito", "entrada", "entradas", "deposit"},
    }
    for index, row in enumerate(rows[:5]):
        normalized = {normalize_text(value) for value in row if normalize_text(value)}
        score = 0
        for values in synonyms.values():
            if normalized & values:
                score += 1
        if score >= 2:
            return index
    return None


def build_header_map(header_row: list[str]) -> dict[str, int]:
    mapping: dict[str, int] = {}
    for idx, header in enumerate(header_row):
        token = normalize_text(header)
        if token in {"data", "dt", "vencimento", "movimento", "dt. lancamento", "data lancamento"}:
            mapping["date"] = idx
        elif token in {"descricao", "historico", "memo", "nome", "detalhe", "estabelecimento"}:
            mapping["description"] = idx
        elif token in {"valor", "amount", "total", "vlr"}:
            mapping["amount"] = idx
        elif token in {"debito", "saidas", "saida", "withdrawal"}:
            mapping["debit"] = idx
        elif token in {"credito", "entrada", "entradas", "deposit"}:
            mapping["credit"] = idx
        elif token in {"observacao", "observacoes", "obs"}:
            mapping["observations"] = idx
        elif token in {"conta", "banco"}:
            mapping["account"] = idx
        elif token in {"categoria"}:
            mapping["category"] = idx
        elif token in {"subcategoria"}:
            mapping["subcategory"] = idx
        elif token in {"cartao", "card"}:
            mapping["card"] = idx
    return mapping


def record_from_mapped_row(row: list[str], mapping: dict[str, int], source_file: str, source_type: str) -> ParsedRecord | None:
    date_value = row[mapping["date"]] if "date" in mapping and mapping["date"] < len(row) else ""
    description = row[mapping["description"]] if "description" in mapping and mapping["description"] < len(row) else ""
    amount_value = ""
    if "amount" in mapping and mapping["amount"] < len(row):
        amount_value = row[mapping["amount"]]
    else:
        debit = row[mapping["debit"]] if "debit" in mapping and mapping["debit"] < len(row) else ""
        credit = row[mapping["credit"]] if "credit" in mapping and mapping["credit"] < len(row) else ""
        amount_value = credit or debit
    amount = parse_amount(str(amount_value))
    due_date = parse_date(str(date_value))
    description = clean_description(str(description))
    if not description or not amount or not due_date:
        return None
    return ParsedRecord(
        source_file=source_file,
        source_type=source_type,
        description=description,
        amount=amount,
        due_date=due_date,
        category=repair_text(row[mapping["category"]]) if "category" in mapping and mapping["category"] < len(row) else "",
        subcategory=repair_text(row[mapping["subcategory"]]) if "subcategory" in mapping and mapping["subcategory"] < len(row) else "",
        account=repair_text(row[mapping["account"]]) if "account" in mapping and mapping["account"] < len(row) else "",
        card=repair_text(row[mapping["card"]]) if "card" in mapping and mapping["card"] < len(row) else "",
        observations=repair_text(row[mapping["observations"]]) if "observations" in mapping and mapping["observations"] < len(row) else "",
        raw_text=" | ".join(cell for cell in row if cell),
        confidence=0.96,
    )


def parse_already_formatted_row(values: list[str], source_file: str, source_type: str) -> ParsedRecord | None:
    columns = [value.strip() for value in values]
    if len(columns) < 6:
        return None
    description = clean_description(columns[0])
    amount = format_amount(columns[1])
    due_date = parse_date(columns[2])
    if not description or not amount or not due_date:
        return None
    observations = columns[7] if len(columns) >= 8 else ""
    launch_date = columns[8] if len(columns) >= 9 else ""
    return ParsedRecord(
        source_file=source_file,
        source_type=source_type,
        description=description,
        amount=amount,
        due_date=due_date,
        category=repair_text(columns[3]) if len(columns) >= 4 else "",
        subcategory=repair_text(columns[4]) if len(columns) >= 5 else "",
        account=repair_text(columns[5]) if len(columns) >= 6 else "",
        card=repair_text(columns[6]) if len(columns) >= 7 else "",
        observations=repair_text(observations),
        launch_date=repair_text(launch_date),
        raw_text=",".join(columns),
        confidence=0.99,
    )


def parse_text_line(line: str, source_file: str, source_type: str) -> ParsedRecord | None:
    compact = re.sub(r"\s+", " ", line).strip()
    if not compact:
        return None
    if normalize_text(compact) in IGNORE_TOKENS:
        return None

    amount_match = None
    for match in AMOUNT_RE.finditer(compact):
        amount_match = match
    date_match = DATE_RE.search(compact)
    if not amount_match or not date_match:
        return None

    amount = parse_amount(amount_match.group(0))
    due_date = parse_date(date_match.group(0))
    if not amount or not due_date:
        return None

    description = compact
    description = description[: amount_match.start()] + description[amount_match.end() :]
    description = description[: date_match.start()] + description[date_match.end() :]
    description = clean_description(description)
    if len(description) < 3:
        return None

    confidence = 0.72
    if compact.startswith(date_match.group(0)) or compact.endswith(amount_match.group(0)):
        confidence += 0.12

    return ParsedRecord(
        source_file=source_file,
        source_type=source_type,
        description=description,
        amount=amount,
        due_date=due_date,
        raw_text=compact,
        confidence=round(confidence, 2),
    )


def resolve_relative_date(label: str, base_date: datetime) -> str:
    token = normalize_text(label)
    if token == "hoje":
        return base_date.strftime("%d/%m/%Y")
    if token == "ontem":
        return (base_date - timedelta(days=1)).strftime("%d/%m/%Y")
    parsed = parse_date(label)
    return parsed or base_date.strftime("%d/%m/%Y")


def is_amount_line(line: str) -> bool:
    return bool(AMOUNT_RE.search(line)) and ("R$" in line or line.strip().startswith("-") or line.strip().startswith("+"))


def is_noise_line(line: str) -> bool:
    compact = clean_description(line)
    token = normalize_text(compact)
    if not token:
        return True
    if token in SCREEN_IGNORE_TOKENS:
        return True
    if len(token) <= 2 and not AMOUNT_RE.search(compact):
        return True
    if re.fullmatch(r"[\W_]+", compact):
        return True
    if re.fullmatch(r"[a-z0-9]{1,3}", token) and not TIME_RE.search(compact):
        return True
    return False


def parse_ocr_statement_lines(lines: list[str], source_file: str, reference_date: datetime) -> list[ParsedRecord]:
    records: list[ParsedRecord] = []
    current_date = reference_date.strftime("%d/%m/%Y")
    block: list[str] = []
    active_section = False

    def flush(candidate_lines: list[str], due_date: str) -> None:
        filtered = [repair_text(line).strip() for line in candidate_lines if repair_text(line).strip() and not is_noise_line(line)]
        if not filtered:
            return
        amount_line = next((line for line in reversed(filtered) if is_amount_line(line)), "")
        if not amount_line:
            return
        amount = parse_amount(amount_line)
        if not amount:
            return
        body = [line for line in filtered if line != amount_line]
        time_line = next((line for line in reversed(body) if TIME_RE.search(line)), "")
        if time_line:
            body = [line for line in body if line != time_line]
        body = [line for line in body if normalize_text(line) not in SCREEN_IGNORE_TOKENS and "saldo do dia" not in normalize_text(line)]
        if not body:
            return
        description = ""
        candidates = sorted(body, key=lambda item: (normalize_text(item) in GENERIC_OPERATION_TOKENS, -len(clean_description(item))))
        for line in candidates:
            if normalize_text(line) not in GENERIC_OPERATION_TOKENS:
                description = clean_description(line)
                break
        if not description:
            description = clean_description(body[0])
        if not description:
            return
        observations_parts = [line for line in body if clean_description(line) != description]
        if time_line:
            observations_parts.append(f"Hora {time_line}")
        observations = " | ".join(part for part in observations_parts if part)
        records.append(
            ParsedRecord(
                source_file=source_file,
                source_type="ocr",
                description=description,
                amount=amount,
                due_date=due_date,
                observations=observations,
                raw_text=" | ".join(filtered),
                confidence=0.83,
            )
        )

    for line in [repair_text(item).strip() for item in lines if repair_text(item).strip()]:
        token = normalize_text(line)
        if token in {"hoje", "ontem"} or DATE_RE.search(line):
            if block:
                flush(block, current_date)
                block = []
            current_date = resolve_relative_date(line, reference_date)
            active_section = True
            continue
        if not active_section:
            continue
        if token in SCREEN_IGNORE_TOKENS or "saldo do dia" in token:
            continue
        if is_noise_line(line):
            continue
        block.append(line)
        if is_amount_line(line):
            flush(block, current_date)
            block = []

    if block:
        flush(block, current_date)
    return records


def parse_rows(rows: list[list[str]], source_file: str, source_type: str) -> tuple[list[ParsedRecord], list[str]]:
    diagnostics: list[str] = []
    parsed: list[ParsedRecord] = []
    header_index = detect_header_index(rows)
    if header_index is not None:
        mapping = build_header_map(rows[header_index])
        for row in rows[header_index + 1 :]:
            record = record_from_mapped_row(row, mapping, source_file, source_type)
            if record:
                parsed.append(record)
        if parsed:
            diagnostics.append(f"{source_file}: tabela estruturada detectada.")
            return parsed, diagnostics

    for row in rows:
        values = [str(cell).strip() for cell in row if str(cell).strip()]
        if not values:
            continue
        if len(values) == 1 and values[0].count(",") >= 5:
            formatted = next(csv.reader([values[0]]))
            record = parse_already_formatted_row(formatted, source_file, source_type)
            if record:
                parsed.append(record)
                continue
        formatted = parse_already_formatted_row(values, source_file, source_type)
        if formatted:
            parsed.append(formatted)
            continue
        joined = " ".join(values)
        heuristic = parse_text_line(joined, source_file, source_type)
        if heuristic:
            parsed.append(heuristic)
    if not parsed:
        diagnostics.append(f"{source_file}: nenhuma linha reconhecida automaticamente.")
    return parsed, diagnostics


def iter_reference_files() -> list[Path]:
    available = [path for path in REFERENCE_FILE_CANDIDATES if path.exists()]
    downloads_dir = Path("/Users/lmv/Downloads")
    if downloads_dir.exists():
        for path in sorted(downloads_dir.glob("*atual*.xlsx")):
            if path not in available:
                available.append(path)
    return available


def derive_card_candidates_from_filename(path: Path) -> list[str]:
    stem = normalize_text(path.stem)
    candidates: list[str] = []
    raw_stem = path.stem
    lowered_raw = normalize_text(raw_stem)
    for marker in ["cartao credito atual", "cartao de credito atual"]:
        position = lowered_raw.find(marker)
        if position >= 0:
            prefix = raw_stem[:position].strip(" -_")
            titleized = titleize_candidate(prefix)
            if titleized:
                candidates.append(titleized)
    if not candidates and "picpay" in stem:
        candidates.append("Pic Pay")
    return candidates


def build_reference_data() -> dict[str, list[str]]:
    accounts: set[str] = set()
    cards: set[str] = set()
    observations: set[str] = set()
    descriptions: set[str] = set()
    categories: set[str] = set()
    subcategories: set[str] = set()

    for path in iter_reference_files():
        records, _ = parse_file(path, path.name)
        for record in records:
            if record.account:
                accounts.add(record.account)
            if record.card:
                cards.add(record.card)
            if record.observations:
                observations.add(record.observations)
            if record.description:
                descriptions.add(record.description)
            if record.category:
                categories.add(record.category)
            if record.subcategory:
                subcategories.add(record.subcategory)
        for candidate in derive_card_candidates_from_filename(path):
            cards.add(candidate)
            accounts.add(candidate)
    cards.update(accounts)

    for override in load_overrides():
        if override.get("account"):
            accounts.add(str(override["account"]).strip())
        if override.get("card"):
            cards.add(str(override["card"]).strip())
        if override.get("observations"):
            observations.add(str(override["observations"]).strip())
        if override.get("description"):
            descriptions.add(str(override["description"]).strip())
        if override.get("category"):
            categories.add(str(override["category"]).strip())
        if override.get("subcategory"):
            subcategories.add(str(override["subcategory"]).strip())

    return {
        "accounts": sorted(accounts),
        "cards": sorted(cards),
        "observations": sorted(observations),
        "descriptions": sorted(descriptions),
        "categories": sorted(categories),
        "subcategories": sorted(subcategories),
        "reference_files": [path.name for path in iter_reference_files()],
    }


def normalize_signature_text(value: str) -> str:
    token = normalize_text(value)
    token = re.sub(r"[^a-z0-9\s]", " ", token)
    token = re.sub(r"\s+", " ", token).strip()
    return token


def load_overrides() -> list[dict[str, str]]:
    if not OVERRIDES_FILE.exists():
        return []
    try:
        payload = json.loads(OVERRIDES_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    if not isinstance(payload, list):
        return []
    overrides: list[dict[str, str]] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        overrides.append({str(key): str(value) for key, value in item.items()})
    return overrides


def save_overrides(overrides: list[dict[str, str]]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    OVERRIDES_FILE.write_text(
        json.dumps(overrides, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def build_override_payload(record: dict[str, object]) -> dict[str, str]:
    raw_text = str(record.get("raw_text", "")).strip()
    description = clean_description(str(record.get("description", "")).strip())
    match_text = raw_text or description
    return {
        "id": str(record.get("override_id", "")).strip() or normalize_signature_text(match_text)[:80],
        "match_text": match_text,
        "description": description,
        "category": str(record.get("category", "")).strip(),
        "subcategory": str(record.get("subcategory", "")).strip(),
        "account": str(record.get("account", "")).strip(),
        "card": str(record.get("card", "")).strip(),
        "observations": str(record.get("observations", "")).strip(),
        "entry_type": str(record.get("entry_type", "account")).strip() or "account",
        "created_at": datetime.now().isoformat(timespec="seconds"),
    }


def apply_overrides_to_record(record: ParsedRecord, overrides: list[dict[str, str]]) -> dict[str, object] | None:
    best_override: dict[str, str] | None = None
    best_score = 0.0
    candidates = [record.raw_text, record.description]
    for override in overrides:
        match_text = override.get("match_text", "")
        if not match_text:
            continue
        score = max(description_similarity(match_text, candidate) for candidate in candidates if candidate)
        if score > best_score:
            best_score = score
            best_override = override
    if not best_override or best_score < 0.62:
        return None

    if best_override.get("description"):
        record.description = clean_description(best_override["description"])
    if best_override.get("category"):
        record.category = best_override["category"]
    if best_override.get("subcategory"):
        record.subcategory = best_override["subcategory"]
    if best_override.get("account"):
        record.account = best_override["account"]
    if best_override.get("card"):
        record.card = best_override["card"]
    if best_override.get("observations"):
        record.observations = best_override["observations"]
    return {
        "id": best_override.get("id", ""),
        "match_text": best_override.get("match_text", ""),
        "score": round(best_score, 2),
    }


def description_similarity(left: str, right: str) -> float:
    a = normalize_signature_text(left)
    b = normalize_signature_text(right)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    a_tokens = set(a.split())
    b_tokens = set(b.split())
    intersection = len(a_tokens & b_tokens)
    union = len(a_tokens | b_tokens) or 1
    overlap = intersection / union
    if a in b or b in a:
        overlap = max(overlap, min(len(a), len(b)) / max(len(a), len(b)))
    return overlap


def build_reference_records() -> list[dict[str, str]]:
    reference_records: list[dict[str, str]] = []
    for path in iter_reference_files():
        records, _ = parse_file(path, path.name)
        inferred_cards = derive_card_candidates_from_filename(path)
        inferred_card = inferred_cards[0] if inferred_cards else ""
        for record in records:
            reference_records.append(
                {
                    "source_file": path.name,
                    "description": record.description,
                    "amount": format_amount(record.amount),
                    "due_date": record.due_date,
                    "account": record.account,
                    "card": record.card or inferred_card,
                    "category": record.category,
                    "subcategory": record.subcategory,
                }
            )
    return reference_records


def detect_duplicate(record: ParsedRecord, reference_records: list[dict[str, str]]) -> dict[str, object] | None:
    current_amount = format_amount(record.amount)
    current_date = record.due_date
    best_match: dict[str, object] | None = None
    best_score = 0.0
    for candidate in reference_records:
        if current_amount != candidate["amount"]:
            continue
        similarity = description_similarity(record.description, candidate["description"])
        if similarity < 0.55:
            continue
        score = similarity
        if current_date and candidate["due_date"] == current_date:
            score += 0.45
        if score > best_score:
            best_score = score
            best_match = {
                "description": candidate["description"],
                "amount": candidate["amount"],
                "due_date": candidate["due_date"],
                "account": candidate["account"],
                "card": candidate["card"],
                "source_file": candidate["source_file"],
                "score": round(score, 2),
            }
    return best_match if best_match and best_score >= 1.0 else None


def parse_csv_text(text: str) -> list[list[str]]:
    sample = "\n".join(text.splitlines()[:5])
    delimiter = sniff_delimiter(sample)
    return [row for row in csv.reader(io.StringIO(text), delimiter=delimiter)]


def parse_ofx_text(text: str, source_file: str) -> list[ParsedRecord]:
    blocks = re.findall(r"<STMTTRN>(.*?)</STMTTRN>", text, flags=re.IGNORECASE | re.DOTALL)
    records: list[ParsedRecord] = []
    for block in blocks:
        amount_match = re.search(r"<TRNAMT>(.+)", block)
        date_match = re.search(r"<DTPOSTED>(\d{8})", block)
        description_match = re.search(r"<MEMO>(.+)", block)
        name_match = re.search(r"<NAME>(.+)", block)
        if not amount_match or not date_match:
            continue
        description = description_match.group(1).strip() if description_match else name_match.group(1).strip() if name_match else ""
        amount = format_amount(amount_match.group(1))
        due_date = parse_date(datetime.strptime(date_match.group(1), "%Y%m%d").strftime("%d/%m/%Y"))
        if not description or not amount or not due_date:
            continue
        records.append(
            ParsedRecord(
                source_file=source_file,
                source_type="ofx",
                description=clean_description(description),
                amount=amount,
                due_date=due_date,
                raw_text=block.strip(),
                confidence=0.95,
            )
        )
    return records


def render_pdf_preview(path: Path, temp_dir: Path) -> Path:
    command = ["qlmanage", "-t", "-s", "2200", "-o", str(temp_dir), str(path)]
    result = subprocess.run(command, capture_output=True, text=True, cwd=str(ROOT))
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "Falha ao converter PDF para imagem.")
    candidate = temp_dir / f"{path.name}.png"
    if candidate.exists():
        return candidate
    previews = sorted(temp_dir.glob("*.png"))
    if previews:
        return previews[0]
    raise RuntimeError("Nao foi possivel gerar uma imagem de pre-visualizacao do PDF.")


def run_tesseract_ocr(path: Path) -> str:
    with tempfile.TemporaryDirectory(prefix="integra-mf-ocr-") as temp_dir:
        temp_root = Path(temp_dir)
        source_path = path
        if path.suffix.lower() == ".pdf":
            source_path = render_pdf_preview(path, temp_root)
        command = [
            TESSERACT_BIN,
            str(source_path),
            "stdout",
            "-l",
            "por+eng",
            "--psm",
            "11",
            "-c",
            "preserve_interword_spaces=1",
        ]
        result = subprocess.run(command, capture_output=True, text=True, cwd=str(ROOT))
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or "Falha ao executar OCR com Tesseract.")
        return result.stdout


def run_ocr(path: Path) -> str:
    if shutil.which(TESSERACT_BIN):
        return run_tesseract_ocr(path)
    command = ["swift", str(OCR_SCRIPT), str(path)]
    result = subprocess.run(command, capture_output=True, text=True, cwd=str(ROOT))
    if result.returncode != 0:
        stderr = result.stderr.strip() or "Falha ao executar OCR nativo."
        if "SDK is not supported by the compiler" in stderr or "SwiftShims" in stderr:
            raise RuntimeError(
                "OCR indisponivel no momento: a toolchain Swift instalada nao bate com o SDK do macOS. "
                "Atualize/alinha as Command Line Tools do Xcode para habilitar OCR de imagens e PDFs."
            )
        raise RuntimeError(stderr)
    return result.stdout


def parse_file(path: Path, original_name: str) -> tuple[list[ParsedRecord], list[str]]:
    extension = path.suffix.lower()
    diagnostics: list[str] = []

    if extension in {".csv", ".txt"}:
        text = path.read_text(encoding="utf-8", errors="ignore")
        return parse_rows(parse_csv_text(text), original_name, extension.lstrip(".")), diagnostics

    if extension == ".ofx":
        text = path.read_text(encoding="utf-8", errors="ignore")
        records = parse_ofx_text(text, original_name)
        if not records:
            diagnostics.append(f"{original_name}: OFX lido, mas sem transacoes reconhecidas.")
        return records, diagnostics

    if extension == ".xlsx":
        return parse_rows(worksheet_rows_from_xlsx(path), original_name, "xlsx")

    if extension in {".png", ".jpg", ".jpeg", ".heic", ".pdf"}:
        ocr_output = run_ocr(path)
        lines = [line.strip() for line in ocr_output.splitlines() if line.strip()]
        reference_date = datetime.now()
        records = parse_ocr_statement_lines(lines, original_name, reference_date)
        if not records:
            for line in lines:
                record = parse_text_line(line, original_name, "ocr")
                if record:
                    records.append(record)
        if not records:
            diagnostics.append(f"{original_name}: OCR executado, mas revise manualmente as linhas.")
        return records, diagnostics

    diagnostics.append(f"{original_name}: formato ainda nao suportado ({extension or 'sem extensao'}).")
    return [], diagnostics


def encode_csv(records: Iterable[dict]) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    for item in records:
        description = clean_description(str(item.get("description", "")))
        amount = format_amount(str(item.get("amount", "")))
        due_date = adjust_to_business_day(str(item.get("due_date", "")))
        entry_type = str(item.get("entry_type", "account")).strip() or "account"
        if not description or not amount or not due_date:
            continue
        try:
            numeric_amount = abs(float(amount))
            amount = f"{numeric_amount:.2f}"
        except ValueError:
            continue
        account = str(item.get("account", "")).strip()
        card = str(item.get("card", "")).strip()
        if entry_type != "credit_card":
            card = ""
        row = [
            description,
            amount,
            due_date,
            str(item.get("category", "")).strip(),
            str(item.get("subcategory", "")).strip(),
            account,
            card,
            str(item.get("observations", "")).strip(),
        ]
        launch_date = str(item.get("launch_date", "")).strip()
        if launch_date:
            row.append(launch_date)
        writer.writerow(row)
    return buffer.getvalue()


def parse_multipart(content_type: str, payload: bytes) -> tuple[dict[str, str], list[dict[str, object]]]:
    message = BytesParser(policy=default_policy).parsebytes(
        f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode("utf-8") + payload
    )
    fields: dict[str, str] = {}
    files: list[dict[str, object]] = []
    for part in message.iter_parts():
        disposition = part.get_content_disposition()
        if disposition != "form-data":
            continue
        name = part.get_param("name", header="content-disposition")
        filename = part.get_filename()
        body = part.get_payload(decode=True) or b""
        if filename:
            files.append({"name": name or "file", "filename": filename, "content": body})
        elif name:
            fields[name] = body.decode("utf-8", errors="ignore")
    return fields, files


def process_uploaded_files(files: list[dict[str, object]]) -> dict[str, object]:
    records: list[ParsedRecord] = []
    diagnostics: list[str] = []
    with tempfile.TemporaryDirectory(prefix="integra-mf-") as temp_dir:
        temp_root = Path(temp_dir)
        temp_files: list[tuple[Path, str]] = []
        for index, file_info in enumerate(files):
            filename = str(file_info["filename"])
            safe_name = Path(filename).name
            temp_path = temp_root / f"{index:02d}-{safe_name}"
            temp_path.write_bytes(file_info["content"])  # type: ignore[arg-type]
            temp_files.append((temp_path, safe_name))

        max_workers = min(max(len(temp_files), 1), max((os.cpu_count() or 4) // 2, 4), 8)

        def process_single_file(item: tuple[Path, str]) -> tuple[str, list[ParsedRecord], list[str]]:
            temp_path, safe_name = item
            try:
                file_records, file_diagnostics = parse_file(temp_path, safe_name)
                return safe_name, file_records, file_diagnostics
            except Exception as exc:  # noqa: BLE001
                return safe_name, [], [f"{safe_name}: erro ao processar ({exc})."]

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_map = {
                executor.submit(process_single_file, item): item[1]
                for item in temp_files
            }
            for future in as_completed(future_map):
                _safe_name, file_records, file_diagnostics = future.result()
                records.extend(file_records)
                diagnostics.extend(file_diagnostics)

    reference_records = build_reference_records()
    overrides = load_overrides()
    enriched_records: list[dict[str, object]] = []
    duplicate_count = 0
    learned_count = 0
    for record in records:
        applied_override = apply_overrides_to_record(record, overrides)
        payload = asdict(record)
        duplicate_match = detect_duplicate(record, reference_records)
        payload["duplicate"] = duplicate_match is not None
        payload["duplicate_match"] = duplicate_match
        payload["applied_override"] = applied_override
        if duplicate_match:
            duplicate_count += 1
        if applied_override:
            learned_count += 1
        enriched_records.append(payload)
    if duplicate_count:
        diagnostics.append(f"{duplicate_count} lançamento(s) parecem já estar cadastrados no extrato atual.")
    if learned_count:
        diagnostics.append(f"{learned_count} lançamento(s) receberam override salvo automaticamente.")

    return {
        "records": enriched_records,
        "diagnostics": diagnostics,
        "count": len(records),
    }


def save_override_record(record: dict[str, object]) -> tuple[dict[str, object], int]:
    if not isinstance(record, dict):
        return {"error": "Override invalido."}, HTTPStatus.BAD_REQUEST

    override_payload = build_override_payload(record)
    if not override_payload["match_text"] or not override_payload["description"]:
        return {"error": "Preencha ao menos texto base e descricao antes de salvar o override."}, HTTPStatus.BAD_REQUEST

    overrides = load_overrides()
    overrides = [item for item in overrides if item.get("id") != override_payload["id"]]
    overrides.insert(0, override_payload)
    save_overrides(overrides)
    return {"ok": True, "override": override_payload, "overrides": overrides}, HTTPStatus.OK


class DesktopBridge:
    def get_reference(self) -> dict[str, object]:
        return build_reference_data()

    def get_overrides(self) -> dict[str, object]:
        return {"overrides": load_overrides()}

    def save_override(self, payload: dict[str, object]) -> dict[str, object]:
        result, _status = save_override_record(payload.get("record", {}))  # type: ignore[arg-type]
        return result

    def process_files(self, payload: dict[str, object]) -> dict[str, object]:
        raw_files = payload.get("files", [])
        if not isinstance(raw_files, list):
            return {"error": "Nenhum arquivo recebido."}
        files: list[dict[str, object]] = []
        for item in raw_files:
            if not isinstance(item, dict):
                continue
            try:
                content = base64.b64decode(str(item.get("content", "")))
            except Exception:  # noqa: BLE001
                continue
            files.append(
                {
                    "filename": str(item.get("filename", "arquivo.bin")),
                    "content": content,
                }
            )
        if not files:
            return {"error": "Nenhum arquivo recebido."}
        return process_uploaded_files(files)

    def export_csv(self, payload: dict[str, object]) -> dict[str, object]:
        records = payload.get("records", [])
        csv_payload = encode_csv(records).encode("utf-8-sig")
        return {
            "filename": f"minhasfinancas-{datetime.now().strftime('%Y%m%d-%H%M%S')}.csv",
            "content": base64.b64encode(csv_payload).decode("ascii"),
        }


class AppHandler(BaseHTTPRequestHandler):
    server_version = "IntegraMF/0.1"

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path in {"/", "/index.html"}:
            self.serve_file(INDEX_FILE)
            return
        if parsed.path == "/api/health":
            self.send_json({"ok": True, "host": HOST, "port": PORT})
            return
        if parsed.path == "/api/reference":
            self.send_json(build_reference_data())
            return
        if parsed.path == "/api/overrides":
            self.send_json({"overrides": load_overrides()})
            return
        if parsed.path.startswith("/static/"):
            target = STATIC_DIR / parsed.path.removeprefix("/static/")
            self.serve_file(target)
            return
        self.send_error(HTTPStatus.NOT_FOUND, "Rota nao encontrada.")

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/process":
            self.handle_process()
            return
        if parsed.path == "/api/overrides":
            self.handle_override_save()
            return
        if parsed.path == "/api/export":
            self.handle_export()
            return
        self.send_error(HTTPStatus.NOT_FOUND, "Rota nao encontrada.")

    def serve_file(self, path: Path) -> None:
        if not path.exists() or not path.is_file():
            self.send_error(HTTPStatus.NOT_FOUND, "Arquivo nao encontrado.")
            return
        content_type, _ = mimetypes.guess_type(path.name)
        data = path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type or "application/octet-stream")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def send_json(self, payload: dict, status: int = HTTPStatus.OK) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def handle_process(self) -> None:
        content_type = self.headers.get("Content-Type", "")
        length = int(self.headers.get("Content-Length", "0"))
        payload = self.rfile.read(length)
        if "multipart/form-data" not in content_type:
            self.send_json({"error": "Envie os arquivos como multipart/form-data."}, HTTPStatus.BAD_REQUEST)
            return
        _, files = parse_multipart(content_type, payload)
        if not files:
            self.send_json({"error": "Nenhum arquivo recebido."}, HTTPStatus.BAD_REQUEST)
            return

        self.send_json(process_uploaded_files(files))

    def handle_override_save(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        payload = self.rfile.read(length)
        try:
            parsed = json.loads(payload.decode("utf-8"))
            record = parsed.get("record", {})
        except json.JSONDecodeError:
            self.send_json({"error": "JSON invalido."}, HTTPStatus.BAD_REQUEST)
            return
        result, status = save_override_record(record)
        self.send_json(result, status)

    def handle_export(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        payload = self.rfile.read(length)
        try:
            parsed = json.loads(payload.decode("utf-8"))
            records = parsed.get("records", [])
        except json.JSONDecodeError:
            self.send_json({"error": "JSON invalido."}, HTTPStatus.BAD_REQUEST)
            return

        csv_payload = encode_csv(records).encode("utf-8-sig")
        filename = f"minhasfinancas-{datetime.now().strftime('%Y%m%d-%H%M%S')}.csv"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/csv; charset=utf-8")
        self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.send_header("Content-Length", str(len(csv_payload)))
        self.end_headers()
        self.wfile.write(csv_payload)


def main() -> None:
    if "--desktop" in sys.argv and webview is not None:
        window = webview.create_window("Integra MF", INDEX_FILE.as_uri(), js_api=DesktopBridge(), width=1480, height=980)
        webview.start()
        return

    host = HOST
    port = PORT
    open_browser = "--open" in sys.argv
    numeric_args = [argument for argument in sys.argv[1:] if argument.isdigit()]
    if numeric_args:
        port = int(numeric_args[0])
    server = ThreadingHTTPServer((host, port), AppHandler)
    app_url = f"http://{host}:{port}"
    print(f"Integra MF em {app_url}")
    print("Abra esse endereço no navegador para usar o app.")
    if open_browser and sys.platform == "darwin":
        try:
            subprocess.Popen(["open", app_url], cwd=str(ROOT))
        except OSError:
            pass
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nServidor encerrado.")


if __name__ == "__main__":
    main()
