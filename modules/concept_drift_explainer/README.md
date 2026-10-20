# Concept Drift Explainer

Port of the master-thesis prototype (Schaffner, "Explaining Concept Drifts in
Business Processes: An LLM-Based Approach to Context-Aware Sense-Making in
Process Mining", University of Münster) into a Mate module.

## Using it

The user guide is [`HOW_TO_USE.md`](HOW_TO_USE.md): the accounts and settings you
need, how to index documents and run an analysis, and how to read the explanation
and the confidence. The app shows it behind the **How to use** button on the
module page. This README is the development and provenance notes.

## What it does

For each drift detected by the [cv4cdd](../cv4cdd/) module, the CDE retrieves
evidence from a per-log corpus of enterprise documents (PDF, DOCX, PPTX, TXT, PNG,
JPG) embedded in Pinecone, then synthesises a ranked, evidence-backed list of
hypothesised causes. A chatbot lets analysts ask follow-up questions grounded
in the same evidence state.

The pipeline is a LangGraph chain, `drift → retrieval → re-ranker → Franzoi
mapper → explanation`, with the chatbot as a separate step over the stored result
(`graph/build_graph.py`, `agents/`).

This module keeps its AI config **fully isolated** from the platform's global
**Settings → AI** – it owns its own OpenAI key and only ever talks to OpenAI.

## Scope

This first cut covers the core retrieval + ranking + explanation pipeline plus
the follow-up chatbot. The original repository's drift-linker (multi-drift
meta-analysis) and DOCX report generation are not yet ported.

## Costs and guardrails

- The pipeline auto-refreshes the drift list when cv4cdd finishes, but it
  never auto-runs the LLM stages – explanation runs are user-driven.
- Only the finished explanation of a drift (`explanations`) and the chat history
  per drift are cached. The LLM response caches live inside one pipeline
  invocation, so every **Run analysis** calls OpenAI again.

## Provenance

The original repository lives at
[github.com/janschaffner/concept_drift_explainer](https://github.com/janschaffner/concept_drift_explainer).
The agents, prompts, and Pydantic schemas in this module are the same as the
original – only the credential plumbing, on-disk document storage, and graph
entry point have been adapted for the platform.
