"""
HR tools MCP server (Week 4).

Offers two tools to any MCP-compatible app (Claude Code, or week-04/agent.py):

  search_policy      Searches the HR policy documents and returns the 4 closest
                     passages with their source files. Same approach as week-03/ask.py:
                     documents are split into chunks at headings, stored in a Chroma
                     database (cosine similarity), and rebuilt when the documents change.

  get_leave_balance  Looks up an employee's leave balance in the employees CSV.
                     Two modes, chosen by how the server is started, never by the model:
                       HR staff mode (default)    takes an employee's work email.
                       Employee mode              --as-employee <address>: takes no inputs
                                                  and can return only that employee's balance.

Nothing is tied to one company's files. Each organization is a folder in week-04/
(hartwell-hr/, ridgeline-hotels/, ...) holding hr-docs/ and employees.csv; pick one with
--org. --docs and --employees override either path. No employee-file column names are
assumed: the email column and the leave balance columns are worked out from the header row.

Usage (normally started by an MCP client, which talks to it over stdin/stdout):
  python week-04/hr_mcp_server.py                                  (default org: hartwell-hr)
  python week-04/hr_mcp_server.py --org ridgeline-hotels
  python week-04/hr_mcp_server.py --as-employee megan.turner@hartwellfoods.example
  python week-04/hr_mcp_server.py --docs <folder> --employees <file.csv>
  python week-04/hr_mcp_server.py --org ridgeline-hotels --rebuild-index   (rebuild, then exit)

Log messages go to stderr; stdout is reserved for the MCP protocol.
"""

import argparse
import csv
import hashlib
import re
import sys
from html.parser import HTMLParser
from pathlib import Path
from typing import Annotated

import chromadb
from chromadb.config import Settings
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import Field
from pypdf import PdfReader

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_ORG = "hartwell-hr"  # an organization is a folder in week-04/ with hr-docs/ and employees.csv
DB_ROOT = SCRIPT_DIR / "chroma_db"  # one subfolder per document folder; ignored by Git
TOP_K = 4
MAX_CHUNK_CHARS = 1500
DOC_EXTENSIONS = {".html", ".htm", ".md", ".txt", ".pdf"}

NO_RECORD_MESSAGE = (
    "No employee record matches the sender's address. Don't state any balance. "
    "Ask the sender to contact HR directly."
)


def log(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


# ---------- Policy search (reuses the week-03/ask.py approach) ----------

class _HTMLSections(HTMLParser):
    """Splits an HTML page into sections at each h1/h2/h3 heading. Skips <head>, <script>, <style>."""

    HEADINGS = {"h1", "h2", "h3"}
    SKIP = {"head", "script", "style", "nav", "header", "footer"}

    def __init__(self):
        super().__init__()
        self.sections = [["", []]]  # [heading, [text pieces]]
        self._skip_depth = 0
        self._in_heading = False

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self._skip_depth += 1
        elif tag in self.HEADINGS and not self._skip_depth:
            self.sections.append(["", []])
            self._in_heading = True

    def handle_endtag(self, tag):
        if tag in self.SKIP and self._skip_depth:
            self._skip_depth -= 1
        elif tag in self.HEADINGS:
            self._in_heading = False

    def handle_data(self, data):
        text = " ".join(data.split())
        if not text or self._skip_depth:
            return
        if self._in_heading:
            self.sections[-1][0] = (self.sections[-1][0] + " " + text).strip()
        else:
            self.sections[-1][1].append(text)


def _sections_from_file(path: Path) -> list[tuple[str, str]]:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        reader = PdfReader(path)
        return [(f"Page {n}", page.extract_text() or "") for n, page in enumerate(reader.pages, start=1)]
    raw = path.read_text(encoding="utf-8", errors="replace")
    if suffix in {".html", ".htm"}:
        parser = _HTMLSections()
        parser.feed(raw)
        return [(heading, " ".join(parts)) for heading, parts in parser.sections]
    if suffix == ".md":
        sections, heading, lines = [], "", []
        for line in raw.splitlines():
            if line.lstrip().startswith("#"):
                sections.append((heading, "\n".join(lines)))
                heading, lines = line.lstrip("# ").strip(), []
            else:
                lines.append(line)
        sections.append((heading, "\n".join(lines)))
        return sections
    return [("", raw)]  # .txt


def _split_long(text: str) -> list[str]:
    """Keep chunks under MAX_CHUNK_CHARS by packing paragraphs/sentences together."""
    if len(text) <= MAX_CHUNK_CHARS:
        return [text]
    pieces = re.split(r"\n\s*\n|(?<=[.!?])\s+", text)
    chunks, current = [], ""
    for piece in pieces:
        if current and len(current) + len(piece) + 1 > MAX_CHUNK_CHARS:
            chunks.append(current)
            current = piece
        else:
            current = f"{current} {piece}".strip()
    if current:
        chunks.append(current)
    return chunks


def _doc_paths(docs_dir: Path) -> list[Path]:
    return sorted(p for p in docs_dir.rglob("*") if p.suffix.lower() in DOC_EXTENSIONS)


def load_chunks(docs_dir: Path) -> list[dict]:
    chunks = []
    for path in _doc_paths(docs_dir):
        source = path.relative_to(docs_dir).as_posix()
        for heading, text in _sections_from_file(path):
            text = text.strip()
            if not text or (not heading and len(text) < 80):
                continue  # skip empty sections and short un-headed fragments (site banners etc.)
            for piece in _split_long(text):
                body = f"{heading}\n{piece}" if heading else piece
                chunks.append({"source": source, "section": heading, "text": body})
    return chunks


def fingerprint(docs_dir: Path) -> str:
    """Hash of every document's name and contents, used to detect when the index is stale."""
    digest = hashlib.sha256()
    for path in _doc_paths(docs_dir):
        digest.update(path.relative_to(docs_dir).as_posix().encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def get_collection(docs_dir: Path, rebuild: bool = False):
    """Open the Chroma collection for this folder, building it if missing, stale, or rebuild=True."""
    # Named after the parent folder too: every organization's documents folder is called hr-docs,
    # so the folder name alone would make all organizations share (and keep rebuilding) one database.
    db_path = DB_ROOT / re.sub(r"[^A-Za-z0-9._-]", "_", f"{docs_dir.parent.name}_{docs_dir.name}")
    client = chromadb.PersistentClient(path=str(db_path), settings=Settings(anonymized_telemetry=False))
    current = fingerprint(docs_dir)
    name = "docs"

    try:
        collection = client.get_collection(name)
        if not rebuild and collection.metadata.get("fingerprint") == current:
            return collection
        client.delete_collection(name)  # documents changed, or a rebuild was requested
    except Exception:
        pass  # collection doesn't exist yet

    chunks = load_chunks(docs_dir)
    if not chunks:
        sys.exit(f"No documents ({', '.join(sorted(DOC_EXTENSIONS))}) found in {docs_dir}")
    log(f"Indexing {len(chunks)} chunks from {docs_dir.name}/ into {db_path} ...")
    collection = client.create_collection(name, metadata={"hnsw:space": "cosine", "fingerprint": current})
    collection.add(
        ids=[f"{c['source']}::{i}" for i, c in enumerate(chunks)],
        documents=[c["text"] for c in chunks],
        metadatas=[{"source": c["source"], "section": c["section"]} for c in chunks],
    )
    return collection


def retrieve(collection, query: str, k: int = TOP_K) -> list[dict]:
    result = collection.query(
        query_texts=[query],
        n_results=min(k, collection.count()),
        include=["documents", "metadatas", "distances"],
    )
    return [
        {"rank": rank, "similarity": 1.0 - distance, "source": meta["source"], "text": doc}
        for rank, (doc, meta, distance) in enumerate(
            zip(result["documents"][0], result["metadatas"][0], result["distances"][0]), start=1
        )
    ]


# ---------- Leave balances ----------

# Words that mark a column as a leave balance, e.g. "vacation_days_left" or "PTO Days Remaining".
BALANCE_WORDS = ("day", "hour", "balance", "left", "remaining", "pto", "vacation", "sick", "personal", "leave")


def _is_number(value: str) -> bool:
    try:
        float(value)
        return True
    except ValueError:
        return False


def load_employees(csv_path: Path) -> tuple[str, list[str], dict[str, dict]]:
    """Read the CSV and index rows by email.

    No column names are assumed; both are worked out from the header row (and the values):
      email column     the header that contains "email"
      balance columns  every column whose values are all numbers and whose header names a
                       leave balance (contains a word such as "days", "remaining", or "PTO")
    """
    with csv_path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        headers = reader.fieldnames or []
        all_rows = list(reader)

    email_col = next((h for h in headers if "email" in h.lower()), None)
    if email_col is None:
        sys.exit(f"No email column found in {csv_path}. Headers: {', '.join(headers)}")

    balance_cols = []
    for h in headers:
        values = [(row.get(h) or "").strip() for row in all_rows]
        filled = [v for v in values if v]
        if (
            h != email_col
            and filled
            and all(_is_number(v) for v in filled)
            and any(word in h.lower() for word in BALANCE_WORDS)
        ):
            balance_cols.append(h)
    if not balance_cols:
        sys.exit(f"No leave balance columns found in {csv_path}. Headers: {', '.join(headers)}")
    log(f"Employee file {csv_path.name}: email column '{email_col}'; balance columns: "
        + ", ".join(f"'{c}'" for c in balance_cols))

    rows = {}
    for row in all_rows:
        address = (row.get(email_col) or "").strip().lower()
        if address:
            rows[address] = row
    return email_col, balance_cols, rows


def _readable(header: str) -> str:
    text = header.replace("_", " ").strip()
    return text[:1].upper() + text[1:]  # keeps acronyms such as "PTO" as written


def format_balance(row: dict, balance_cols: list[str]) -> str:
    """The employee's details, then their leave balances under their own heading."""
    def show(value):
        return value if value not in (None, "") else "(none)"

    details = [f"{_readable(k)}: {show(v)}" for k, v in row.items() if k not in balance_cols]
    balances = [f"  {_readable(k)}: {show(row.get(k))}" for k in balance_cols]
    return "\n".join(details + ["Leave balances:"] + balances)


# ---------- Server ----------

def build_server(docs_dir: Path, employees_csv: Path, as_employee: str | None) -> MCPServer:
    collection = get_collection(docs_dir)
    _, balance_cols, employees = load_employees(employees_csv)
    server = MCPServer("hr-tools", log_level="WARNING")  # keep the agent's terminal output readable

    def lookup(address: str) -> str:
        row = employees.get(address.strip().lower())
        if row is None:
            raise ToolError(NO_RECORD_MESSAGE)  # returned to the model with is_error: true
        return format_balance(row, balance_cols)

    @server.tool(
        description=(
            "Search the organization's HR policy documents. Use this for any policy question "
            "(for example leave, benefits, scheduling, remote work, or reporting concerns). "
            "Returns the 4 closest passages with their source files."
        )
    )
    def search_policy(query: Annotated[str, Field(description="What to look for")]) -> str:
        results = retrieve(collection, query)
        if not results:
            return "No passages found. Say the policies don't answer this question."
        return "\n\n".join(
            f"[{r['rank']}] Source: {r['source']} (similarity {r['similarity']:.2f})\n{r['text']}"
            for r in results
        )

    if as_employee:
        # Employee mode: no inputs. The address comes from how the server was started.
        sender = as_employee.strip().lower()

        @server.tool(
            name="get_leave_balance",
            description=(
                "Get the leave balance of the employee who sent this email. "
                "Takes no inputs: it can return only the sender's own balance."
            ),
        )
        def get_own_leave_balance() -> str:
            return lookup(sender)

    else:
        # HR staff mode: HR may look up any employee by work email.

        @server.tool(
            name="get_leave_balance",
            description=(
                "Get an employee's leave balance (for HR staff). Takes the employee's work email "
                "address and returns their record, including days left for each type of leave."
            ),
        )
        def get_leave_balance(
            email: Annotated[str, Field(description="The employee's work email address")],
        ) -> str:
            return lookup(email)

    return server


def resolve(value: str | None, default: Path) -> Path:
    path = Path(value).expanduser() if value else default
    return path if path.is_absolute() else (Path.cwd() / path).resolve()


def main():
    parser = argparse.ArgumentParser(description="MCP server offering HR policy search and leave balance tools.")
    parser.add_argument(
        "--org",
        default=DEFAULT_ORG,
        help=f"Organization folder in week-04/ holding hr-docs/ and employees.csv (default: {DEFAULT_ORG})",
    )
    parser.add_argument("--docs", help="Folder of HR policy documents (default: week-04/<org>/hr-docs)")
    parser.add_argument("--employees", help="Employees CSV (default: week-04/<org>/employees.csv)")
    parser.add_argument(
        "--as-employee",
        metavar="EMAIL",
        help="Employee mode: get_leave_balance takes no inputs and returns only this address's balance",
    )
    parser.add_argument("--rebuild-index", action="store_true", help="Rebuild the Chroma database, then exit")
    args = parser.parse_args()

    org_dir = SCRIPT_DIR / args.org
    if not (args.docs and args.employees) and not org_dir.is_dir():
        orgs = sorted(
            p.name for p in SCRIPT_DIR.iterdir() if (p / "hr-docs").is_dir() and (p / "employees.csv").is_file()
        )
        sys.exit(f"Organization folder not found: {org_dir}. Available: {', '.join(orgs) or '(none)'}")
    docs_dir = resolve(args.docs, org_dir / "hr-docs")
    employees_csv = resolve(args.employees, org_dir / "employees.csv")
    if not docs_dir.is_dir():
        sys.exit(f"Document folder not found: {docs_dir}")
    if not employees_csv.is_file():
        sys.exit(f"Employees file not found: {employees_csv}")

    if args.rebuild_index:
        get_collection(docs_dir, rebuild=True)
        log("Index rebuilt.")
        return

    build_server(docs_dir, employees_csv, args.as_employee).run()  # stdio transport


if __name__ == "__main__":
    main()
