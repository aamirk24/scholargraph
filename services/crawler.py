from __future__ import annotations

import asyncio
import logging
import os
import random
import re
import unicodedata
from datetime import date, datetime
from typing import Any

import httpx
import xmltodict
from sqlalchemy import delete, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from models.author import Author, PaperAuthor
from models.citation import Citation
from models.paper import Paper

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

ARXIV_API_URL = "https://export.arxiv.org/api/query"
USER_AGENT = (
    "ScholarGraph/0.1 (COMP3011 academic project; "
    "contact: sc23a4k@leeds.ac.uk)"
)

SEMANTIC_SCHOLAR_API_URL = "https://api.semanticscholar.org/graph/v1"
SEMANTIC_SCHOLAR_API_KEY = os.getenv("SEMANTIC_SCHOLAR_API_KEY")
SEMANTIC_SCHOLAR_USER_AGENT = (
    "ScholarGraph/0.1 (COMP3011 academic project; "
    "contact: sc23a4k@leeds.ac.uk)"
)


# ─────────────────────────────────────────────────────────────────────────────
# 1. ARXIV HTTP CLIENT
# ─────────────────────────────────────────────────────────────────────────────

class ArxivClient:
    """
    Async HTTP client for the arXiv Atom API.

    Handles:
    - polite rate limiting (3 seconds between requests)
    - retry logic with exponential backoff (3 attempts)
    """

    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None
        self._last_request_time: float | None = None

    async def __aenter__(self) -> "ArxivClient":
        self._client = httpx.AsyncClient(
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "application/atom+xml, application/xml;q=0.9, */*;q=0.8",
            },
            timeout=httpx.Timeout(30.0),
        )
        return self

    async def __aexit__(self, *_) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def _respect_rate_limit(self) -> None:
        loop = asyncio.get_running_loop()
        now = loop.time()
        if self._last_request_time is not None:
            elapsed = now - self._last_request_time
            wait_time = max(0.0, 3.0 - elapsed)
            if wait_time > 0:
                await asyncio.sleep(wait_time)
        self._last_request_time = loop.time()

    async def _get_with_retry(self, params: dict[str, Any]) -> str:
        """Shared GET + retry logic used by both fetch methods."""
        if self._client is None:
            raise RuntimeError(
                "ArxivClient must be used as an async context manager."
            )

        max_attempts = 3
        base_delay   = 5.0

        for attempt in range(1, max_attempts + 1):
            try:
                await self._respect_rate_limit()
                logger.info("arXiv GET | params=%r attempt=%d", params, attempt)
                response = await self._client.get(ARXIV_API_URL, params=params)
                response.raise_for_status()
                return response.text

            except (httpx.HTTPStatusError, httpx.RequestError) as exc:
                logger.warning("arXiv request failed on attempt %d: %s", attempt, exc)
                if attempt == max_attempts:
                    raise
                delay = base_delay * (2 ** (attempt - 1))
                logger.info("Retrying in %.0f seconds...", delay)
                await asyncio.sleep(delay)

        raise RuntimeError("Unreachable: retries exhausted")

    async def fetch_papers(
        self,
        query: str,
        start: int = 0,
        max_results: int = 100,
    ) -> str:
        """Fetch papers by search query (e.g. cat:cs.AI)."""
        params = {
            "search_query": query,
            "start": start,
            "max_results": max_results,
            "sortBy": "relevance",
            "sortOrder": "descending",
        }
        return await self._get_with_retry(params)

    async def fetch_by_ids(self, arxiv_ids: list[str]) -> str:
        """
        Fetch specific papers by arXiv ID using the id_list parameter.

        Used to retrieve individual papers by their known IDs, e.g. when
        seeding referenced papers that are not in the DB yet.
        arXiv allows up to 100 IDs per id_list request.
        """
        params = {
            "id_list": ",".join(arxiv_ids),
            "max_results": len(arxiv_ids),
        }
        return await self._get_with_retry(params)


# ─────────────────────────────────────────────────────────────────────────────
# 2. XML PARSING HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _ensure_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return " ".join(value.split()).strip()
    return str(value).strip()


def _normalise_author_name(raw: str) -> str:
    normalised = unicodedata.normalize("NFD", raw)
    ascii_only = normalised.encode("ascii", "ignore").decode("ascii")
    return re.sub(r"\s+", " ", ascii_only).strip().lower()


def _extract_pdf_url(links: list[dict] | dict | None) -> str | None:
    if links is None:
        return None
    if isinstance(links, dict):
        links = [links]
    for link in links:
        if not isinstance(link, dict):
            continue
        if link.get("@type") == "application/pdf":
            return link.get("@href")
        if link.get("@title") == "pdf":
            return link.get("@href")
    return None


def _extract_categories(entry: dict[str, Any]) -> tuple[str | None, list[str]]:
    primary_category: str | None = None

    primary_raw = entry.get("arxiv:primary_category")
    if isinstance(primary_raw, dict):
        primary_category = primary_raw.get("@term")

    categories_raw = _ensure_list(entry.get("category", []))
    all_categories: list[str] = []
    for category in categories_raw:
        if isinstance(category, dict):
            term = category.get("@term")
            if term:
                all_categories.append(term)

    if primary_category is None and all_categories:
        primary_category = all_categories[0]

    return primary_category, all_categories


# ─────────────────────────────────────────────────────────────────────────────
# 3. XML PARSER
# ─────────────────────────────────────────────────────────────────────────────

def parse_papers(xml_string: str) -> list[dict[str, Any]]:
    """
    Parse an arXiv Atom XML response into a list of paper dicts.

    Each dict contains:
        arxiv_id, title, abstract, authors (list[str]),
        published (date | None), updated (datetime | None),
        primary_category (str | None), all_categories (list[str]),
        pdf_url (str | None)
    """
    try:
        parsed = xmltodict.parse(xml_string)
    except Exception as exc:
        logger.error("Failed to parse arXiv XML: %s", exc)
        return []

    feed    = parsed.get("feed", {})
    entries = _ensure_list(feed.get("entry"))

    if not entries:
        logger.info("arXiv response contained 0 entries.")
        return []

    papers: list[dict[str, Any]] = []

    for entry in entries:
        if not isinstance(entry, dict):
            continue
        try:
            raw_id   = _clean_text(entry.get("id"))
            arxiv_id = raw_id.split("/abs/")[-1]
            arxiv_id = re.sub(r"v\d+$", "", arxiv_id).strip()

            if not arxiv_id:
                logger.warning(
                    "Skipping entry with no parseable arXiv ID: %r", raw_id
                )
                continue

            title    = _clean_text(entry.get("title"))
            abstract = _clean_text(entry.get("summary"))

            authors_raw = _ensure_list(entry.get("author"))
            authors: list[str] = [
                _clean_text(author.get("name"))
                for author in authors_raw
                if isinstance(author, dict) and author.get("name")
            ]

            published_raw = _clean_text(entry.get("published"))
            updated_raw   = _clean_text(entry.get("updated"))

            published: date | None = None
            updated: datetime | None = None

            if published_raw:
                try:
                    published = datetime.fromisoformat(
                        published_raw.replace("Z", "+00:00")
                    ).date()
                except ValueError:
                    pass

            if updated_raw:
                try:
                    updated = datetime.fromisoformat(
                        updated_raw.replace("Z", "+00:00")
                    )
                except ValueError:
                    pass

            primary_category, all_categories = _extract_categories(entry)
            pdf_url = _extract_pdf_url(entry.get("link"))

            papers.append({
                "arxiv_id":         arxiv_id,
                "title":            title,
                "abstract":         abstract,
                "authors":          authors,
                "published":        published,
                "updated":          updated,
                "primary_category": primary_category,
                "all_categories":   all_categories,
                "pdf_url":          pdf_url,
            })

        except Exception as exc:
            logger.warning("Skipping malformed entry: %s", exc, exc_info=True)
            continue

    logger.info("Parsed %d papers from XML.", len(papers))
    return papers


# ─────────────────────────────────────────────────────────────────────────────
# 4. DATABASE PERSISTENCE
# ─────────────────────────────────────────────────────────────────────────────

async def save_paper_to_db(
    db: AsyncSession,
    paper_dict: dict[str, Any],
) -> Paper:
    """
    Upsert a paper and its author associations into the database.

    - Existing paper: mutable fields are updated.
    - New paper: inserted fresh.
    - Authors are deduplicated by normalised name.
    - PaperAuthor rows are deleted and rebuilt on every upsert.

    Does NOT commit — caller controls the transaction.
    """
    arxiv_id: str = paper_dict["arxiv_id"]

    # Crawls for overlapping topics can encounter the same paper concurrently.
    # Serialize writes for that arXiv ID for the lifetime of this transaction.
    await db.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:arxiv_id, 0))"),
        {"arxiv_id": arxiv_id},
    )

    result = await db.execute(select(Paper).where(Paper.arxiv_id == arxiv_id))
    paper  = result.scalar_one_or_none()

    if paper is None:
        paper = Paper(arxiv_id=arxiv_id)
        db.add(paper)
        logger.debug("Inserting new paper: %s", arxiv_id)
    else:
        logger.debug("Updating existing paper: %s", arxiv_id)

    paper.title            = paper_dict["title"]
    paper.abstract         = paper_dict["abstract"]
    paper.published_date   = paper_dict["published"]
    paper.updated_date     = paper_dict["updated"]
    paper.primary_category = paper_dict["primary_category"]
    paper.all_categories   = paper_dict["all_categories"]
    paper.pdf_url          = paper_dict["pdf_url"]

    await db.flush()

    await db.execute(delete(PaperAuthor).where(PaperAuthor.paper_id == paper.id))
    await db.flush()

    linked_author_ids: set[Any] = set()
    position = 1

    for raw_name in paper_dict["authors"]:
        normalised = _normalise_author_name(raw_name)
        if not normalised:
            continue

        with db.no_autoflush:
            author_result = await db.execute(
                select(Author).where(Author.name_normalised == normalised)
            )
            author = author_result.scalar_one_or_none()

            if author is None:
                author = Author(
                    name=raw_name.strip(),
                    name_normalised=normalised,
                    arxiv_ids=None,
                )
                db.add(author)
                await db.flush()

        if author.id in linked_author_ids:
            continue

        db.add(PaperAuthor(
            paper_id=paper.id,
            author_id=author.id,
            position=position,
        ))
        linked_author_ids.add(author.id)
        position += 1

    await db.flush()
    await db.refresh(paper)
    return paper


# ─────────────────────────────────────────────────────────────────────────────
# 5. ARXIV CRAWL ORCHESTRATOR
# ─────────────────────────────────────────────────────────────────────────────

async def crawl_topic(
    db: AsyncSession,
    topic: str,
    max_papers: int = 200,
) -> dict[str, int]:
    """
    Crawl arXiv for a topic and persist papers in batches of 100.

    Commits after each batch so progress is preserved if the job is interrupted.
    After crawling all desired topics, call build_graph_for_all() to create
    citation edges across the full corpus.
    """
    batch_size    = 100
    total_fetched = 0
    inserted      = 0
    updated       = 0
    start         = 0

    logger.info("Starting crawl | topic=%r max_papers=%d", topic, max_papers)

    async with ArxivClient() as client:
        while total_fetched < max_papers:
            remaining  = max_papers - total_fetched
            this_batch = min(batch_size, remaining)

            try:
                xml = await client.fetch_papers(
                    query=topic,
                    start=start,
                    max_results=this_batch,
                )
            except (httpx.HTTPStatusError, httpx.RequestError) as exc:
                logger.error("Crawl aborted at offset %d: %s", start, exc)
                break

            papers = parse_papers(xml)

            if not papers:
                logger.info("No more papers at offset %d. Stopping.", start)
                break

            for paper_dict in papers:
                exists_result = await db.execute(
                    select(Paper.id).where(Paper.arxiv_id == paper_dict["arxiv_id"])
                )
                is_new = exists_result.scalar_one_or_none() is None
                await save_paper_to_db(db, paper_dict)
                if is_new:
                    inserted += 1
                else:
                    updated += 1

            await db.commit()

            batch_count    = len(papers)
            total_fetched += batch_count
            start         += batch_count

            logger.info(
                "Batch complete | offset=%d batch=%d total=%d inserted=%d updated=%d",
                start, batch_count, total_fetched, inserted, updated,
            )

            if batch_count < this_batch:
                logger.info("Partial batch — end of arXiv results reached.")
                break

    logger.info(
        "Crawl finished | topic=%r inserted=%d updated=%d total=%d",
        topic, inserted, updated, total_fetched,
    )
    return {"inserted": inserted, "updated": updated, "total_fetched": total_fetched}


# ─────────────────────────────────────────────────────────────────────────────
# 6. FOUNDATION SEEDING
# ─────────────────────────────────────────────────────────────────────────────

async def seed_foundations(
    db: AsyncSession,
    top_n: int = 150,
) -> dict[str, int]:
    """
    Identify the most-cited papers missing from the local corpus and crawl them.

    Strategy:
        1. Load all papers currently in the DB.
        2. Ask Semantic Scholar for their outgoing references (one batch call).
        3. Count how many local papers cite each external arXiv ID.
        4. Take the top_n most-cited external IDs not already in the DB.
        5. Fetch those papers from arXiv by id_list and insert them.

    This deliberately targets the foundational papers your corpus is built on
    (e.g. "Attention Is All You Need", BERT, ResNet) which recent papers cite
    heavily but which don't appear in a recency/relevance crawl. Adding them
    transforms a sparse graph into a connected one.

    Args:
        db:     Async SQLAlchemy session.
        top_n:  How many missing papers to seed. 150 papers = ~5 arXiv requests
                at 3 s each, so roughly 15–20 seconds of crawl time.

    Returns:
        {"candidates_found": N, "seeded": N, "already_in_db": N, "errors": N}
    """
    # Load the full corpus
    corpus_result = await db.execute(select(Paper))
    corpus_papers = corpus_result.scalars().all()
    total = len(corpus_papers)

    if total == 0:
        logger.info("seed_foundations: no papers in DB yet.")
        return {"candidates_found": 0, "seeded": 0, "already_in_db": 0, "errors": 0}

    logger.info(
        "seed_foundations: scanning %d corpus papers for missing cited foundations.",
        total,
    )

    corpus_lookup: dict[str, Paper] = {
        p.arxiv_id: p for p in corpus_papers if p.arxiv_id
    }
    corpus_ids = list(corpus_lookup.keys())

    # One Semantic Scholar batch call for the full corpus
    citation_counts: dict[str, int] = {}

    async with SemanticScholarClient() as client:
        for batch_start in range(0, len(corpus_ids), SemanticScholarClient.BATCH_SIZE):
            batch_ids = corpus_ids[
                batch_start : batch_start + SemanticScholarClient.BATCH_SIZE
            ]
            try:
                references_map = await client.fetch_references_batch(batch_ids)
            except Exception:
                logger.exception(
                    "seed_foundations: SS batch failed at offset %d", batch_start
                )
                continue

            for ref_list in references_map.values():
                for ref_id in ref_list:
                    # Only count papers NOT already in the corpus
                    if ref_id not in corpus_lookup:
                        citation_counts[ref_id] = citation_counts.get(ref_id, 0) + 1

    if not citation_counts:
        logger.info("seed_foundations: no external citations found.")
        return {"candidates_found": 0, "seeded": 0, "already_in_db": total, "errors": 0}

    # Sort by citation frequency descending, take top_n
    ranked = sorted(citation_counts.items(), key=lambda x: x[1], reverse=True)
    top_candidates = [arxiv_id for arxiv_id, _ in ranked[:top_n]]

    logger.info(
        "seed_foundations: top %d candidates | most cited: %s (cited by %d papers)",
        len(top_candidates),
        ranked[0][0],
        ranked[0][1],
    )

    # Double-check against DB in case something was added since we loaded corpus
    existing_result = await db.execute(
        select(Paper.arxiv_id).where(Paper.arxiv_id.in_(top_candidates))
    )
    already_in_db = set(existing_result.scalars().all())
    to_fetch = [aid for aid in top_candidates if aid not in already_in_db]

    logger.info(
        "seed_foundations: %d to fetch | %d already in DB",
        len(to_fetch), len(already_in_db),
    )

    seeded  = 0
    errors  = 0
    chunk_size = 100  # arXiv id_list limit per request

    async with ArxivClient() as client:
        for i in range(0, len(to_fetch), chunk_size):
            chunk = to_fetch[i : i + chunk_size]
            logger.info(
                "seed_foundations: fetching arXiv chunk %d-%d of %d",
                i + 1, i + len(chunk), len(to_fetch),
            )
            try:
                xml    = await client.fetch_by_ids(chunk)
                papers = parse_papers(xml)

                if not papers:
                    logger.warning(
                        "seed_foundations: arXiv returned 0 results for chunk at %d", i
                    )
                    continue

                for paper_dict in papers:
                    await save_paper_to_db(db, paper_dict)
                    seeded += 1

                await db.commit()
                logger.info(
                    "seed_foundations: chunk done | seeded_this_chunk=%d total_seeded=%d",
                    len(papers), seeded,
                )

            except Exception:
                await db.rollback()
                errors += 1
                logger.exception(
                    "seed_foundations: arXiv chunk failed at index %d", i
                )
                continue

    logger.info(
        "seed_foundations finished | candidates=%d seeded=%d already_in_db=%d errors=%d",
        len(top_candidates), seeded, len(already_in_db), errors,
    )
    return {
        "candidates_found": len(top_candidates),
        "seeded":           seeded,
        "already_in_db":    len(already_in_db),
        "errors":           errors,
    }


# ─────────────────────────────────────────────────────────────────────────────
# 7. SEMANTIC SCHOLAR CLIENT (BATCH ENDPOINT)
# ─────────────────────────────────────────────────────────────────────────────

class SemanticScholarClient:
    """
    Async client for Semantic Scholar.

    Uses the /paper/batch POST endpoint so references for up to 499 papers
    are fetched in one request instead of one request per paper.

    Rate limits:
        Without API key : 10 s minimum between requests
        With API key    : 1 s minimum between requests
    """

    BASE_URL   = SEMANTIC_SCHOLAR_API_URL
    BATCH_SIZE = 499

    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None
        self._last_request_time: float | None = None

    async def __aenter__(self) -> "SemanticScholarClient":
        headers = {
            "User-Agent":   SEMANTIC_SCHOLAR_USER_AGENT,
            "Accept":       "application/json",
            "Content-Type": "application/json",
        }
        if SEMANTIC_SCHOLAR_API_KEY:
            headers["x-api-key"] = SEMANTIC_SCHOLAR_API_KEY

        self._client = httpx.AsyncClient(
            headers=headers,
            timeout=httpx.Timeout(60.0),
        )
        return self

    async def __aexit__(self, *_) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def _respect_rate_limit(self) -> None:
        min_interval = 1.0 if SEMANTIC_SCHOLAR_API_KEY else 10.0
        loop = asyncio.get_running_loop()
        now  = loop.time()
        if self._last_request_time is not None:
            elapsed   = now - self._last_request_time
            wait_time = max(0.0, min_interval - elapsed)
            if wait_time > 0:
                await asyncio.sleep(wait_time)
        self._last_request_time = loop.time()

    async def _post_with_retry(
        self,
        url: str,
        payload: dict[str, Any],
        params: dict[str, Any],
    ) -> list[dict[str, Any]] | None:
        """POST with exponential backoff + jitter on 429 responses."""
        if self._client is None:
            raise RuntimeError("Must be used as an async context manager.")

        max_attempts = 4
        base_delay   = 15.0

        for attempt in range(1, max_attempts + 1):
            try:
                await self._respect_rate_limit()
                logger.info(
                    "Semantic Scholar batch POST | papers=%d attempt=%d",
                    len(payload.get("ids", [])), attempt,
                )
                response = await self._client.post(url, json=payload, params=params)
                response.raise_for_status()

                data = response.json()
                if isinstance(data, list):
                    return data

                logger.warning(
                    "Unexpected Semantic Scholar response type: %s", type(data)
                )
                return None

            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 429:
                    retry_after = exc.response.headers.get("Retry-After")
                    if retry_after:
                        try:
                            delay = float(retry_after)
                        except ValueError:
                            delay = base_delay * (2 ** (attempt - 1))
                    else:
                        delay = base_delay * (2 ** (attempt - 1))

                    jitter = delay * 0.3 * (random.random() * 2 - 1)
                    delay  = max(5.0, delay + jitter)

                    logger.warning(
                        "Semantic Scholar 429 on attempt %d. Waiting %.1f s.",
                        attempt, delay,
                    )
                    if attempt == max_attempts:
                        logger.error(
                            "Batch request failed after %d attempts.", max_attempts
                        )
                        return None
                    await asyncio.sleep(delay)
                    continue

                logger.error("Semantic Scholar HTTP error: %s", exc)
                return None

            except httpx.RequestError as exc:
                logger.warning(
                    "Semantic Scholar request error attempt %d: %s", attempt, exc
                )
                if attempt == max_attempts:
                    return None
                delay = base_delay * (2 ** (attempt - 1))
                await asyncio.sleep(delay)

        return None

    async def fetch_references_batch(
        self,
        arxiv_ids: list[str],
    ) -> dict[str, list[str]]:
        """
        Fetch outgoing references for a list of papers in one POST request.

        Returns:
            Dict mapping source_arxiv_id -> list of referenced arxiv_ids.
            Papers unknown to Semantic Scholar are absent from the dict.
        """
        if not arxiv_ids:
            return {}

        payload = {"ids": [f"arXiv:{aid}" for aid in arxiv_ids]}
        # references.externalIds is required — without the sub-field selector,
        # SS returns {"paperId": "..."} only, with no ArXiv ID to match on.
        params  = {"fields": "externalIds,references.externalIds"}
        url     = f"{self.BASE_URL}/paper/batch"

        data = await self._post_with_retry(url, payload, params)
        if data is None:
            return {}

        results: dict[str, list[str]] = {}

        for item in data:
            if not isinstance(item, dict):
                continue

            external_ids = item.get("externalIds") or {}
            if not isinstance(external_ids, dict):
                continue

            source_arxiv_id = external_ids.get("ArXiv")
            if not source_arxiv_id:
                continue
            source_arxiv_id = re.sub(r"v\d+$", "", source_arxiv_id.strip())

            references = item.get("references") or []
            if not isinstance(references, list):
                references = []

            ref_arxiv_ids: list[str] = []
            seen: set[str] = set()

            for ref in references:
                if not isinstance(ref, dict):
                    continue
                ref_ext   = ref.get("externalIds") or {}
                if not isinstance(ref_ext, dict):
                    continue
                ref_arxiv = ref_ext.get("ArXiv")
                if ref_arxiv:
                    cleaned = re.sub(r"v\d+$", "", ref_arxiv.strip())
                    if cleaned not in seen:
                        seen.add(cleaned)
                        ref_arxiv_ids.append(cleaned)

            results[source_arxiv_id] = ref_arxiv_ids

        logger.info(
            "Batch response parsed | requested=%d returned=%d",
            len(arxiv_ids), len(results),
        )
        return results





# ─────────────────────────────────────────────────────────────────────────────
# 8. SHARED EDGE-CREATION HELPER
# ─────────────────────────────────────────────────────────────────────────────

def _normalise_topic_label(topic: str) -> str:
    topic = topic.strip()
    if topic.startswith("cat:"):
        return topic.removeprefix("cat:")
    return topic


async def _create_edges_for_batch(
    db: AsyncSession,
    batch_ids: list[str],
    references_map: dict[str, list[str]],
    paper_lookup: dict[str, Paper],
    *,
    force_refresh: bool,
) -> tuple[int, int]:
    """
    Create Citation rows for one batch of papers.

    Shared by both build_graph_for_topic and build_graph_for_all so the
    edge-creation logic is not duplicated.

    Returns:
        (papers_processed, edges_created) for this batch.
    """
    if not references_map:
        return len(batch_ids), 0

    # Collect all referenced IDs in this batch and look them up in one query
    all_ref_ids: set[str] = set()
    for ref_ids in references_map.values():
        all_ref_ids.update(ref_ids)

    cited_lookup: dict[str, Paper] = {}
    if all_ref_ids:
        cited_result = await db.execute(
            select(Paper).where(Paper.arxiv_id.in_(list(all_ref_ids)))
        )
        cited_lookup = {p.arxiv_id: p for p in cited_result.scalars().all()}

    logger.info(
        "Overlap | unique_refs=%d | found_in_corpus=%d",
        len(all_ref_ids), len(cited_lookup),
    )

    source_papers = [paper_lookup[aid] for aid in batch_ids if aid in paper_lookup]
    source_ids    = [p.id for p in source_papers]

    # Delete existing edges first when force_refresh is set
    if force_refresh and source_ids:
        await db.execute(
            delete(Citation).where(Citation.citing_paper_id.in_(source_ids))
        )
        await db.flush()

    # Load existing edges to skip duplicates (only needed without force_refresh)
    existing_edges_by_source: dict[Any, set[Any]] = {
        sid: set() for sid in source_ids
    }
    if source_ids and not force_refresh:
        existing_result = await db.execute(
            select(Citation.citing_paper_id, Citation.cited_paper_id).where(
                Citation.citing_paper_id.in_(source_ids)
            )
        )
        for citing_id, cited_id in existing_result.all():
            existing_edges_by_source.setdefault(citing_id, set()).add(cited_id)

    batch_edges      = 0
    papers_processed = 0

    for arxiv_id in batch_ids:
        source_paper = paper_lookup.get(arxiv_id)
        if source_paper is None:
            papers_processed += 1
            continue

        existing_cited_ids = existing_edges_by_source.get(source_paper.id, set())
        ref_arxiv_ids      = references_map.get(arxiv_id, [])

        if not ref_arxiv_ids:
            papers_processed += 1
            continue

        for ref_arxiv_id in ref_arxiv_ids:
            cited = cited_lookup.get(ref_arxiv_id)
            if cited is None:
                continue  # not in corpus
            if cited.id == source_paper.id:
                continue  # self-citation
            if cited.id in existing_cited_ids:
                continue  # already exists

            db.add(Citation(
                citing_paper_id=source_paper.id,
                cited_paper_id=cited.id,
            ))
            existing_cited_ids.add(cited.id)
            batch_edges += 1

        papers_processed += 1

    return papers_processed, batch_edges


# ─────────────────────────────────────────────────────────────────────────────
# 9. CITATION GRAPH — PER-TOPIC (incremental refresh after a single topic crawl)
# ─────────────────────────────────────────────────────────────────────────────

async def build_graph_for_topic(
    db: AsyncSession,
    topic: str,
    *,
    force_refresh: bool = False,
) -> dict[str, int]:
    """
    Build citation edges for papers in a single topic that exist in the DB.

    Because citation edges are only created between papers already in the
    local corpus, cross-topic edges (e.g. cs.AI → cs.LG) will be missed if
    topics are built individually. Use build_graph_for_all() after crawling
    all topics for a complete graph.

    This function is useful for incremental updates after adding more papers
    to a single topic without re-running the full corpus build.
    """
    topic_label = _normalise_topic_label(topic)

    result = await db.execute(
        select(Paper).where(
            or_(
                Paper.primary_category == topic_label,
                Paper.all_categories.any(topic_label),
            )
        )
    )
    papers = result.scalars().all()
    total  = len(papers)

    if total == 0:
        logger.info("No papers found in DB for topic %r", topic_label)
        return {"papers_processed": 0, "edges_created": 0, "errors": 0}

    logger.info(
        "Starting per-topic graph build | topic=%r papers=%d",
        topic_label, total,
    )

    paper_lookup: dict[str, Paper] = {p.arxiv_id: p for p in papers if p.arxiv_id}
    arxiv_ids = list(paper_lookup.keys())

    papers_processed = 0
    edges_created    = 0
    errors           = 0

    async with SemanticScholarClient() as client:
        for batch_start in range(0, len(arxiv_ids), SemanticScholarClient.BATCH_SIZE):
            batch_ids = arxiv_ids[
                batch_start : batch_start + SemanticScholarClient.BATCH_SIZE
            ]

            logger.info(
                "SS batch | papers %d-%d of %d",
                batch_start + 1, batch_start + len(batch_ids), total,
            )

            try:
                references_map = await client.fetch_references_batch(batch_ids)
            except Exception:
                logger.exception("Batch fetch failed at offset %d", batch_start)
                errors += len(batch_ids)
                continue

            try:
                processed, batch_edges = await _create_edges_for_batch(
                    db,
                    batch_ids,
                    references_map,
                    paper_lookup,
                    force_refresh=force_refresh,
                )
                await db.flush()
                await db.commit()
            except Exception:
                await db.rollback()
                errors += len(batch_ids)
                logger.exception(
                    "Commit failed for topic batch at offset %d", batch_start
                )
                continue

            papers_processed += processed
            edges_created    += batch_edges

            logger.info(
                "Batch saved | processed=%d/%d edges_total=%d errors=%d",
                papers_processed, total, edges_created, errors,
            )

    logger.info(
        "Per-topic graph build finished | topic=%r processed=%d edges=%d errors=%d",
        topic_label, papers_processed, edges_created, errors,
    )
    return {
        "papers_processed": papers_processed,
        "edges_created":    edges_created,
        "errors":           errors,
    }


# ─────────────────────────────────────────────────────────────────────────────
# 10. CITATION GRAPH — FULL CORPUS (run this after crawling all topics)
# ─────────────────────────────────────────────────────────────────────────────

async def build_graph_for_all(
    db: AsyncSession,
    *,
    force_refresh: bool = False,
) -> dict[str, int]:
    """
    Build citation edges across the entire paper corpus in one pass.

    This is the correct function to call after crawling all topics. Because
    it searches the full corpus when resolving citations, cross-topic edges
    (e.g. a cs.AI paper citing a cs.LG paper) are captured correctly.

    Recommended workflow:
        POST /crawl  topic=cat:cs.AI   max_papers=300
        POST /crawl  topic=cat:cs.LG   max_papers=300
        POST /crawl  topic=cat:cs.CV   max_papers=300
        # wait for all three to finish, then:
        POST /crawl/build-graph-all

    Args:
        db:            Async SQLAlchemy session.
        force_refresh: Delete and recreate all Citation rows before rebuilding.
                       Use this when re-running after adding more papers.
    """
    result = await db.execute(select(Paper))
    papers = result.scalars().all()
    total  = len(papers)

    if total == 0:
        logger.info("No papers in DB.")
        return {"papers_processed": 0, "edges_created": 0, "errors": 0}

    logger.info(
        "Starting full-corpus graph build | total_papers=%d force_refresh=%s",
        total, force_refresh,
    )

    paper_lookup: dict[str, Paper] = {p.arxiv_id: p for p in papers if p.arxiv_id}
    arxiv_ids = list(paper_lookup.keys())

    papers_processed = 0
    edges_created    = 0
    errors           = 0

    async with SemanticScholarClient() as client:
        for batch_start in range(0, len(arxiv_ids), SemanticScholarClient.BATCH_SIZE):
            batch_ids = arxiv_ids[
                batch_start : batch_start + SemanticScholarClient.BATCH_SIZE
            ]

            logger.info(
                "SS batch | papers %d-%d of %d",
                batch_start + 1, batch_start + len(batch_ids), total,
            )

            try:
                references_map = await client.fetch_references_batch(batch_ids)
            except Exception:
                logger.exception("Batch fetch failed at offset %d", batch_start)
                errors += len(batch_ids)
                continue

            try:
                processed, batch_edges = await _create_edges_for_batch(
                    db,
                    batch_ids,
                    references_map,
                    paper_lookup,
                    force_refresh=force_refresh,
                )
                await db.flush()
                await db.commit()
            except Exception:
                await db.rollback()
                errors += len(batch_ids)
                logger.exception(
                    "Commit failed for full-corpus batch at offset %d", batch_start
                )
                continue

            papers_processed += processed
            edges_created    += batch_edges

            logger.info(
                "Batch saved | processed=%d/%d edges_total=%d errors=%d",
                papers_processed, total, edges_created, errors,
            )

    logger.info(
        "Full-corpus graph build finished | processed=%d edges=%d errors=%d",
        papers_processed, edges_created, errors,
    )
    return {
        "papers_processed": papers_processed,
        "edges_created":    edges_created,
        "errors":           errors,
    }
