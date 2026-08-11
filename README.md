# ScholarGraph

![CI](https://github.com/aamirk24/scholargraph/actions/workflows/ci.yml/badge.svg)

An asynchronous research-intelligence API that combines semantic paper
discovery, citation-graph analytics, authenticated annotations, and MCP tooling
over an arXiv-derived corpus.

[Project overview](#project-overview) ·
[Engineering case study](docs/engineering-case-study.md) ·
[Quick start](#quick-start) ·
[API reference](#api-endpoints-reference) ·
[📄 API documentation (PDF)](./API_Documentation.pdf)

Built with FastAPI, PostgreSQL, pgvector, and sentence-transformers,
ScholarGraph supports vector similarity search, PageRank ranking, author-impact
analytics, and automated citation enrichment in a reproducible Docker-based
development environment.

---

## Table of Contents

- [Project Overview](#project-overview)
- [Demo](#demo)
- [Engineering Highlights](#engineering-highlights)
- [Engineering Case Study](docs/engineering-case-study.md)
- [Architecture Diagram](#architecture-diagram)
- [Quick Start](#quick-start)
- [Environment Variables](#environment-variables)
- [API Endpoints Reference](#api-endpoints-reference)
- [Example Requests](#example-requests)
- [MCP Server Setup](#mcp-server-setup)
- [Running Tests](#running-tests)
- [Deployment Notes](#deployment-notes)
- [Tech Stack](#tech-stack)
- [Author](#author)
- [License](#license)

---

## Project Overview

**ScholarGraph** is a backend API for exploring a corpus of academic research
papers in a more intelligent and research-friendly way than simple keyword
search.

It combines traditional paper metadata retrieval, semantic search using vector
embeddings, citation-graph analytics using PageRank, author impact summaries,
user-authenticated annotations, API-key lifecycle management, and MCP server
integration for AI-assisted workflows.

The system ingests and enriches research data from **arXiv** and
**Semantic Scholar**. arXiv provides the paper corpus and metadata, while
Semantic Scholar is used as a citation source for building and enriching the
citation graph.

## Demo

The example below runs semantic search over embedded paper abstracts and
returns the closest papers by pgvector cosine similarity.

![ScholarGraph semantic-search demonstration](docs/assets/semantic-search-demo.gif)

### API snapshots

| API surface | Semantic search |
|---|---|
| ![ScholarGraph OpenAPI endpoint overview](docs/assets/swagger-overview.png) | ![Semantic paper-search response](docs/assets/semantic-search.png) |

| PageRank results | Topic analytics |
|---|---|
| ![Papers ordered by normalized PageRank score](docs/assets/pagerank-results.png) | ![Paper counts and average PageRank by topic](docs/assets/topic-analytics.png) |

## Engineering Highlights

- **Hybrid research discovery:** combines structured metadata filters with
  384-dimensional vector similarity search over paper abstracts.
- **Graph-based relevance:** builds a citation network and computes PageRank to
  surface influential papers beyond keyword matching.
- **Asynchronous architecture:** uses FastAPI, async SQLAlchemy, and asyncpg for
  non-blocking API and database operations.
- **Reproducible development:** provides locked dependencies, Docker Compose,
  pgvector, a devcontainer, migrations, and CI for Python 3.12.
- **Safe integration testing:** isolates destructive database cleanup to a
  dedicated `scholargraph_test` database and refuses to run against other
  database names.
- **AI-client integration:** exposes the public paper-discovery workflows
  through a separate MCP server.

### Core capabilities

#### Paper Discovery

- List papers with pagination
- Filter papers by category
- Retrieve full paper metadata
- View authors for a paper
- View citations and references for a paper
- Run semantic search over paper abstracts
- Find papers similar to an existing paper

#### Research Analytics

- Rank papers by PageRank over the citation graph
- Analyse topic and category statistics
- View publication trends over time
- Summarise author impact and top papers

#### User Functionality

- User registration and JWT login
- Access-token refresh
- API-key generation and revocation
- Paper annotations with owner-controlled editing and deletion

#### Corpus Maintenance

- Crawl topic-specific papers
- Seed foundational missing papers
- Build citation graph edges for one topic or the full corpus
- Trigger background embedding generation
- Trigger background PageRank recomputation

#### AI and Tool Integration

- MCP server support
- MCP tools for semantic search, rankings, paper details and author impact
- Natural-language paper-discovery workflows for AI clients

---

## Architecture Diagram

```text
┌─────────────────────────────────────────────────────────────────────────────┐
│                                 CLIENTS                                     │
│─────────────────────────────────────────────────────────────────────────────│
│ Browser / Swagger UI / curl / Postman / Python scripts / MCP host / Claude │
└─────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      │ HTTP / JSON
                                      ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                               FASTAPI APP                                   │
│─────────────────────────────────────────────────────────────────────────────│
│ app.main                                                                    │
│ routers/                                                                    │
│   • auth                                                                    │
│   • papers                                                                  │
│   • authors                                                                 │
│   • annotations                                                             │
│   • analytics                                                               │
│   • crawl                                                                   │
└───────────────────────────────┬─────────────────────────────────────────────┘
                                │
                ┌───────────────┴────────────────┐
                │                                │
                ▼                                ▼
┌────────────────────────────┐      ┌────────────────────────────────────────┐
│         CRUD LAYER         │      │             SERVICE LAYER              │
│────────────────────────────│      │────────────────────────────────────────│
│ SQLAlchemy async queries   │      │ auth / JWT / API keys                  │
│ papers / authors / cites   │      │ embeddings / semantic search           │
│ users / annotations        │      │ PageRank / crawler / background jobs   │
└───────────────┬────────────┘      └──────────────────────┬─────────────────┘
                │                                          │
                └───────────────────┬──────────────────────┘
                                    ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                          POSTGRESQL + PGVECTOR                              │
│─────────────────────────────────────────────────────────────────────────────│
│ papers / authors / paper_authors / citations / users / api_keys             │
│ annotations / abstract_embedding vectors                                    │
└─────────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                       EXTERNAL RESEARCH SOURCES                             │
│─────────────────────────────────────────────────────────────────────────────│
│ arXiv API / Semantic Scholar API / background corpus enrichment             │
└─────────────────────────────────────────────────────────────────────────────┘
```

The optional MCP server runs as a separate process and calls the FastAPI API's
public paper and author read endpoints over HTTP.

---

## Quick Start

### GitHub Codespaces

Open the repository in GitHub Codespaces.

The committed devcontainer automatically:

- installs Python 3.12;
- installs `uv`;
- installs PostgreSQL client tools including `psql`;
- starts PostgreSQL 16 with pgvector;
- creates `scholargraph`;
- creates `scholargraph_test`;
- enables the `vector` extension in both databases;
- installs the locked Python dependencies;
- uses CPU-only PyTorch;
- runs the application database migrations.

After the Codespace finishes building, start the API:

```bash
make dev
```

Run the tests:

```bash
make test
```

### Local Development with Docker

Requirements:

- Docker
- Docker Compose
- Python 3.12 or later
- `uv`

Clone the project:

```bash
git clone https://github.com/aamirk24/scholargraph.git
cd scholargraph
```

Create the local environment file:

```bash
cp .env.example .env
```

Install dependencies, start PostgreSQL and run migrations:

```bash
make setup
```

Start the API:

```bash
make dev
```

The API will be available at:

```text
http://127.0.0.1:8000
```

Swagger UI:

```text
http://127.0.0.1:8000/docs
```

PostgreSQL and pgvector do not need to be manually installed on the host
machine.

---

## Environment Variables

Codespaces supplies local development variables automatically.

For manual local development:

```bash
cp .env.example .env
```

| Variable | Required | Purpose |
|---|---:|---|
| `DATABASE_URL` | Yes | Application PostgreSQL URL |
| `TEST_DATABASE_URL` | Tests | Dedicated test database URL |
| `SECRET_KEY` | Yes | JWT signing secret |
| `ALGORITHM` | No | JWT algorithm; defaults to `HS256` |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | No | Access-token lifetime |
| `ENVIRONMENT` | Recommended | `development`, `test`, or `production` |
| `ALLOWED_ORIGINS` | Production | JSON array or comma-separated CORS origins |
| `ADMIN_EMAILS` | Maintenance | Comma-separated allowlist for crawl, graph, embedding, and PageRank jobs |
| `SCHEDULER_ENABLED` | No | Enables the in-process graph-refresh scheduler; defaults to `false` |
| `GRAPH_REFRESH_TOPICS` | Scheduler | Comma-separated topics refreshed by the scheduler |
| `SEMANTIC_SCHOLAR_API_KEY` | No | Optional upstream API key used for citation enrichment |

Supported application database URL formats include:

```text
postgresql+asyncpg://...
postgresql+psycopg://...
postgresql://...
postgres://...
```

Plain Render-style `postgresql://` and `postgres://` URLs are normalised to
use `asyncpg`.

Example:

```env
DATABASE_URL=postgresql+asyncpg://sguser:password@localhost:5432/scholargraph
TEST_DATABASE_URL=postgresql+asyncpg://sguser:password@localhost:5432/scholargraph_test
SECRET_KEY=replace-with-a-long-random-secret
ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=30
ENVIRONMENT=development
ALLOWED_ORIGINS=["http://localhost:8000","http://127.0.0.1:8000"]
ADMIN_EMAILS=admin@example.com
SCHEDULER_ENABLED=false
GRAPH_REFRESH_TOPICS=cs.AI
```

Every corpus-mutating or compute-heavy endpoint is restricted to users whose
email appears in `ADMIN_EMAILS`. Restart the application after changing this
allowlist because security settings are loaded at process startup.

Never point `TEST_DATABASE_URL` at the development or production database. The
test suite performs destructive table cleanup.

---

## API Endpoints Reference

### Authentication

| Method | Endpoint | Authentication | Description |
|---|---|---|---|
| POST | `/auth/register` | No | Register a user |
| POST | `/auth/login` | No | Receive access and refresh tokens |
| POST | `/auth/refresh` | No | Refresh an access token |
| GET | `/auth/me` | Yes | Return the authenticated user |
| POST | `/auth/api-keys` | Yes | Create an API key |
| GET | `/auth/api-keys` | Yes | List the user's API keys |
| DELETE | `/auth/api-keys/{api_key_id}` | Yes | Revoke an API key |

### Papers

| Method | Endpoint | Authentication | Description |
|---|---|---|---|
| GET | `/papers` | No | List and filter papers |
| GET | `/papers/ranked` | No | Return papers ranked by PageRank |
| GET | `/papers/search/semantic` | No | Search abstract embeddings |
| GET | `/papers/{paper_id}` | No | Return one paper |
| GET | `/papers/{paper_id}/similar` | No | Return similar papers |
| GET | `/papers/{paper_id}/citations` | No | Return citation relationships |
| GET | `/papers/{paper_id}/authors` | No | Return paper authors |

### Authors

| Method | Endpoint | Authentication | Description |
|---|---|---|---|
| GET | `/authors` | No | List authors with aggregate metrics |
| GET | `/authors/{author_id}` | No | Return one author and their papers |
| GET | `/authors/{author_id}/impact` | No | Return author-impact analytics |

### Annotations

| Method | Endpoint | Authentication | Description |
|---|---|---|---|
| POST | `/papers/{paper_id}/annotations` | Yes | Create an annotation |
| GET | `/papers/{paper_id}/annotations` | No | List annotations |
| PUT | `/annotations/{annotation_id}` | Owner | Update an annotation |
| DELETE | `/annotations/{annotation_id}` | Owner | Delete an annotation |

### Analytics

| Method | Endpoint | Authentication | Description |
|---|---|---|---|
| POST | `/analytics/pagerank` | Admin | Trigger PageRank |
| GET | `/analytics/topics` | No | Return topic analytics |
| GET | `/analytics/trend` | No | Return publication trends |
| POST | `/analytics/embed-papers` | Admin | Trigger embedding generation |

### Corpus Maintenance

| Method | Endpoint | Authentication | Description |
|---|---|---|---|
| POST | `/crawl` | Admin | Start a topic crawl |
| POST | `/crawl/seed-foundations` | Yes | Seed foundational papers |
| POST | `/crawl/build-graph` | Yes | Build one topic graph |
| POST | `/crawl/build-graph-all` | Yes | Build the full graph |

---

## Example Requests

### Register

```bash
curl -X POST http://127.0.0.1:8000/auth/register \
  -H "Content-Type: application/json" \
  -d '{
    "username": "aamir",
    "email": "aamir@example.com",
    "password": "StrongPassword123!"
  }'
```

### Login

```bash
curl -X POST http://127.0.0.1:8000/auth/login \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "username=aamir@example.com&password=StrongPassword123!"
```

### Semantic Search

```bash
curl \
  "http://127.0.0.1:8000/papers/search/semantic?q=transformer%20attention&limit=5"
```

### Ranked Papers

```bash
curl \
  "http://127.0.0.1:8000/papers/ranked?category=cs.AI&limit=10"
```

---

## MCP Server Setup

Start the ScholarGraph API locally on port `8000`, then run the MCP adapter.
It uses the API's public read endpoints and does not require separate
credentials.

Run the MCP server:

```bash
uv run python mcp_server/server.py
```

Example Claude Desktop configuration:

```json
{
  "mcpServers": {
    "scholargraph": {
      "command": "/absolute/path/to/scholargraph/.venv/bin/python",
      "args": [
        "/absolute/path/to/scholargraph/mcp_server/server.py"
      ]
    }
  }
}
```

---

## Running Tests

The pgvector test database is created automatically by Docker Compose and
Codespaces.

The test fixture:

- enables pgvector;
- creates ORM tables at session start;
- clears tables between tests;
- drops ORM tables after the suite;
- refuses to target anything except `scholargraph_test`.

Run the complete suite:

```bash
make test
```

Run coverage:

```bash
make test-cov
```

Run linting:

```bash
make lint
```

---

## Deployment Notes

### Render

Render may supply a database URL beginning with:

```text
postgresql://
```

ScholarGraph normalises that URL to:

```text
postgresql+asyncpg://
```

You may also explicitly configure:

```text
postgresql+asyncpg://
```

or:

```text
postgresql+psycopg://
```

Required production variables:

```env
DATABASE_URL=postgresql+asyncpg://<user>:<password>@<host>:5432/<database>
SECRET_KEY=<long-random-production-secret>
ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=30
ENVIRONMENT=production
ALLOWED_ORIGINS=["https://your-scholargraph-domain.onrender.com"]
ADMIN_EMAILS=your-admin@example.com
SCHEDULER_ENABLED=false
```

Production startup rejects placeholder JWT secrets, wildcard credentialed
CORS, an empty origin list, and an empty administrator allowlist. Enable the
in-process scheduler on only one designated application process; otherwise
each replica would run the same scheduled job.

Enable pgvector in the Render database:

```sql
CREATE EXTENSION IF NOT EXISTS vector;
```

The production container runs migrations automatically before starting
Uvicorn.

#### Embedding resource requirements

The current 512 MB Render deployment can serve the conventional API, but it
does not have enough memory to run the sentence-transformers embedding job
reliably. PyTorch model inference can exceed the instance limit and cause the
service to restart.

Run `POST /analytics/embed-papers` in the devcontainer, GitHub Codespaces, or
another environment with at least 1 GB of available memory. The job commits
after each batch and only selects papers whose `abstract_embedding` is `NULL`,
so an interrupted run can be resumed safely.

This is a resource limitation of the current deployment rather than a missing
API feature. Semantic search and similar-paper discovery require generated
embeddings; metadata retrieval, authentication, annotations, citation
analytics, and PageRank do not.

---

## Disk-Space Management

ScholarGraph uses CPU-only PyTorch by default.

This prevents Linux environments from downloading several gigabytes of CUDA
and NVIDIA libraries when no GPU is available.

Codespaces also:

- stores temporary uv files under `/tmp`;
- installs with `--no-cache`;
- deletes the temporary uv cache after setup;
- excludes `.venv`, caches, models and test output from Git;
- uses a persistent PostgreSQL Docker volume;
- avoids installing PostgreSQL server binaries in the workspace container.

Useful cleanup command:

```bash
make clean-cache
```

To completely recreate the local development databases:

```bash
make reset-db
```

This deletes the local Docker PostgreSQL volume and its data.

---

## Tech Stack

| Technology | Purpose |
|---|---|
| FastAPI | Async REST API and OpenAPI documentation |
| Pydantic | Request, response and settings validation |
| SQLAlchemy async | ORM and asynchronous database access |
| Alembic | Database migrations |
| PostgreSQL | Relational persistence |
| pgvector | Vector similarity search |
| asyncpg | Default async PostgreSQL driver |
| Psycopg 3 | Optional PostgreSQL-driver compatibility |
| Sentence Transformers | Abstract embeddings |
| CPU-only PyTorch | Model inference without CUDA dependencies |
| APScheduler | Background jobs |
| JWT | Authentication |
| arXiv API | Paper metadata |
| Semantic Scholar API | Citation enrichment |
| MCP | AI tool integration |
| GitHub Actions | Automated linting and tests |
| Docker Compose | Automatic development infrastructure |
| Render | Production deployment |

---

## Author

**Aamir Khan** — University of Leeds

[GitHub](https://github.com/aamirk24) ·
[LinkedIn](https://www.linkedin.com/in/aamirkhan05/)

---

## License

No open-source license has been selected for this repository. The source is
available for portfolio review; reuse or redistribution is not granted unless
a license is added later.
