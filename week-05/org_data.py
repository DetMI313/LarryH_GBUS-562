"""
Company folders (Week 5), shared by supply_mcp_server.py and specialists.py.

A company folder (for example week-05/brookfield-bikes or week-05/copperline-coffee) holds:

  parts.csv, inbound.csv, bill-of-materials.csv, orders.csv, suppliers.csv
  prompts/        analyst.txt, supplier-writer.txt, customer-writer.txt, reviewer.txt,
                  orchestrator.txt
  supplier-emails/

Column names differ between companies (Brookfield's orders.csv has model, qty, and
ship_in_days; Copperline's has product, units, and ship_by_day). So the column names are
not fixed: Org.table() reads a CSV's header row, works out which column holds each value,
and returns the rows under fixed internal names:

  parts.csv              part_id, description, on_hand, supplier
  inbound.csv            po_number, part_id, supplier, qty, arrives_day
  bill-of-materials.csv  product, part_id, per_unit
  orders.csv             order_id, customer, contact_email, product, quantity, ship_day,
                         late_penalty
  suppliers.csv          supplier, contact_name, contact_email, part_id

How a column is found: the header is lowercased, with spaces and punctuation turned into
underscores (so "Ship By Day" becomes ship_by_day). Each value has a list of patterns, tried
in order, and only columns not yet taken count. Number values (quantity, ship day, parts
per unit, ...) only match columns whose values are all whole numbers. If a pattern matches
two columns, the one named exactly like the value wins, otherwise it's an error. If no
pattern matches, a few values have a fallback that looks at the data: parts per unit and
the order quantity take the only whole-number column left, the product in
bill-of-materials.csv takes the only text column left, and the product in orders.csv takes
the column whose values best match the products in bill-of-materials.csv. If a column still
can't be found, the error names the file, the value, and the headers.
"""

import csv
import re
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent            # week-05/
DEFAULT_ORG = SCRIPT_DIR / "brookfield-bikes"

DATA_FILES = ("parts.csv", "inbound.csv", "bill-of-materials.csv", "orders.csv", "suppliers.csv")
SERVER_FILES = DATA_FILES[:4]                           # what supply_mcp_server.py needs
PROMPT_NAMES = ("analyst", "supplier-writer", "customer-writer", "reviewer", "orchestrator")


class OrgError(Exception):
    """The company folder is missing something, or a column can't be found."""


# ---------- Finding the company folder ----------

def resolve_org(value: str | None, required: tuple[str, ...] = DATA_FILES, prompts: bool = True) -> Path:
    """The company folder named by --org: a path (from the current folder), or the name of a
    folder in week-05. None means week-05/brookfield-bikes. Checks that it has what's needed."""
    if not value:
        folder = DEFAULT_ORG
    else:
        given = Path(value).expanduser()
        folder = next((p for p in (given, SCRIPT_DIR / given) if p.is_dir()), None)
        if folder is None:
            raise OrgError(f"Company folder not found: {value}")
    folder = folder.resolve()

    missing = [n for n in required if not (folder / n).is_file()]
    if prompts:
        missing += [f"prompts/{n}.txt" for n in PROMPT_NAMES if not (folder / "prompts" / f"{n}.txt").is_file()]
    if missing:
        hint = ""
        inner = [d for d in folder.iterdir() if d.is_dir() and (d / "orders.csv").is_file()]
        if len(inner) == 1:  # e.g. an archive unzipped one level too deep
            hint = f" (the files seem to be one folder down, in {inner[0]})"
        raise OrgError(f"Missing in {folder}: {', '.join(missing)}{hint}")
    return folder


# ---------- Finding the columns ----------

def norm(header: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", header.lower()).strip("_")


def words(*alternatives: str) -> str:
    """A pattern matching any of the alternatives as whole words of a normalized header."""
    return r"(?:^|_)(?:" + "|".join(alternatives) + r")(?:_|$)"


PART_ID = ("part_id", [r"^part_?(id|no|num|number|code)$", words("part", "sku", "component")])
SUPPLIER = ("supplier", [words("supplier", "vendor", "manufacturer", "maker")])
EMAIL = ("contact_email", [words("e_?mail")])
PRODUCT_WORDS = words("product", "model", "item", "machine", "bike", "assembly", "finished_good", "sku")

# Per file: (internal name, patterns in order, whole numbers only?, fallback), in the order
# they are resolved. Order matters: a column taken by an earlier value can't be taken again.
SPECS: dict[str, list[tuple]] = {
    "parts.csv": [
        (*PART_ID, False, None),
        (*SUPPLIER, False, None),
        ("on_hand", [words("on_?hand", "in_?stock", "stock", "inventory", "available"),
                     words("qty", "quantity", "units", "count")], True, "only_number"),
        ("description", [words("description", "desc", "name", "title")], False, "only_text"),
    ],
    "inbound.csv": [
        ("po_number", [words("po", "po_?number", "purchase_?order")], False, None),
        (*PART_ID, False, None),
        (*SUPPLIER, False, None),
        ("arrives_day", [r"arriv.*day|day.*arriv", words(r"arriv\w*", "eta", "due", "days?")], True, None),
        ("qty", [words("qty", "quantity", "units?", "count", "amount", "pieces", "pcs")], True, "only_number"),
    ],
    "bill-of-materials.csv": [
        (*PART_ID, False, None),
        ("per_unit", [words("per"), words("each", "usage", "ratio"),
                      words("qty", "quantity", "count", "units?")], True, "only_number"),
        ("product", [PRODUCT_WORDS], False, "only_text"),
    ],
    "orders.csv": [
        ("order_id", [r"^order_?(id|no|num|number)$", words("order"), r"^id$"], False, None),
        (*EMAIL, False, None),
        ("customer", [words("customer", "client", "buyer", "account"), words("company", "name")], False, None),
        ("late_penalty", [words("penalty", "penalties", "late_fee", "sla")], False, None),
        ("ship_day", [r"ship.*day|day.*ship", r"(due|deliver).*day|day.*(due|deliver)",
                      words(r"ship\w*", "due", r"deliver\w*", "days?")], True, None),
        ("quantity", [words("qty", "quantity", "units?", "count", "amount", "pieces", "pcs", "ordered")],
         True, "only_number"),
        ("product", [PRODUCT_WORDS], False, "matches_bom"),
    ],
    "suppliers.csv": [
        (*EMAIL, False, None),
        (*SUPPLIER, False, None),
        (*PART_ID, False, None),
        ("contact_name", [words("contact", "person", "rep", "representative"), words("name")], False, None),
    ],
}

LABELS = {"product": "the product", "quantity": "the quantity ordered", "per_unit": "the parts per unit",
          "ship_day": "the ship day", "arrives_day": "the arrival day", "qty": "the quantity",
          "on_hand": "the stock on hand"}


def is_whole_numbers(rows: list[dict], header: str) -> bool:
    values = [r[header] for r in rows if r[header] != ""]
    return bool(values) and all(re.fullmatch(r"[+-]?\d+", v) for v in values)


def read_raw(path: Path) -> tuple[list[str], list[dict]]:
    with path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        headers = [h.strip() for h in (reader.fieldnames or []) if h and h.strip()]
        rows = [{(k or "").strip(): (v or "").strip() for k, v in row.items() if k} for row in reader]
    return headers, rows


class Org:
    """One company folder. Tables are read fresh each call, so edits take effect at once."""

    def __init__(self, folder: Path):
        self.folder = Path(folder)
        self.name = self.folder.name                                    # e.g. copperline-coffee
        self.short_name = re.split(r"[-_ ]+", self.name)[0].capitalize()  # e.g. Copperline

    def columns(self, file: str) -> dict[str, str]:
        """Internal name -> the header in this company's file."""
        headers, rows = read_raw(self.folder / file)
        return self._resolve(file, headers, rows)

    def table(self, file: str) -> list[dict]:
        """The rows of one CSV, under the internal names (all values are strings)."""
        headers, rows = read_raw(self.folder / file)
        cols = self._resolve(file, headers, rows)
        return [{name: row.get(header, "") for name, header in cols.items()} for row in rows]

    def _resolve(self, file: str, headers: list[str], rows: list[dict]) -> dict[str, str]:
        found: dict[str, str] = {}
        for name, patterns, numeric, fallback in SPECS[file]:
            free = [h for h in headers if h not in found.values()
                    and (not numeric or is_whole_numbers(rows, h))]
            header = self._match(file, name, patterns, free) or self._fallback(fallback, free, rows)
            if header is None:
                kind = " (whole numbers)" if numeric else ""
                raise OrgError(f"{self.folder / file}: can't tell which column holds "
                               f"{LABELS.get(name, name)}{kind}. Columns: {', '.join(headers)}")
            found[name] = header
        return found

    def _match(self, file: str, name: str, patterns: list[str], free: list[str]) -> str | None:
        for pattern in patterns:
            hits = [h for h in free if re.search(pattern, norm(h))]
            if len(hits) == 1:
                return hits[0]
            if len(hits) > 1:
                exact = [h for h in hits if norm(h) == name]
                if len(exact) == 1:
                    return exact[0]
                raise OrgError(f"{self.folder / file}: more than one column could hold "
                               f"{LABELS.get(name, name)}: {', '.join(hits)}")
        return None

    def _fallback(self, fallback: str | None, free: list[str], rows: list[dict]) -> str | None:
        if fallback == "only_number":
            numbers = [h for h in free if is_whole_numbers(rows, h)]
            return numbers[0] if len(numbers) == 1 else None
        if fallback == "only_text":
            text = [h for h in free if not is_whole_numbers(rows, h)]
            return text[0] if len(text) == 1 else None
        if fallback == "matches_bom":
            products = {r["product"].lower() for r in self.table("bill-of-materials.csv")}
            share = {h: sum(r[h].lower() in products for r in rows) / max(len(rows), 1) for h in free}
            best = max(share, key=share.get, default=None)
            return best if best is not None and share[best] >= 0.5 else None
        return None

    def describe(self) -> str:
        """One line per file: internal name = header, for the values whose names may differ."""
        lines = []
        for file in DATA_FILES:
            cols = self.columns(file)
            lines.append(f"  {file}: " + ", ".join(f"{k}={v}" for k, v in cols.items()))
        return "\n".join(lines)
