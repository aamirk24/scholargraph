# ScholarGraph Engineering Case Study

## The problem

Keyword search is useful when a researcher already knows the terminology used
in a paper, but it is less effective for discovering conceptually related work.
Citation counts also provide only a partial view of influence because they do
not account for the structure of the citation network.

ScholarGraph was built as coursework to explore both problems through a single
backend API. It creates a research corpus from external scholarly APIs, supports
semantic discovery over abstracts, and analyses relationships between papers.

## The approach

The system combines two complementary views of the corpus:

- **Meaning:** Sentence Transformers converts abstracts into 384-dimensional
  vectors. PostgreSQL with pgvector ranks results using cosine distance.
- **Influence:** Semantic Scholar citation data forms a directed graph. A
  PageRank implementation scores papers while accounting for incoming links,
  outgoing-link counts, dangling nodes, and convergence.

FastAPI exposes these capabilities alongside metadata browsing, author
analytics, authentication, and user-owned annotations. arXiv supplies paper
metadata; Semantic Scholar supplies citation relationships. These external
services are data sources rather than work authored as part of ScholarGraph.

## Key engineering decisions

### Keep vector search beside relational data

pgvector keeps embeddings, paper metadata, authors, citations, and annotations
in one database. This avoids introducing a separate vector service for a corpus
of this size and allows category filters to be applied within the semantic
search query. A dedicated vector database may become appropriate at much larger
scale, but it would add operational complexity without improving this project.

### Use PageRank rather than raw citation counts

Raw counts treat every citation equally. PageRank also considers the influence
of the citing paper and redistributes score from papers with no outgoing edges.
The implementation uses a damping factor of `0.85`, stops when the maximum score
change falls below `0.0001`, and stores the resulting values for ranked queries
and topic analytics.

### Design ingestion around unreliable upstream services

External APIs can time out, throttle requests, or return overlapping records.
The crawler therefore applies polite request spacing and exponential retries,
honours numeric `Retry-After` guidance, and uses smaller batches for foundation
seeding. Database transaction locks keyed by arXiv ID prevent simultaneous topic
crawls from inserting the same paper twice.

### Isolate destructive integration tests

The test suite truncates database tables to keep tests independent. It refuses
to run unless `TEST_DATABASE_URL` contains the dedicated
`scholargraph_test` database name, reducing the risk of accidentally clearing a
development or production database. CI runs the Alembic migration chain before
the tests, so a passing build checks both installation and application behavior.

### Prefer portable CPU inference

The project installs CPU-only PyTorch because its target environments do not
provide GPUs. This keeps Codespaces and Docker setup reproducible and avoids
downloading unused CUDA libraries. Embeddings are generated in resumable
batches, but loading the model still exceeds the memory available on the
current 512 MB Render service. The conventional API can run there; embedding
generation is performed in Codespaces or another environment with at least
1 GB of memory.

## What changed after real usage

Running the full corpus workflow exposed issues that small isolated examples
did not: concurrent crawls collided on unique paper IDs, maximum-sized arXiv
requests were throttled, absolute response links leaked `localhost` through a
proxy, and the free deployment could not load the embedding model. The fixes
focused on those observed failures rather than adding speculative platform
features.

## Current boundaries

ScholarGraph is a portfolio-scale backend, not a multi-tenant production search
platform. Background work runs in the API process, the scheduler should be
enabled on only one instance, and embedding inference requires more memory than
the smallest deployment tier. If usage justified it, the next architectural
step would be a durable worker for crawl and embedding jobs—not additional
in-process orchestration.

