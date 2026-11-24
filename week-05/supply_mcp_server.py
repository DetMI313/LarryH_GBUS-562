"""
Supply data MCP server (Week 5).

Reads one company's CSV files and offers three tools to any MCP-compatible app (Claude
Code, or the Week 5 agents):

  get_part             A part's stock on hand, its scheduled shipments, and the products
                       that use it.
  get_orders_for_part  The open orders that need a part: parts needed (units ordered x parts
                       per unit), ship day, and late penalty, in ship-day order.
  check_coverage       Takes a part and a new list of arrivals (quantity and day), which
                       replaces that part's scheduled shipments. Fills orders complete, in
                       ship-day order, so a short order holds up the ones after it. For each
                       order: covered, or units short and the first day it can ship.

Days are counted from today (day 0), matching the arrival day in inbound.csv and the ship
day in orders.csv. Units short are counted in finished products, not parts.

The company folder (--org; default: week-05/brookfield-bikes) holds parts.csv, inbound.csv,
bill-of-materials.csv, and orders.csv. Column names may differ between companies: see
org_data.py for how each file's header row is read. The CSVs are read again on every tool
call, so edits take effect without restarting the server.

Usage (normally started by an MCP client, which talks to it over stdin/stdout):
  python week-05/supply_mcp_server.py
  python week-05/supply_mcp_server.py --org week-05/copperline-coffee

Log messages go to stderr; stdout is reserved for the MCP protocol.
"""

import argparse
import math
import sys
from typing import Annotated

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import BaseModel, Field

from org_data import SERVER_FILES, Org, OrgError, resolve_org


def log(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


# ---------- Data ----------

class SupplyData:
    """The four CSV files, read fresh for each tool call, under org_data's internal names."""

    def __init__(self, org: Org):
        self.org = org

    def _rows(self, name: str) -> list[dict]:
        try:
            return self.org.table(name)
        except OrgError as e:
            raise ToolError(str(e)) from None

    def part(self, part_id: str) -> dict:
        part_id = part_id.strip().upper()
        rows = self._rows("parts.csv")
        for row in rows:
            if row["part_id"].upper() == part_id:
                return row
        known = ", ".join(r["part_id"] for r in rows)
        raise ToolError(f"Unknown part_id: {part_id}. Known parts: {known}")  # sent to the model as an error

    def shipments(self, part_id: str) -> list[dict]:
        rows = [r for r in self._rows("inbound.csv") if r["part_id"].upper() == part_id]
        return sorted(
            (
                {"po_number": r["po_number"], "supplier": r["supplier"],
                 "qty": int(r["qty"]), "arrives_day": int(r["arrives_day"])}
                for r in rows
            ),
            key=lambda s: s["arrives_day"],
        )

    def products_using(self, part_id: str) -> dict[str, int]:
        """Product name -> parts per unit."""
        return {
            r["product"]: int(r["per_unit"])
            for r in self._rows("bill-of-materials.csv")
            if r["part_id"].upper() == part_id
        }

    def orders_for(self, part_id: str) -> list[dict]:
        per_unit = self.products_using(part_id)
        orders = []
        for r in self._rows("orders.csv"):
            if r["product"] not in per_unit:
                continue
            quantity = int(r["quantity"])
            orders.append({
                "order_id": r["order_id"],
                "customer": r["customer"],
                "product": r["product"],
                "quantity": quantity,
                "parts_per_unit": per_unit[r["product"]],
                "parts_needed": quantity * per_unit[r["product"]],
                "ship_day": int(r["ship_day"]),
                "late_penalty": r["late_penalty"].lower() in ("yes", "true", "y", "1"),
            })
        return sorted(orders, key=lambda o: (o["ship_day"], o["order_id"]))


# ---------- Coverage ----------

def coverage(on_hand: int, arrivals: list[tuple[int, int]], orders: list[dict]) -> list[dict]:
    """Fill orders complete, in ship-day order, from stock on hand plus arrivals.

    An order can ship once everything up to and including it has been filled, so a short
    order holds up the ones after it. Its first ship day is the first day on which the
    running supply covers the running demand. Supply by a day counts arrivals on that day.
    """
    # (day, total supply available by that day), starting with stock on hand today.
    steps = [(0, on_hand)]
    total = on_hand
    for day, qty in sorted(arrivals):
        total += qty
        if day == steps[-1][0]:
            steps[-1] = (day, total)
        else:
            steps.append((day, total))

    def supply_by(day: int) -> int:
        return max((s for d, s in steps if d <= day), default=0)

    def first_day_with(needed: int) -> int | None:
        return next((d for d, s in steps if s >= needed), None)

    results = []
    demand = 0  # running demand in parts, this order included
    for o in orders:
        demand += o["parts_needed"]
        ready = first_day_with(demand)
        result = {
            "order_id": o["order_id"],
            "customer": o["customer"],
            "product": o["product"],
            "quantity": o["quantity"],
            "ship_day": o["ship_day"],
            "late_penalty": o["late_penalty"],
        }
        if ready is not None and ready <= o["ship_day"]:
            result["status"] = "covered"
        else:
            # Parts still missing for this order on its ship day (it may be partly filled).
            parts_short = min(o["parts_needed"], demand - supply_by(o["ship_day"]))
            result.update({
                "status": "short",
                "units_short": math.ceil(parts_short / o["parts_per_unit"]),  # in finished units
                "parts_short": parts_short,
                "first_ship_day": ready,  # None: these arrivals never cover it
                "days_late": None if ready is None else ready - o["ship_day"],
            })
        results.append(result)
    return results


# ---------- Server ----------

class Arrival(BaseModel):
    qty: int = Field(ge=0, description="Number of parts in this shipment")
    day: int = Field(ge=0, description="Day it arrives, counted from today (today is day 0)")


def build_server(data: SupplyData) -> MCPServer:
    server = MCPServer("supply-tools", log_level="WARNING")

    @server.tool(
        description=(
            "Get one part's stock on hand, its supplier, its scheduled shipments (with arrival "
            "day counted from today, day 0), and the products that use it with parts per unit."
        )
    )
    def get_part(part_id: Annotated[str, Field(description="Part ID, as in parts.csv")]) -> dict:
        part = data.part(part_id)
        pid = part["part_id"]
        return {
            "part_id": pid,
            "description": part["description"],
            "supplier": part["supplier"],
            "on_hand": int(part["on_hand"]),
            "scheduled_shipments": data.shipments(pid),
            "products_using": [
                {"product": p, "parts_per_unit": n} for p, n in data.products_using(pid).items()
            ],
        }

    @server.tool(
        description=(
            "List the open orders that need a part, in ship-day order: customer, product, "
            "quantity (finished units ordered), parts per unit, parts needed (quantity times "
            "parts per unit), ship day counted from today (day 0), and whether the order has a "
            "late penalty."
        )
    )
    def get_orders_for_part(
        part_id: Annotated[str, Field(description="Part ID, as in parts.csv")],
    ) -> dict:
        pid = data.part(part_id)["part_id"]
        return {"part_id": pid, "orders": data.orders_for(pid)}

    @server.tool(
        description=(
            "Check whether a part's supply covers its open orders under a new arrival schedule. "
            "The arrivals you give REPLACE the part's scheduled shipments; stock on hand is added "
            "automatically. Orders are filled complete in ship-day order, so a short order holds "
            "up the ones after it. For each order returns status 'covered', or 'short' with "
            "units_short (in finished units), first_ship_day, and days_late. Days are counted "
            "from today (day 0)."
        )
    )
    def check_coverage(
        part_id: Annotated[str, Field(description="Part ID, as in parts.csv")],
        arrivals: Annotated[
            list[Arrival],
            Field(description="Every shipment of this part still to arrive, each with qty and day"),
        ],
    ) -> dict:
        part = data.part(part_id)
        pid = part["part_id"]
        on_hand = int(part["on_hand"])
        results = coverage(on_hand, [(a.day, a.qty) for a in arrivals], data.orders_for(pid))
        short = [r for r in results if r["status"] == "short"]
        return {
            "part_id": pid,
            "on_hand": on_hand,
            "arrivals": sorted(({"qty": a.qty, "day": a.day} for a in arrivals), key=lambda a: a["day"]),
            "orders": results,
            "orders_short": [r["order_id"] for r in short],
        }

    return server


def main():
    parser = argparse.ArgumentParser(description="MCP server offering one company's supply data tools.")
    parser.add_argument("--org", metavar="FOLDER",
                        help="Company folder holding the CSV files: a path, or a folder name in "
                             "week-05 (default: week-05/brookfield-bikes)")
    parser.add_argument("--data", dest="org", help=argparse.SUPPRESS)  # the earlier name for --org
    args = parser.parse_args()

    try:
        org = Org(resolve_org(args.org, required=SERVER_FILES, prompts=False))
        for name in SERVER_FILES:  # fail now, not on the first tool call, if a column can't be found
            org.columns(name)
    except OrgError as e:
        sys.exit(str(e))
    log(f"supply-tools: {org.folder}")

    build_server(SupplyData(org)).run()  # stdio transport


if __name__ == "__main__":
    main()
