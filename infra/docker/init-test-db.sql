-- Creates the test database alongside the development one.
--
-- Tests run against real Postgres rather than SQLite because behaviour must
-- match production: SQLite silently accepts partial indexes, CITEXT, and
-- NUMERIC semantics that Postgres enforces. Running them against a separate
-- database keeps `make test` from destroying local development data.

CREATE DATABASE frugal_test OWNER frugal;

-- The personalization database (ADR-011), and its test twin.
--
-- Be precise about what this buys locally: these are separate *databases* on
-- one Postgres instance, not separate servers. That is still the property the
-- design depends on -- Postgres cannot express a foreign key across databases,
-- and no query can join them -- but it is a weaker isolation claim than
-- production, where the two are separate Neon projects. The docs must not blur
-- the two.
CREATE DATABASE frugal_signals OWNER frugal;
CREATE DATABASE frugal_signals_test OWNER frugal;
