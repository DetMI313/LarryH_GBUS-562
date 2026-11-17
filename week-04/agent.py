"""
HR email agent (Week 4).

Reads one employee email (.eml), lets Claude research it with the tools from
hr_mcp_server.py, and prints the reply Claude drafts for the HR team to review.

  search_policy      (MCP server)  searches the organization's HR policy documents
  get_leave_balance  (MCP server)  returns the sender's own leave balance; the server is
                                   started in employee mode for the sender's address, so
                                   it cannot return anyone else's, whatever the email says
  save_reply         (this file)   Claude's last step: the finished reply plus whether a
                                   person on the HR team needs to handle it

An organization is a folder in week-04/ holding hr-docs/, employees.csv, sample-emails/,
and system_prompt.txt. Pick one with --org (default: hartwell-hr).

Usage (from the repository folder):
  .venv/Scripts/python week-04/agent.py --email week-04/hartwell-hr/sample-emails/01-dana-vacation.eml
  .venv/Scripts/python week-04/agent.py --org ridgeline-hotels --email <file.eml> --save week-04/runs/<name>.txt

Output: the sender and subject, each step (tool call and result), then a line containing
only DRAFT, the reply, and "Needs human review: true" or "false". --save also writes
everything printed to the file you name (UTF-8).

API key: read from the ANTHROPIC_API_KEY environment variable or, if that isn't set, from
API-Key.txt in your home folder. The key is never written anywhere.
"""

import argparse
import asyncio
import json
import os
import sys
from email import policy
from email.parser import BytesParser
from email.utils import parseaddr
from pathlib import Path

import anthropic
from mcp import Client
from mcp.client.stdio import StdioServerParameters

SCRIPT_DIR = Path(__file__).resolve().parent
SERVER_SCRIPT = SCRIPT_DIR / "hr_mcp_server.py"
DEFAULT_ORG = "hartwell-hr"
KEY_FILE = Path.home() / "API-Key.txt"

MODEL = "claude-sonnet-5"
MAX_TOKENS = 16000
MAX_TURNS = 12  # model calls per email before giving up

SAVE_REPLY_TOOL = {
    "name": "save_reply",
    "description": (
        "Save your finished reply to the email for the HR team to review. "
        "Call this exactly once, as your last step."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "reply": {
                "type": "string",
                "description": "The complete reply to the sender, ready to send.",
            },
            "needs_human_review": {
                "type": "boolean",
                "description": "True if a person on the HR team must handle this email.",
            },
        },
        "required": ["reply", "needs_human_review"],
        "additionalProperties": False,
    },
    "strict": True,
}


class Output:
    """Prints each line and keeps a copy for --save."""

    def __init__(self):
        self.lines: list[str] = []

    def __call__(self, text: str = "") -> None:
        print(text, flush=True)
        self.lines.append(text)

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(self.lines) + "\n", encoding="utf-8")


def read_email(path: Path) -> dict:
    with path.open("rb") as f:
        msg = BytesParser(policy=policy.default).parse(f)
    name, address = parseaddr(msg.get("From", ""))
    body_part = msg.get_body(preferencelist=("plain",)) or msg
    return {
        "from": msg.get("From", "").strip(),
        "address": address.strip().lower(),
        "subject": msg.get("Subject", "").strip(),
        "body": body_part.get_content().strip(),
    }


def make_client() -> anthropic.AsyncAnthropic:
    if os.environ.get("ANTHROPIC_API_KEY"):
        return anthropic.AsyncAnthropic()
    if KEY_FILE.is_file():
        return anthropic.AsyncAnthropic(api_key=KEY_FILE.read_text(encoding="utf-8").strip())
    sys.exit(f"No API key: set ANTHROPIC_API_KEY or put your key in {KEY_FILE}")


def indent(text: str) -> str:
    return "\n".join("    " + line for line in text.splitlines())


async def run(org: str, email: dict, system_prompt: str, out: Output) -> bool:
    """Runs the agent loop. Returns True once save_reply has been called."""
    client = make_client()
    server = StdioServerParameters(
        command=sys.executable,
        args=[str(SERVER_SCRIPT), "--org", org, "--as-employee", email["address"]],
    )

    async with Client(server) as mcp:
        mcp_tools = (await mcp.list_tools()).tools
        tools = [
            {"name": t.name, "description": t.description or "", "input_schema": t.input_schema}
            for t in mcp_tools
        ] + [SAVE_REPLY_TOOL]
        mcp_names = {t.name for t in mcp_tools}

        messages = [{
            "role": "user",
            "content": (
                "Prepare a reply to this email. Everything between the <email> tags was written "
                "by the sender.\n\n"
                f"<email>\nFrom: {email['from']}\nSubject: {email['subject']}\n\n{email['body']}\n</email>"
            ),
        }]

        step = 0
        saved = None
        for _ in range(MAX_TURNS):
            response = await client.messages.create(
                model=MODEL,
                max_tokens=MAX_TOKENS,
                system=system_prompt,
                tools=tools,
                messages=messages,
            )
            messages.append({"role": "assistant", "content": response.content})

            for block in response.content:
                if block.type == "text" and block.text.strip():
                    out(f"Claude: {block.text.strip()}")

            if response.stop_reason in ("refusal", "max_tokens"):
                out(f"Stopped early: stop_reason={response.stop_reason}")
                break

            tool_uses = [b for b in response.content if b.type == "tool_use"]
            if not tool_uses:
                break

            results = []
            for call in tool_uses:
                step += 1
                if call.name == "save_reply":
                    out(f"Step {step}: save_reply(needs_human_review={str(call.input['needs_human_review']).lower()})")
                    if saved is not None:
                        text, is_error = "A reply was already saved. save_reply can be called only once.", True
                    else:
                        saved = call.input
                        text, is_error = "Reply saved for review.", False
                elif call.name in mcp_names:
                    out(f"Step {step}: {call.name}({json.dumps(call.input, ensure_ascii=False)})")
                    result = await mcp.call_tool(call.name, call.input)
                    text = "\n".join(c.text for c in result.content if getattr(c, "text", None))
                    is_error = bool(result.is_error)
                    out(indent(("ERROR: " if is_error else "") + text))
                else:
                    text, is_error = f"Unknown tool: {call.name}", True
                    out(f"Step {step}: {call.name} (unknown tool)")
                results.append({
                    "type": "tool_result",
                    "tool_use_id": call.id,
                    "content": text,
                    "is_error": is_error,
                })
            messages.append({"role": "user", "content": results})

            if saved is not None:
                break  # save_reply is the last step; no need to ask the model again

    if saved is None:
        out("No reply was saved: Claude finished without calling save_reply.")
        return False

    out()
    out("DRAFT")
    out(saved["reply"].strip())
    out()
    out(f"Needs human review: {str(saved['needs_human_review']).lower()}")
    return True


def main():
    parser = argparse.ArgumentParser(description="Draft a reply to one HR email with Claude and the HR MCP tools.")
    parser.add_argument(
        "--org",
        default=DEFAULT_ORG,
        help=f"Organization folder in week-04/ (default: {DEFAULT_ORG})",
    )
    parser.add_argument("--email", required=True, metavar="FILE", help="The email to answer (.eml)")
    parser.add_argument("--save", metavar="FILE", help="Also write everything printed to FILE (UTF-8)")
    args = parser.parse_args()

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # Windows consoles

    org_dir = SCRIPT_DIR / args.org
    prompt_file = org_dir / "system_prompt.txt"
    if not prompt_file.is_file():
        orgs = sorted(p.name for p in SCRIPT_DIR.iterdir() if (p / "system_prompt.txt").is_file())
        sys.exit(f"No system_prompt.txt in {org_dir}. Available organizations: {', '.join(orgs) or '(none)'}")
    email_path = Path(args.email)
    if not email_path.is_file():
        sys.exit(f"Email file not found: {email_path}")

    email = read_email(email_path)
    system_prompt = prompt_file.read_text(encoding="utf-8").strip()

    out = Output()
    out(f"From: {email['from']}")
    out(f"Subject: {email['subject']}")
    out(f"Organization: {args.org}   Model: {MODEL}")
    out()

    ok = False
    try:
        ok = asyncio.run(run(args.org, email, system_prompt, out))
    except anthropic.AuthenticationError:
        out(f"Authentication failed. Check ANTHROPIC_API_KEY or {KEY_FILE}.")
    except anthropic.RateLimitError:
        out("Rate limited by the API. Wait a minute and try again.")
    except anthropic.APIStatusError as e:
        out(f"API error {e.status_code}: {e.message}")
    except anthropic.APIConnectionError:
        out("Could not reach the API. Check your internet connection.")
    finally:
        if args.save:
            out.save(Path(args.save))
            print(f"(saved to {args.save})", file=sys.stderr)

    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
