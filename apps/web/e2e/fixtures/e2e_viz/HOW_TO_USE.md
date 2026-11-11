# How to use E2E viz fixture

E2E viz fixture serves static sample data in every dataset shape so the Studio catalog can be tested end to end.

## When to use it

- Only in the Playwright run of `make e2e`, which copies it into the modules folder and removes it afterwards.
- Never for real analysis: the data does not come from your event log.

## Before you start

- Any case-centric event log; the module does not read it.

## Settings

This module has no settings.

## Reading the results

### Sample datasets

Each dataset returns a fixed response: rows, a Petri net, a process tree, KPIs, parallel arrays, a dense grid, column and row arrays, buckets, KPI items, a DFG, envelope responses, a prefix tree and a note.

## Tips and limits

- The numbers are made up and never change.

## Method

Every route returns a constant from `module.py`.
