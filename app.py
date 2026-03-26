#!/usr/bin/env python3
from __future__ import annotations

import csv
import io
import json
import mimetypes
import re
import subprocess
import sys
import tempfile
import unicodedata
import zipfile
from dataclasses import dataclass, asdict
from datetime import datetime
from email.parser import BytesParser
from email.policy import default as default_policy
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Iterable
from urllib.parse import urlparse
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parent
STATIC_DIR = ROOT / "static"
OCR_SCRIPT = ROOT / "scripts" / "ocr.swift"
HOST = "127.0.0.1"
PORT = 8765

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
    raw = value.strip().replace("R$", "").replace(" ", "")
    if not raw:
        return ""
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
    for fmt in DATE_PATTERNS:
        try:
            dt = datetime.strptime(text, fmt)
            if dt.year < 100:
                dt = dt.replace(year=2000 + dt.year)
            return dt.strftime("%d/%m/%Y")
        except ValueError:
            continue
    return ""


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


def run_ocr(path: Path) -> str:
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
        records = []
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
        due_date = parse_date(str(item.get("due_date", "")))
        if not description or not amount or not due_date:
            continue
        row = [
            description,
            amount,
            due_date,
            str(item.get("category", "")).strip(),
            str(item.get("subcategory", "")).strip(),
            str(item.get("account", "")).strip(),
            str(item.get("card", "")).strip(),
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


class AppHandler(BaseHTTPRequestHandler):
    server_version = "IntegraMF/0.1"

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/":
            self.serve_file(STATIC_DIR / "index.html")
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

        records: list[ParsedRecord] = []
        diagnostics: list[str] = []
        with tempfile.TemporaryDirectory(prefix="integra-mf-") as temp_dir:
            temp_root = Path(temp_dir)
            for index, file_info in enumerate(files):
                filename = str(file_info["filename"])
                safe_name = Path(filename).name
                temp_path = temp_root / f"{index:02d}-{safe_name}"
                temp_path.write_bytes(file_info["content"])  # type: ignore[arg-type]
                try:
                    file_records, file_diagnostics = parse_file(temp_path, safe_name)
                except Exception as exc:  # noqa: BLE001
                    diagnostics.append(f"{safe_name}: erro ao processar ({exc}).")
                    continue
                records.extend(file_records)
                diagnostics.extend(file_diagnostics)

        self.send_json(
            {
                "records": [asdict(record) for record in records],
                "diagnostics": diagnostics,
                "count": len(records),
            }
        )

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
    host = HOST
    port = PORT
    if len(sys.argv) >= 2:
        port = int(sys.argv[1])
    server = ThreadingHTTPServer((host, port), AppHandler)
    print(f"Integra MF em http://{host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nServidor encerrado.")


if __name__ == "__main__":
    main()
