"""
Fixed pipeline coordinator (Week 5).

Prepares one company's response to one supplier email by running the specialists from
specialists.py in the same order every time:

  1. The supply analyst (its brief is printed in full).
  2. A customer notice for each order at risk (skipped if there are none).
  3. A reply to the supplier.

Every draft goes through the review step, is printed once as a FINAL DRAFT, and is saved in
week-05/drafts/<company folder name>/pipeline-<email>/. At the end it prints the drafts
saved, the drafts marked for a person, the decisions for a person, and the total tokens.

--org names the company folder (a path, or a folder name in week-05) holding the CSVs,
supplier-emails/, and prompts/. Without it: week-05/brookfield-bikes.

Usage (from the repository folder):
  .venv/Scripts/python week-05/pipeline.py --email week-05/brookfield-bikes/supplier-emails/01-northgate-controller-delay.eml
  .venv/Scripts/python week-05/pipeline.py --org week-05/copperline-coffee --email week-05/copperline-coffee/supplier-emails/01-danforth-pump-delay.eml --save week-05/runs/copperline-01-pipeline.txt

--save also writes everything printed to the file you name (UTF-8).
"""

import argparse
import asyncio
import sys
from pathlib import Path

import anthropic

import specialists as sp
from specialists import OUT


async def run(email: dict) -> list[sp.Draft]:
    run_name = f"pipeline-{email['file']}"
    drafts: list[sp.Draft] = []

    OUT("STEP 1: supply analyst")
    brief = await sp.run_analyst(email)

    OUT()
    at_risk = [o.get("order_id") for o in brief.get("orders_at_risk", []) if o.get("order_id")]
    if at_risk:
        OUT(f"STEP 2: customer notices for {len(at_risk)} order(s) at risk: {', '.join(at_risk)}")
        for order_id in at_risk:
            drafts.append(await sp.prepare_customer_notice(brief, order_id, run_name))
    else:
        OUT("STEP 2: skipped, no orders at risk")

    OUT()
    OUT("STEP 3: reply to the supplier")
    reply = await sp.prepare_supplier_reply(email, brief, run_name)
    if reply is None:
        OUT("    no reply needed, nothing saved")
    else:
        drafts.append(reply)

    print_summary(drafts, brief)
    return drafts


def print_summary(drafts: list[sp.Draft], brief: dict) -> None:
    rel = sp.rel
    OUT()
    OUT("=" * 70)
    OUT("SUMMARY")
    OUT(f"Drafts saved: {len(drafts)}")
    for d in drafts:
        OUT(f"  {rel(d.path)}")
    flagged = [d for d in drafts if d.needs_person]
    OUT(f"Drafts marked for a person: {len(flagged)}")
    for d in flagged:
        OUT(f"  {rel(d.path)}: {'; '.join(d.reasons)}")
    decisions = brief.get("decisions_for_a_person") or []
    OUT(f"Decisions for a person: {len(decisions)}")
    for item in decisions:
        OUT(f"  - {item if isinstance(item, str) else sp.as_text(item)}")
    OUT(sp.token_summary())


def add_common_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--org", metavar="FOLDER",
                        help="Company folder with the CSVs, supplier-emails/, and prompts/: a path, "
                             "or a folder name in week-05 (default: week-05/brookfield-bikes)")
    parser.add_argument("--email", required=True, metavar="FILE", help="The supplier email (.eml)")
    parser.add_argument("--save", metavar="FILE", help="Also write everything printed to FILE (UTF-8)")


def start(args, coordinator: str) -> dict:
    """Choose the company, read the email, and print the run's header. Returns the email."""
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # Windows consoles
    try:
        org = sp.set_org(args.org)
    except sp.OrgError as e:
        sys.exit(str(e))

    email_path = Path(args.email)
    if not email_path.is_file():
        sys.exit(f"Email file not found: {email_path}")
    email = sp.read_email(email_path)

    OUT(f"Company: {org.short_name}   Folder: {sp.rel(org.folder)}")
    OUT("Columns read from the CSVs (internal name=header in the file):")
    OUT(org.describe())
    OUT(f"From: {email['from']}")
    OUT(f"Subject: {email['subject']}")
    OUT(f"Coordinator: {coordinator}   Model: {sp.MODEL}")
    OUT()
    return email


def main():
    parser = argparse.ArgumentParser(description="Prepare a company's response to one supplier email (fixed pipeline).")
    add_common_args(parser)
    args = parser.parse_args()
    email = start(args, "fixed pipeline")

    ok = False
    try:
        asyncio.run(run(email))
        ok = True
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
