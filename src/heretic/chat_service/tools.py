# SPDX-License-Identifier: AGPL-3.0-or-later
import ast
import datetime
import json
import math
import operator
import re
from pathlib import Path

import httpx


def schema(name, description, properties, required):
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
                "additionalProperties": False,
            },
        },
    }


TOOL_SCHEMAS = [
    schema(
        "calculate",
        "Evaluate an arithmetic expression accurately.",
        {"expression": {"type": "string"}},
        ["expression"],
    ),
    schema("current_time", "Get the current UTC and Japan time.", {}, []),
    schema(
        "search_wikipedia",
        "Search public Wikipedia articles for factual background and return source links.",
        {
            "query": {"type": "string"},
            "language": {"type": "string", "enum": ["en", "ja", "zh"]},
        },
        ["query"],
    ),
    schema(
        "search_reports",
        "Read measured local prompt-intervention comparison results.",
        {"query": {"type": "string"}},
        ["query"],
    ),
]


def calculate(expression: str) -> float:
    if not isinstance(expression, str) or len(expression) > 160:
        raise ValueError("Expression must be a string of at most 160 characters.")
    operations = {
        ast.Add: operator.add,
        ast.Sub: operator.sub,
        ast.Mult: operator.mul,
        ast.Div: operator.truediv,
        ast.Mod: operator.mod,
        ast.Pow: operator.pow,
    }
    tree = ast.parse(expression, mode="eval")
    if len(list(ast.walk(tree))) > 50:
        raise ValueError("Expression is too complex.")

    def visit(node):
        if isinstance(node, ast.Constant) and type(node.value) in {float, int}:
            value = float(node.value)
        elif isinstance(node, ast.UnaryOp) and type(node.op) in {ast.USub, ast.UAdd}:
            operand = visit(node.operand)
            value = -operand if isinstance(node.op, ast.USub) else operand
        elif isinstance(node, ast.BinOp) and type(node.op) in operations:
            left, right = visit(node.left), visit(node.right)
            if isinstance(node.op, ast.Pow) and (abs(right) > 12 or abs(left) > 1e6):
                raise ValueError("Exponentiation exceeds the calculator limit.")
            value = operations[type(node.op)](left, right)
        else:
            raise ValueError("Only numeric arithmetic is supported.")
        if (
            not isinstance(value, (int, float))
            or not math.isfinite(value)
            or abs(value) > 1e100
        ):
            raise ValueError("Result exceeds the calculator limit.")
        return value

    return visit(tree.body)


def execute_tool(name: str, arguments: dict, report_root: Path) -> dict:
    if not isinstance(arguments, dict):
        raise TypeError("Tool arguments must be an object.")
    definitions = {
        s["function"]["name"]: s["function"]["parameters"] for s in TOOL_SCHEMAS
    }
    if name not in definitions:
        raise ValueError("Tool is not allowed.")
    definition = definitions[name]
    if set(arguments) - set(definition["properties"]) or set(
        definition["required"]
    ) - set(arguments):
        raise ValueError("Invalid tool arguments.")
    try:
        if name == "calculate":
            return {
                "expression": arguments["expression"],
                "value": calculate(arguments["expression"]),
            }
        if name == "current_time":
            now = datetime.datetime.now(datetime.timezone.utc)
            return {
                "utc": now.isoformat(),
                "japan": now.astimezone(
                    datetime.timezone(datetime.timedelta(hours=9))
                ).isoformat(),
            }
        query = arguments["query"]
        if not isinstance(query, str) or not 1 <= len(query) <= 200:
            raise ValueError("Query must contain 1–200 characters.")
        if name == "search_reports":
            report = json.loads((report_root / "comparison.json").read_text())
            terms = re.findall(r"[\w-]+", query.casefold())
            ranked = []
            for index, row in enumerate(report["comparisons"]):
                text = json.dumps(row, ensure_ascii=False).casefold()
                score = 2 * (query.casefold() in text) + sum(t in text for t in terms)
                if score:
                    ranked.append((-score, index, row))
            matches = [row for _, _, row in sorted(ranked)]
            # Bound tool context independently of report size and response length.
            excerpts = [
                {
                    "id": row["id"],
                    "method": row["method"],
                    "prompt": row["prompt"][:200],
                    "before_excerpt": row["before"][:240],
                    "after_excerpt": row["after"][:240],
                    "automatic_outcome": row["outcome"],
                    "review_excerpt": row.get("assistant_review", "")[:160],
                }
                for row in matches[:3]
            ]
            return {
                "matches": excerpts,
                "total": len(matches),
                "source": "/reports/comparison.html",
            }
        language = arguments.get("language", "en")
        if language not in {"en", "ja", "zh"}:
            raise ValueError("Unsupported encyclopedia language.")
        response = httpx.get(
            f"https://{language}.wikipedia.org/w/api.php",
            params={
                "action": "query",
                "list": "search",
                "srsearch": query,
                "srlimit": 3,
                "format": "json",
                "utf8": 1,
            },
            headers={"User-Agent": "BreakLLM-Research/1.0 (local research assistant)"},
            timeout=10,
            follow_redirects=False,
        )
        response.raise_for_status()
        results = response.json()["query"]["search"]
        return {
            "results": [
                {
                    "title": r["title"],
                    "snippet": r["snippet"],
                    "url": f"https://{language}.wikipedia.org/?curid={r['pageid']}",
                }
                for r in results
            ]
        }
    except (ArithmeticError, SyntaxError, OSError, httpx.HTTPError, KeyError) as error:
        return {"error": str(error)[:300]}
