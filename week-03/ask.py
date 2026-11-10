"""Ask questions about a folder of documents (RAG with Chroma + Claude).

How it works:
  1. Load every document in the --docs folder (.html, .htm, .md, .txt, .pdf) and split it into chunks.
  2. Store the chunks in a Chroma database for that folder (week-03/chroma_db/<folder name>/).
     The database is rebuilt automatically whenever the folder's files change.
  3. Retrieve the chunks most similar to the question (cosine similarity).
  4. Send only those chunks to Claude and print its answer.

Usage:
  python ask.py "What is the overdraft fee?"                          # uses copperline-docs/
  python ask.py --docs larkfield-docs "What is the overdraft fee?"
  python ask.py --docs larkfield-docs --save runs/q1.txt "What is the overdraft fee?"

Output (printed, and written to the --save file as UTF-8):
  the retrieved chunks, each with rank, similarity score, and source file,
  then a line containing only ANSWER, then Claude's answer.

Requires an Anthropic API key in the ANTHROPIC_API_KEY environment variable.
"""

import argparse
import hashlib
import re
import sys
from html.parser import HTMLParser
from pathlib import Path

import anthropic
import chromadb
from chromadb.config import Settings
from pypdf import PdfReader

MODEL = "claude-sonnet-5"
SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_DOCS = SCRIPT_DIR / "copperline-docs"
DB_ROOT = SCRIPT_DIR / "chroma_db"  # one subfolder per document folder; ignored by Git
TOP_K = 4
MAX_CHUNK_CHARS = 1500
DOC_EXTENSIONS = {".html", ".htm", ".md", ".txt", ".pdf"}

SYSTEM_PROMPT = """You answer questions using ONLY the document excerpts provided in the user message.
- Every fact in your answer must come from the excerpts. Do not use outside knowledge or make assumptions.
- After each fact, cite the source file in square brackets, e.g. [fee-refund-guide.html].
- If the excerpts do not contain the answer, say that the documents provided do not answer the question.
- If excerpts disagree, say so and show what each source says.
- Keep the answer short and direct."""


# ---------- 1. Load and chunk ----------

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


def load_chunks(docs_dir: Path) -> list[dict]:
    chunks = []
    for path in sorted(p for p in docs_dir.rglob("*") if p.suffix.lower() in DOC_EXTENSIONS):
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
    for path in sorted(p for p in docs_dir.rglob("*") if p.suffix.lower() in DOC_EXTENSIONS):
        digest.update(path.relative_to(docs_dir).as_posix().encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


# ---------- 2. Index in Chroma (one database per document folder) ----------

def get_collection(docs_dir: Path):
    db_path = DB_ROOT / re.sub(r"[^A-Za-z0-9._-]", "_", docs_dir.name)
    client = chromadb.PersistentClient(path=str(db_path), settings=Settings(anonymized_telemetry=False))
    current = fingerprint(docs_dir)
    name = "docs"

    try:
        collection = client.get_collection(name)
        if collection.metadata.get("fingerprint") == current:
            return collection, db_path
        client.delete_collection(name)  # documents changed: rebuild
    except Exception:
        pass  # collection doesn't exist yet

    chunks = load_chunks(docs_dir)
    if not chunks:
        sys.exit(f"No documents ({', '.join(sorted(DOC_EXTENSIONS))}) found in {docs_dir}")
    print(f"Indexing {len(chunks)} chunks from {docs_dir.name}/ into {db_path} ...", file=sys.stderr)
    collection = client.create_collection(
        name, metadata={"hnsw:space": "cosine", "fingerprint": current}
    )
    collection.add(
        ids=[f"{c['source']}::{i}" for i, c in enumerate(chunks)],
        documents=[c["text"] for c in chunks],
        metadatas=[{"source": c["source"], "section": c["section"]} for c in chunks],
    )
    return collection, db_path


# ---------- 3. Retrieve ----------

def retrieve(collection, question: str, k: int = TOP_K) -> list[dict]:
    result = collection.query(
        query_texts=[question],
        n_results=min(k, collection.count()),
        include=["documents", "metadatas", "distances"],
    )
    return [
        {
            "rank": rank,
            "similarity": 1.0 - distance,  # cosine distance -> cosine similarity
            "source": meta["source"],
            "text": doc,
        }
        for rank, (doc, meta, distance) in enumerate(
            zip(result["documents"][0], result["metadatas"][0], result["distances"][0]), start=1
        )
    ]


# ---------- 4. Generate ----------

def answer(question: str, chunks: list[dict]) -> str:
    excerpts = "\n\n".join(
        f'<excerpt rank="{c["rank"]}" source="{c["source"]}">\n{c["text"]}\n</excerpt>' for c in chunks
    )
    response = anthropic.Anthropic().messages.create(
        model=MODEL,
        max_tokens=16000,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": f"<excerpts>\n{excerpts}\n</excerpts>\n\nQuestion: {question}"}],
    )
    if response.stop_reason == "refusal":
        return "(Claude declined to answer this question.)"
    return "".join(block.text for block in response.content if block.type == "text").strip()


def format_output(chunks: list[dict], answer_text: str) -> str:
    lines = []
    for c in chunks:
        lines.append(f"Rank: {c['rank']} | Similarity: {c['similarity']:.4f} | Source: {c['source']}")
        lines.append(c["text"])
        lines.append("")
    lines.append("ANSWER")
    lines.append(answer_text)
    return "\n".join(lines) + "\n"


def resolve_docs(value: str | None) -> Path:
    if value is None:
        path = DEFAULT_DOCS
    else:
        path = Path(value)
        if not path.is_dir() and not path.is_absolute() and (SCRIPT_DIR / path).is_dir():
            path = SCRIPT_DIR / path  # allow "--docs larkfield-docs" from the repo root too
    if not path.is_dir():
        sys.exit(f"Document folder not found: {path}")
    return path.resolve()


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(description="Ask a question about a folder of documents.")
    parser.add_argument("question", nargs="+", help="The question to ask")
    parser.add_argument("--docs", help="Document folder (default: week-03/copperline-docs)")
    parser.add_argument("--save", metavar="FILE", help="Also write the complete output to FILE (UTF-8)")
    args = parser.parse_args()
    question = " ".join(args.question)

    docs_dir = resolve_docs(args.docs)
    collection, _ = get_collection(docs_dir)
    chunks = retrieve(collection, question)

    try:
        answer_text = answer(question, chunks)
    except anthropic.AuthenticationError:
        sys.exit("Authentication failed. Set the ANTHROPIC_API_KEY environment variable.")
    except anthropic.NotFoundError as e:
        sys.exit(f"Model or endpoint not found ({MODEL}): {e.message}")
    except anthropic.RateLimitError:
        sys.exit("Rate limited by the API. Wait a moment and try again.")
    except anthropic.APIStatusError as e:
        sys.exit(f"API error {e.status_code}: {e.message}")
    except anthropic.APIConnectionError:
        sys.exit("Could not reach the Anthropic API. Check your internet connection.")

    output = format_output(chunks, answer_text)
    print(output, end="")

    if args.save:
        save_path = Path(args.save)
        save_path.parent.mkdir(parents=True, exist_ok=True)
        save_path.write_text(output, encoding="utf-8")
        print(f"\nSaved to {save_path.resolve()}", file=sys.stderr)


if __name__ == "__main__":
    main()
