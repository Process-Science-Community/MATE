# How to use Sample Module

A test fixture that exposes one route and one event handler, so the loader, the guide route and the upload path have a module to work with.

## When to use it

- When a test needs a bundled module that ships a guide.

## Before you start

- Any event log with `case_id`, `activity` and `timestamp`.

## Settings

This module has no settings.

## Reading the results

The module produces no output; it only answers `GET /api/v1/modules/sample_mod/ping`.

## Tips and limits

- It exists for the test suite and is never shipped.

## Method

None - this is a test double.
