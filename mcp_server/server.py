from __future__ import annotations

import asyncio
import logging
import sys
from typing import Any

import httpx
import mcp.types as types
from mcp.server import Server
from mcp.server.stdio import stdio_server

API_BASE_URL = "http://localhost:8000"

HEADERS = {
    "Accept": "application/json",
    "Content-Type": "application/json",
}

logger = logging.getLogger("scholargraph_mcp")
logging.basicConfig(
    level=logging.INFO,
    stream=sys.stderr,
    format="%(levelname)s:%(name)s:%(message)s",
)

app = Server("scholargraph")

_http_client: httpx.AsyncClient | None = None


async def startup() -> None:
    global _http_client

    if _http_client is None:
        _http_client = httpx.AsyncClient(
            base_url=API_BASE_URL,
            headers=HEADERS,
            timeout=httpx.Timeout(30.0),
        )
        logger.info("ScholarGraph MCP server connected to API base URL: %s", API_BASE_URL)


async def shutdown() -> None:
    global _http_client

    if _http_client is not None:
        await _http_client.aclose()
        _http_client = None
        logger.info("ScholarGraph MCP server shut down cleanly")


def get_http_client() -> httpx.AsyncClient:
    if _http_client is None:
        raise RuntimeError("HTTP client is not initialised. Call startup() first.")
    return _http_client


async def api_get(path: str, params: dict[str, Any] | None = None) -> Any:
    client = get_http_client()
    response = await client.get(path, params=params)
    response.raise_for_status()
    return response.json()


def _extract_items(data: Any) -> list[dict[str, Any]]:
    """
    Handle both envelope-style responses ({items: [...]}) and raw lists.
    """
    if isinstance(data, dict):
        items = data.get("items", [])
        if isinstance(items, list):
            return items
        return []

    if isinstance(data, list):
        return data

    return []


async def _get_paper_authors(paper_id: str) -> str:
    """
    Fetch authors for one paper and return a readable comma-separated string.
    """
    try:
        data = await api_get(
            f"/papers/{paper_id}/authors",
            params={"page": 1, "size": 50},
        )
        items = _extract_items(data)

        if not items:
            return "Unknown"

        names: list[str] = []
        for item in items:
            name = item.get("name")
            if isinstance(name, str) and name.strip():
                names.append(name.strip())

        if not names:
            return "Unknown"

        if len(names) > 6:
            return ", ".join(names[:6]) + ", et al."

        return ", ".join(names)

    except Exception:
        logger.exception("Failed to fetch authors for paper %s", paper_id)
        return "Unknown"


async def _get_paper_citation_count(paper_id: str) -> int:
    """
    Fetch citation relations for one paper and count only incoming citations
    (direction == 'cited_by').

    This handles paginated citation responses conservatively.
    """
    total_cited_by = 0
    page = 1
    size = 100

    try:
        while True:
            data = await api_get(
                f"/papers/{paper_id}/citations",
                params={"page": page, "size": size},
            )
            items = _extract_items(data)

            if not items:
                break

            for item in items:
                if item.get("direction") == "cited_by":
                    total_cited_by += 1

            if len(items) < size:
                break

            page += 1

        return total_cited_by

    except Exception:
        logger.exception("Failed to fetch citation count for paper %s", paper_id)
        return 0


async def _search_papers(arguments: dict[str, Any]) -> list[types.TextContent]:
    query = str(arguments["query"]).strip()
    limit = int(arguments.get("limit", 10))
    category = arguments.get("category")

    params: dict[str, Any] = {
        "q": query,
        "limit": limit,
    }
    if category:
        params["category"] = category

    data = await api_get("/papers/search/semantic", params=params)
    papers = _extract_items(data)

    if not papers:
        return [
            types.TextContent(
                type="text",
                text=f"No papers found matching '{query}'.",
            )
        ]

    author_tasks = [
        _get_paper_authors(str(paper["id"]))
        for paper in papers
        if paper.get("id") is not None
    ]
    authors_list = await asyncio.gather(*author_tasks)

    lines = [f"Found {len(papers)} papers matching '{query}':", ""]

    for idx, (paper, authors) in enumerate(zip(papers, authors_list, strict=False), start=1):
        title = paper.get("title", "Untitled")
        arxiv_id = paper.get("arxiv_id", "N/A")
        pagerank_score = paper.get("pagerank_score")
        pagerank_text = (
            f"{float(pagerank_score):.6f}"
            if pagerank_score is not None
            else "N/A"
        )

        lines.append(f"{idx}. {title}")
        lines.append(f"   Authors: {authors}")
        lines.append(f"   PageRank: {pagerank_text}")
        lines.append(f"   URL: https://arxiv.org/abs/{arxiv_id}")
        lines.append("")

    return [types.TextContent(type="text", text="\n".join(lines).strip())]


async def _get_top_papers(arguments: dict[str, Any]) -> list[types.TextContent]:
    category = str(arguments["category"]).strip()
    limit = int(arguments.get("limit", 10))

    data = await api_get(
        "/papers/ranked",
        params={"category": category, "limit": limit},
    )
    papers = _extract_items(data)

    if not papers:
        return [
            types.TextContent(
                type="text",
                text=f"No ranked papers found for category '{category}'.",
            )
        ]

    citation_tasks = [
        _get_paper_citation_count(str(paper["id"]))
        for paper in papers
        if paper.get("id") is not None
    ]
    citation_counts = await asyncio.gather(*citation_tasks)

    lines = [f"Top {len(papers)} most influential papers in {category} by PageRank:", ""]

    for idx, (paper, citation_count) in enumerate(zip(papers, citation_counts, strict=False), start=1):
        title = paper.get("title", "Untitled")
        arxiv_id = paper.get("arxiv_id", "N/A")
        pagerank_score = paper.get("pagerank_score")
        pagerank_text = (
            f"{float(pagerank_score):.6f}"
            if pagerank_score is not None
            else "N/A"
        )

        lines.append(f"{idx}. {title}")
        lines.append(f"   PageRank: {pagerank_text}")
        lines.append(f"   Citations received: {citation_count}")
        lines.append(f"   URL: https://arxiv.org/abs/{arxiv_id}")
        lines.append("")

    return [types.TextContent(type="text", text="\n".join(lines).strip())]


async def _resolve_paper_by_arxiv_id(arxiv_id: str) -> dict[str, Any] | None:
    """
    Resolve an arXiv ID to a paper object using the /papers endpoint.

    Since /papers?search=... may behave like text search, we fetch a small page
    and then try to find an exact arxiv_id match first.
    """
    data = await api_get(
        "/papers",
        params={"search": arxiv_id, "page": 1, "size": 10},
    )
    items = _extract_items(data)

    if not items:
        return None

    normalized = arxiv_id.strip()
    for item in items:
        if str(item.get("arxiv_id", "")).strip() == normalized:
            return item

    return items[0]


async def _get_paper_details(arguments: dict[str, Any]) -> list[types.TextContent]:
    arxiv_id = str(arguments["arxiv_id"]).strip()

    resolved = await _resolve_paper_by_arxiv_id(arxiv_id)
    if resolved is None:
        return [
            types.TextContent(
                type="text",
                text=f"Paper '{arxiv_id}' not found in ScholarGraph.",
            )
        ]

    paper_id = str(resolved["id"])

    paper = await api_get(f"/papers/{paper_id}")
    authors = await _get_paper_authors(paper_id)
    citation_count = await _get_paper_citation_count(paper_id)

    title = paper.get("title", "Untitled")
    resolved_arxiv_id = paper.get("arxiv_id", arxiv_id)
    pagerank_score = paper.get("pagerank_score")
    pagerank_text = (
        f"{float(pagerank_score):.6f}"
        if pagerank_score is not None
        else "N/A"
    )

    abstract = str(paper.get("abstract") or "No abstract available.")
    abstract_excerpt = abstract[:300] + ("..." if len(abstract) > 300 else "")

    primary_category = paper.get("primary_category")
    all_categories = paper.get("all_categories") or []

    if isinstance(all_categories, list):
        category_values = [str(c) for c in all_categories if c]
    else:
        category_values = []

    if primary_category and primary_category not in category_values:
        category_values.insert(0, str(primary_category))

    categories_text = ", ".join(category_values) if category_values else "N/A"

    text = (
        f"Title: {title}\n"
        f"arXiv ID: {resolved_arxiv_id}\n"
        f"Authors: {authors}\n"
        f"Categories: {categories_text}\n"
        f"PageRank score: {pagerank_text}\n"
        f"Citations received: {citation_count}\n"
        f"URL: https://arxiv.org/abs/{resolved_arxiv_id}\n\n"
        f"Abstract excerpt: {abstract_excerpt}"
    )

    return [types.TextContent(type="text", text=text)]


async def _find_similar_papers(arguments: dict[str, Any]) -> list[types.TextContent]:
    arxiv_id = str(arguments["arxiv_id"]).strip()
    limit = int(arguments.get("limit", 5))

    resolved = await _resolve_paper_by_arxiv_id(arxiv_id)
    if resolved is None:
        return [
            types.TextContent(
                type="text",
                text=f"Paper '{arxiv_id}' not found in ScholarGraph.",
            )
        ]

    paper_id = str(resolved["id"])
    source_title = str(resolved.get("title", "Unknown paper"))

    data = await api_get(
        f"/papers/{paper_id}/similar",
        params={"limit": limit},
    )
    items = _extract_items(data)

    if not items:
        return [
            types.TextContent(
                type="text",
                text=f"No similar papers found for '{source_title}'.",
            )
        ]

    lines = [f"Papers semantically similar to '{source_title}':", ""]

    for idx, paper in enumerate(items, start=1):
        title = paper.get("title", "Untitled")
        paper_arxiv_id = paper.get("arxiv_id", "N/A")
        similarity_score = paper.get("similarity_score")
        similarity_text = (
            f"{float(similarity_score):.4f}"
            if similarity_score is not None
            else "N/A"
        )

        lines.append(f"{idx}. {title}")
        lines.append(f"   Similarity: {similarity_text}")
        lines.append(f"   URL: https://arxiv.org/abs/{paper_arxiv_id}")
        lines.append("")

    return [types.TextContent(type="text", text="\n".join(lines).strip())]


def _normalise_name(value: str) -> str:
    return " ".join(value.strip().lower().split())


async def _resolve_author_by_name(author_name: str) -> dict[str, Any] | None:
    """
    Resolve an author by name using the /authors endpoint.

    This does client-side matching so it still works even if the API does not
    support a dedicated search parameter.
    """
    target = _normalise_name(author_name)

    page = 1
    size = 100
    partial_match: dict[str, Any] | None = None

    while True:
        data = await api_get("/authors", params={"page": page, "size": size})
        items = _extract_items(data)

        if not items:
            break

        for item in items:
            name = str(item.get("name", ""))
            norm_name = _normalise_name(name)

            if norm_name == target:
                return item

            if partial_match is None and target in norm_name:
                partial_match = item

        if len(items) < size:
            break

        page += 1

    return partial_match


async def _get_author_impact(arguments: dict[str, Any]) -> list[types.TextContent]:
    author_name = str(arguments["author_name"]).strip()

    author = await _resolve_author_by_name(author_name)
    if author is None:
        return [
            types.TextContent(
                type="text",
                text=f"Author '{author_name}' not found in ScholarGraph.",
            )
        ]

    author_id = str(author["id"])
    impact = await api_get(f"/authors/{author_id}/impact")

    name = impact.get("name", author.get("name", author_name))
    total_papers = impact.get("total_papers", "N/A")
    total_citations = impact.get("total_citations_received", impact.get("total_citations", "N/A"))
    avg_pagerank = impact.get("avg_pagerank_score", impact.get("avg_pagerank", None))

    avg_pagerank_text = (
        f"{float(avg_pagerank):.6f}"
        if avg_pagerank is not None
        else "N/A"
    )

    top_papers = impact.get("top_papers", []) or []
    if top_papers:
        top_paper = top_papers[0]
        top_paper_title = top_paper.get("title", "Unknown")
        top_paper_arxiv = top_paper.get("arxiv_id", "N/A")
        top_paper_text = f"{top_paper_title} (https://arxiv.org/abs/{top_paper_arxiv})"
    else:
        top_paper_text = "N/A"

    text = (
        f"Author: {name}\n"
        f"Total Papers: {total_papers}\n"
        f"Total Citations: {total_citations}\n"
        f"Average PageRank: {avg_pagerank_text}\n"
        f"Top Paper: {top_paper_text}"
    )

    return [types.TextContent(type="text", text=text)]


@app.list_tools()
async def list_tools() -> list[types.Tool]:
    return [
        types.Tool(
            name="search_papers",
            description=(
                "Search for research papers by topic or concept using semantic similarity. "
                "Use this when the user asks to find papers about a subject."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The search query, e.g. 'transformer attention mechanism'",
                    },
                    "category": {
                        "type": "string",
                        "description": "Optional arXiv category filter, e.g. 'cs.AI'",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Number of results to return (default 10)",
                        "default": 10,
                        "minimum": 1,
                        "maximum": 100,
                    },
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        ),
        types.Tool(
            name="get_top_papers",
            description=(
                "Get the most influential papers in an arXiv category ranked by PageRank. "
                "Use this when the user asks for the most important, most influential, or "
                "top papers in a field."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "category": {
                        "type": "string",
                        "description": "arXiv category, e.g. 'cs.AI', 'cs.LG', 'cs.CV'",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Number of papers to return (default 10)",
                        "default": 10,
                        "minimum": 1,
                        "maximum": 100,
                    },
                },
                "required": ["category"],
                "additionalProperties": False,
            },
        ),
        types.Tool(
            name="get_paper_details",
            description=(
                "Get full details about a specific paper by arXiv ID, including title, "
                "authors, categories, PageRank score, citation count, and an abstract excerpt."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "arxiv_id": {
                        "type": "string",
                        "description": "The arXiv ID of the paper, e.g. '1706.03762'",
                    },
                },
                "required": ["arxiv_id"],
                "additionalProperties": False,
            },
        ),
        types.Tool(
            name="find_similar_papers",
            description=(
                "Find papers semantically similar to a given paper using vector similarity search. "
                "Use this when the user wants recommended reading related to a known paper."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "arxiv_id": {
                        "type": "string",
                        "description": "The arXiv ID of the source paper, e.g. '1706.03762'",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Number of similar papers to return (default 5)",
                        "default": 5,
                        "minimum": 1,
                        "maximum": 100,
                    },
                },
                "required": ["arxiv_id"],
                "additionalProperties": False,
            },
        ),
        types.Tool(
            name="get_author_impact",
            description=(
                "Get a citation and influence overview for an author by name. "
                "Use this when the user asks about a researcher's impact, top paper, "
                "or overall influence."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "author_name": {
                        "type": "string",
                        "description": "The author's name, e.g. 'Yann LeCun'",
                    },
                },
                "required": ["author_name"],
                "additionalProperties": False,
            },
        ),
    ]


@app.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> list[types.TextContent]:
    arguments = arguments or {}

    try:
        if name == "search_papers":
            return await _search_papers(arguments)

        if name == "get_top_papers":
            return await _get_top_papers(arguments)

        if name == "get_paper_details":
            return await _get_paper_details(arguments)

        if name == "find_similar_papers":
            return await _find_similar_papers(arguments)

        if name == "get_author_impact":
            return await _get_author_impact(arguments)

        return [
            types.TextContent(
                type="text",
                text=f"Unknown tool: {name}",
            )
        ]

    except httpx.HTTPStatusError as exc:
        logger.exception("ScholarGraph API HTTP error during tool call: %s", name)
        return [
            types.TextContent(
                type="text",
                text=(
                    f"ScholarGraph API request failed.\n"
                    f"Tool: {name}\n"
                    f"Status: {exc.response.status_code}\n"
                    f"Response: {exc.response.text}"
                ),
            )
        ]
    except Exception as exc:
        logger.exception("Unhandled MCP tool error: %s", name)
        return [
            types.TextContent(
                type="text",
                text=f"Tool execution failed: {exc}",
            )
        ]


async def main() -> None:
    await startup()
    try:
        async with stdio_server() as (read_stream, write_stream):
            await app.run(
                read_stream,
                write_stream,
                app.create_initialization_options(),
            )
    finally:
        await shutdown()


if __name__ == "__main__":
    asyncio.run(main())
