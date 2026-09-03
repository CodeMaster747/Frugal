"""Bank-alert SMS as a transaction source.

Frugal's other four ingestion paths all begin with the user telling us what an
entry means: they typed it, they mapped a CSV column to it, they photographed a
receipt and reviewed the fields. SMS is the first path where a message arrives
unbidden and the system has to work out whether it is a transaction at all.

That difference shapes the module. The parser is pure and lives behind an
import-linter contract that keeps a database session out of it, so a template
can be exercised against a fixture in microseconds. Nothing auto-commits
without both a confident parse and a *confirmed* account mapping, because a
transaction attributed to the wrong account is worse than one not recorded --
it is wrong in the ledger, and it silently corrupts every engine downstream.
"""
