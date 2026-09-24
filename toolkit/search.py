"""web_search — answers from the live web.

Primary path is Gemini with Google Search grounding, which returns a
written answer. If that fails (quota, network), a DuckDuckGo text search
supplies raw results instead.
"""

from __future__ import annotations

from toolkit import llm
from toolkit.base import ToolContext, register, S


def _duckduckgo(query: str, limit: int = 6) -> list[dict]:
    try:
        from ddgs import DDGS
    except ImportError:  # older package name
        from duckduckgo_search import DDGS
    with DDGS() as ddg:
        return list(ddg.text(query, max_results=limit))


def _render_hits(hits: list[dict]) -> str:
    lines = []
    for n, hit in enumerate(hits, 1):
        title = hit.get("title") or hit.get("href") or "result"
        lines.append(f"{n}. {title} — {hit.get('body', '').strip()} ({hit.get('href', '')})")
    return "\n".join(lines)


def lookup(question: str) -> str:
    try:
        return llm.ask(question, search=True)
    except Exception as err:
        print(f"[search] grounded answer failed, using DuckDuckGo: {err}")
    hits = _duckduckgo(question)
    return _render_hits(hits) if hits else f"Nothing found for: {question}"


@register(
    "web_search",
    "Searches the web for any information. Set mode to 'compare' with a list of "
    "items to compare them on one aspect.",
    {
        "query": S("What to search for"),
        "mode": S("search (default) or compare"),
        "items": {"type": "ARRAY", "items": {"type": "STRING"}, "description": "Things to compare"},
        "aspect": S("What to compare them on, e.g. price, specs, reviews"),
    },
    ["query"],
)
def web_search(args: dict, ctx: ToolContext) -> str:
    query = (args.get("query") or "").strip()
    items = [i for i in (args.get("items") or []) if str(i).strip()]
    aspect = (args.get("aspect") or "").strip() or "overall"
    if not query and not items:
        return "What should I search for?"

    if items:
        question = (f"Compare {', '.join(items)} on {aspect}. Use current facts and "
                    f"concrete numbers, and end with a one-line verdict.")
        ctx.log(f"[Search] compare {', '.join(items)} ({aspect})")
    else:
        question = query
        ctx.log(f"[Search] {query}")

    try:
        return lookup(question)
    except Exception as err:
        return f"The search didn't work: {err}"
