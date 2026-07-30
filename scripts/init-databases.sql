CREATE EXTENSION IF NOT EXISTS vector;

-- PostgreSQL init scripts cannot use CREATE DATABASE inside a transaction.
SELECT 'CREATE DATABASE scholargraph_test OWNER sguser'
WHERE NOT EXISTS (
    SELECT
    FROM pg_database
    WHERE datname = 'scholargraph_test'
)\gexec

\connect scholargraph_test

CREATE EXTENSION IF NOT EXISTS vector;