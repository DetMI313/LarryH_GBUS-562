"""
Orchestrator coordinator (Week 5).

Prepares one company's response to one supplier email, but here Claude decides which
specialists to use. An orchestrator agent, with the system prompt in the company folder's
prompts/orchestrator.txt, gets four tools:

  run_supply_analyst      runs the supply analyst on the email; returns its brief
  draft_supplier_reply    supplier writer + review step; needs the brief
  draft_customer_notice   customer writer + review step for one order at risk; needs the brief
  finish                  a summary for a person, and whether a person must make a decision

Drafts go through the same review step, name checks, and flags as in pipeline.py, and are
saved in week-05/drafts/<company folder name>/orchestrator-<email>/. The orchestrator
can't send anything. It stops after 10 tool calls (finish included). Each choice it makes
is printed, with the analyst's brief in full and each final draft once, then the same
summary as the pipeline.

--org names the company folder (a path, or a folder name in week-05) holding the CSVs,
supplier-emails/, and prompts/. Without it: week-05/brookfield-bikes.

Usage (from the repository folder):
  .venv/Scripts/python week-05/orchestrator.py --email week-05/brookfield-bikes/supplier-emails/01-northgate-controller-delay.eml
  .venv/Scripts/python week-05/orchestrator.py --org week-05/copperline-coffee --email week-05/copperline-coffee/supplier-emails/01-danforth-pump-delay.eml --save week-05/runs/copperline-01-orchestrator.txt

--save also writes everything printed to the file you name (UTF-8).
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path

import anthropic

import specialists as sp
from pipeline import add_common_args, print_summary, start
from specialists import OUT

NAME = "orchestrator"
MAX_TOOL_CALLS = 10


def tool(name: str, description: str, properties: dict, required: list[str]) -> dict:
    return {
        "name": name,
        "description": description,
        "input_schema": {"type": "object", "properties": properties,
                         "required": required, "additionalProperties": False},
        "strict": True,
    }


def build_tools(company: str) -> list[dict]:
    return [
        tool("run_supply_analyst",
             "Run the supply analyst on the supplier email. Returns its brief (JSON): what changed, "
             "the orders at risk, any options, decisions for a person, and supplier questions. "
             "The draft tools need this brief.", {}, []),
        tool("draft_supplier_reply",
             "Have the supplier writer draft a reply to the supplier from the analyst's brief. The draft "
             "is reviewed and saved for a person to check. Returns the outcome and the draft.", {}, []),
        tool("draft_customer_notice",
             "Have the customer writer draft a notice to the customer of one order at risk (an order_id "
             "from the brief's orders_at_risk). The draft is reviewed and saved for a person to check. "
             "Returns the outcome and the draft.",
             {"order_id": {"type": "string", "description": "An order_id from the brief's orders_at_risk"}},
             ["order_id"]),
        tool("finish",
             "End the job. Call this once, as your last step.",
             {"summary": {"type": "string",
                          "description": f"A short summary for {company}'s purchasing manager"},
              "needs_decision": {"type": "boolean",
                                 "description": "True if a person must make a decision"}},
             ["summary", "needs_decision"]),
    ]


class Job:
    """What the orchestrator has done so far, and the tool implementations."""

    def __init__(self, email: dict):
        self.email = email
        self.run_name = f"orchestrator-{email['file']}"
        self.brief: dict | None = None
        self.drafts: list[sp.Draft] = []
        self.supplier_reply_done = False
        self.noticed: set[str] = set()
        self.finished: dict | None = None

    def draft_result(self, d: sp.Draft) -> str:
        status = "approved by the reviewer" if d.approved else "NOT approved by the reviewer"
        flag = f"Marked for a person: {'; '.join(d.reasons)}." if d.needs_person else "Not marked for a person."
        return (f"Draft to the {d.recipient} saved ({status}, {d.revisions} revision(s)). {flag}\n"
                f"<draft>\n{d.text}\n</draft>")

    async def call(self, name: str, args: dict) -> tuple[str, bool]:
        """Run one tool. Returns (result text, is_error)."""
        if name == "run_supply_analyst":
            if self.brief is not None:
                return "The analyst has already run. Its brief:\n" + sp.as_text(self.brief), False
            self.brief = await sp.run_analyst(self.email)
            return sp.as_text(self.brief), False

        if name == "draft_supplier_reply":
            if self.brief is None:
                return "Run the supply analyst first: the supplier writer needs its brief.", True
            if self.supplier_reply_done:
                return "The supplier reply has already been drafted.", True
            self.supplier_reply_done = True
            d = await sp.prepare_supplier_reply(self.email, self.brief, self.run_name)
            if d is None:
                return "The supplier writer says this email needs no reply. Nothing was saved.", False
            self.drafts.append(d)
            return self.draft_result(d), False

        if name == "draft_customer_notice":
            order_id = str(args.get("order_id", "")).strip().upper()
            if self.brief is None:
                return "Run the supply analyst first: the customer writer needs its brief.", True
            if order_id in self.noticed:
                return f"A notice for {order_id} has already been drafted.", True
            d = await sp.prepare_customer_notice(self.brief, order_id, self.run_name)  # raises if not at risk
            self.noticed.add(order_id)
            self.drafts.append(d)
            return self.draft_result(d), False

        if name == "finish":
            self.finished = args
            return "Finished.", False

        return f"Unknown tool: {name}", True


async def run(email: dict) -> Job:
    job = Job(email)
    system = sp.load_prompt("orchestrator")
    tools = build_tools(sp.ORG.short_name)
    messages = [{
        "role": "user",
        "content": (
            f"Coordinate {sp.ORG.short_name}'s response to this supplier email. You have at most "
            f"{MAX_TOOL_CALLS} tool calls, finish included.\n\n" + sp.email_block(email)
        ),
    }]
    calls = 0

    while calls < MAX_TOOL_CALLS and job.finished is None:
        response = await sp.client().messages.create(
            model=sp.MODEL, max_tokens=sp.MAX_TOKENS, system=system, tools=tools, messages=messages,
        )
        sp.report_tokens(NAME, *sp.count_tokens(NAME, response))
        sp.check_stop(NAME, response)
        messages.append({"role": "assistant", "content": response.content})

        thoughts = sp.text_of(response)
        if thoughts:
            OUT(f"{NAME}: {thoughts}")
        tool_uses = [b for b in response.content if b.type == "tool_use"]
        if not tool_uses:
            OUT(f"{NAME} stopped without calling finish.")
            break

        results = []
        for call in tool_uses:
            if calls >= MAX_TOOL_CALLS or job.finished is not None:
                results.append({"type": "tool_result", "tool_use_id": call.id, "is_error": True,
                                "content": "Not run: the job has ended."})
                continue
            calls += 1
            OUT()
            OUT(f"CHOICE {calls}: {call.name}({json.dumps(call.input, ensure_ascii=False) if call.input else ''})")
            try:
                text, is_error = await job.call(call.name, call.input)
            except sp.SpecialistError as e:
                text, is_error = str(e), True
            if is_error:
                OUT(f"    ERROR: {text}")
            results.append({"type": "tool_result", "tool_use_id": call.id,
                            "content": text, "is_error": is_error})
        messages.append({"role": "user", "content": results})

    if job.finished is None and calls >= MAX_TOOL_CALLS:
        OUT(f"Stopped: the tool call limit ({MAX_TOOL_CALLS}) was reached before finish.")

    print_summary(job.drafts, job.brief or {})
    OUT("Orchestrator's summary for a person:")
    if job.finished is None:
        OUT("  (none: the orchestrator didn't call finish)")
    else:
        OUT(sp.indent(job.finished["summary"]))
        OUT(f"Person must make a decision: {'YES' if job.finished['needs_decision'] else 'no'}")
    if job.brief is None:
        OUT("(The supply analyst wasn't run.)")
    return job


def main():
    parser = argparse.ArgumentParser(description="Prepare a company's response to one supplier email (orchestrator agent).")
    add_common_args(parser)
    args = parser.parse_args()
    email = start(args, "orchestrator agent")

    ok = False
    try:
        job = asyncio.run(run(email))
        ok = job.finished is not None
    except sp.SpecialistError as e:
        OUT(f"Stopped: {e}")
    except anthropic.AuthenticationError:
        OUT(f"Authentication failed. Check the key in {sp.KEY_FILE}.")
    except anthropic.RateLimitError:
        OUT("Rate limited by the API. Wait a minute and try again.")
    except anthropic.APIStatusError as e:
        OUT(f"API error {e.status_code}: {e.message}")
    except anthropic.APIConnectionError:
        OUT("Could not reach the API. Check your internet connection.")
    finally:
        if args.save:
            OUT.save(Path(args.save))
            print(f"(saved to {args.save})", file=sys.stderr)

    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
