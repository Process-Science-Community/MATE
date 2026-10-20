# How to use Concept Drift Explainer

Concept Drift Explainer looks for the reason behind a change that CV4CDD detected. You upload dated company documents, such as policies, announcements or slide decks. For a drift you choose, it finds the documents from around that time, lets an AI model suggest what in them could explain the change and lists the likely causes with their evidence. A chat answers follow-up questions.

## When to use it

- CV4CDD found a drift and you want to know what could have caused it, such as a system rollout, a new policy or a reorganisation, and you have documents from around that time.
- You want a hypothesis about a change that points at a passage in a named document instead of a guess.
- You want to know when the process changed and what kind of change it was: use CV4CDD, which this module builds on. To compare the process before and after a drift, use Process Comparison.
- You have no documents from the period, or you may not send them to an outside service: this module has nothing to work with then.

> [!WARNING] This module is not safe for confidential data. Indexing sends the text of every uploaded document to OpenAI to be embedded, lets OpenAI's vision model describe pictures and stores the text in your Pinecone index. An analysis sends OpenAI the name of the process, a few events with their activity names and timestamps, and the text of up to four documents. The chat sends the explanation and your questions. With **Show only confidential modules** switched on in Settings → General, the module is hidden from the module list.

## Before you start

- The module needs a case-centric event log with `case_id`, `activity` and `timestamp` columns, at least 200 events and at least 20 cases, and the CV4CDD module installed. Otherwise the module's card in the process's module list shows an **Unavailable** badge and cannot be opened; hover the card to read the reason.
- The drifts come from CV4CDD, and there is no run button for them. The first time you open the module for a process, and again when the log's data has changed, MATE starts CV4CDD first. It shows "Computing CV4CDD - Concept Drift Detection…" with a progress bar and a **Cancel** button before the panel appears. CV4CDD needs a detection model; without one the run fails and the page offers **Retry**. The Explainer itself computes nothing on opening. Nothing is sent to an outside service until you press **Re-index**, press **Run analysis** or send a chat question.
- You need an OpenAI API key and a Pinecone API key of your own, and the providers may bill you for what you use. The module has its own OpenAI key and does not use the platform's Settings → AI. See Settings.
- Name every document `YYYY-MM-DD_`, followed by a name of your choice, such as `2024-03-01_new-approval-policy.pdf`. The date is the day the document applies to, and the search for evidence uses it. Supported types are PDF, DOCX, PPTX, TXT, PNG and JPG.
- This page has no filter bar. A filter applied on the log's **Events** tab narrows the events that CV4CDD and this module read.

## Settings

The panel has no settings of its own. They sit on the module's page: open **Module Settings** in the sidebar and choose this module. An administrator can lock each card, which makes it read-only. **Save AI models** and **Save configuration** both save everything on the page.

### AI models card

Paste your OpenAI key and click **Check**. A verified key reads "Key verified" with the number of models available and fills the two model lists.

| Setting | Default | What it changes |
| --- | --- | --- |
| OpenAI API key | none | The key for every AI call of this module. It is stored with this module only. Without it, indexing and analysis fail with "No OpenAI API key set for the Concept Drift Explainer". |
| Agent model | `gpt-4o-mini` | The chat model for all steps of an analysis, for the chat and for describing pictures. The list holds every model of your account that is not an embedding model, so pick a chat model. It has to follow JSON instructions, and to read pictures it has to accept images. None uses the default. |
| Embedding model | `text-embedding-3-small` | The model that turns document passages and the drift description into vectors. None uses the default. |
| Dimensions | 1536 | The size of the vectors and of the Pinecone index, with presets from 256 to 3072. Blank means that no size is sent to OpenAI and the index gets 1536, so a model whose native size differs needs it set here. After a change, use **Recreate Pinecone index** and index the documents again. |

**Recreate Pinecone index** deletes the index and creates an empty one with the saved dimension, after you confirm, so save the AI models first. All indexed passages are lost. The uploaded documents stay on the server, so index them again afterwards.

### Configuration card

| Setting | Default | What it changes |
| --- | --- | --- |
| Pinecone API key | none | The key from your Pinecone account. It is stored encrypted and is needed to index documents and to look up evidence. |
| Pinecone index name | `conceptdriftexplainer` | The index that holds the passages. It is created at the first indexing if it does not exist, as a serverless index on AWS in us-east-1. Every process uses its own section of the index, so processes do not mix. |
| Maximum ranked causes | 3 | The most causes shown for one drift (1 to 10). An analysis uses at most four documents and writes one cause for each, so four is the practical limit. |
| Cause confidence threshold | 0.25 | Causes with a confidence below this value are dropped (0 to 1 in steps of 0.05). |

Both limits apply when an analysis runs and are stored with its result. Changing them does not change an explanation that already exists; run the analysis again.

## Reading the results

If CV4CDD found no drifts, the page reads "No drifts to explain yet". Otherwise the panel shows four cards: **Detected drifts**, **Context documents**, **Explanation** and **Follow-up chat**.

### Detected drifts

One badge per drift type counts the detections, for example `Sudden · 2`. The table lists them as CV4CDD stored them, and the first row is selected at the start. Click a row to select another drift; the explanation and the chat follow the selection.

- **Type**: Sudden, Gradual, Incremental or Recurring, each with its own colour. CV4CDD explains the types.
- **Start**: the date and time at which the drift begins, in your local format.
- **Activities**: two activity names with an arrow between them, which are a rough hint at what changed. Treat them with care, see Tips and limits.
- **Confidence**: how sure CV4CDD is about the detection, with one decimal. It is not the confidence of a cause.

**Run analysis** explains the selected drift and reads "Running…" until it is done. It stays off until at least one document with a valid date and a supported type is uploaded, and a note says so. It does not know whether you indexed the documents. When the run ends, a message reads "Explanation ready", or "Explanation failed" with the reason. One reason is "No indexed documents for this log", which means the documents were uploaded but not indexed, or the indexing is still running.

### Context documents

**Upload** adds one or more documents to this process, and the trash icon next to a document removes it from the list and its passages from the index. **Re-index** reads every uploaded document again, splits it into passages, embeds them and stores them in Pinecone, so use it after uploading documents. It also stores a short glossary of process-management terms that the AI model uses as background and never quotes. Re-index is off until a document with a valid date is uploaded.

- Documents belong to one process. A document whose name begins with a date that does not exist, such as `2024-13-45_`, is marked "no date prefix" and skipped when indexing. A file with no extractable text, such as a scanned PDF, is skipped too.
- Re-index runs as a background job named "CDE - indexing documents". The panel shows neither its progress nor its end. Watch it in the **Jobs** drawer of the sidebar and wait until it has finished before you press **Run analysis**.
- Uploading gives no message either. A file whose name does not start with a date, or whose type is not supported, is refused and does not appear in the list.

### Explanation

The **Executive summary** is a short text that sums up the causes. Below it, the causes are numbered in the order the model ranked them. Each shows a badge with its context category, its confidence in percent and a description. Clicking a cause opens or closes its details, and the first one starts open. They hold the name of the source document, the passage that supports the cause in quotation style, and a bar that shows the confidence.

The category is written as a path such as `ORGANIZATION_INTERNAL::Process_Management`. The three groups are the context of the process itself, of the organisation and of the world outside it, each with sub-categories such as IT management, legal or economic. A cause with no category reads "Uncategorised".

The descriptions are written as hypotheses, with phrases such as "This could suggest…", and they are meant to be checked. If nothing fits, the card reads "No causes were ranked above the configured confidence threshold", and when no document matched at all, the summary says that no relevant documents were found.

The confidence is a score that MATE computes, not a probability and not the certainty of the model. It rises with the match between the passage and the drift, and with how close the document's date is to the start of the drift, up to 60 days, and it is boosted for the best two documents, doubled for the first and multiplied by 1.5 for the second, with a ceiling of 99%. With a single relevant document the score is always 99%.

### Follow-up chat

Type a question below the explanation and send it. The assistant answers from the explanation of the selected drift and the earlier questions about it, and it names the documents. A question that has nothing to do with the drift gets a standard reply that the assistant covers only the drift analysis. The conversation is kept for each drift. After a reload or when you switch to another drift, the box starts empty, and the whole earlier conversation of that drift shows again when you ask the next question.

Ask only once the analysis for that drift is done. Without it the question gets no answer and no message.

### Dashboard cards

Each card has an info icon with a short explanation and an arrow (**Open in module**) that opens this page.

- **Concept drifts**: the number of detected drifts across the run's windows and one row per drift with its type, its window numbers and its confidence. **Min confidence** (0 to 1, default 0) hides drifts below that value on the card only. A log without any detected drift reads "Run drift detection in the Concept Drift Explainer module to populate this card", although there is no run button: the detection starts when the module or the card is first opened.

## Tips and limits

- Date each document with the day it took effect or was published. The search looks first at documents from 14 days before to 3 days after the start of the drift, and only when nothing falls in that window does it look at all documents. A document dated far from the drift is used only when nothing falls into that window, and it earns little or no date bonus in its confidence.
- One matching document gives one cause with a score of 99%, whatever its quality. Add documents from the period for a ranked comparison.
- Check every cause in the document it names. The model writes the description and the quoted passage, so it can misread or shorten the text.
- The **Activities** column is only a hint. For each end of the drift, MATE takes the most frequent activity in the part of the stored event table that lies at the same relative position as the drift's first and last window. That table is ordered by case ID, not by time, so the two activities say little about what changed. The same two names go into the search for documents.
- Each run calls OpenAI for up to about a dozen chat answers and a few dozen embeddings, and a repeated run pays again. Only the finished explanation of a drift is kept, and a new run replaces it.
- The chat knows the summary, the names of the documents behind the causes and the first 100 characters of each quoted passage. It does not search the documents again.
- Embedding model and dimensions have to fit the index. After you change either, recreate the index and index the documents again.
- A missing key or an unreachable Pinecone shows up as a failed job in the **Jobs** drawer, and as "Explanation failed" for an analysis. Indexing and chat show no error in the panel.

## Method

The module follows the prototype of Schaffner's master's thesis, which explains concept drifts with a chain of AI agents over company documents. For the selected drift it works in five steps.

1. It takes the drift from CV4CDD and asks the model for a one-sentence description of the change, from a few events of a case that contains each of the two activities.
2. It embeds a search query about the drift, with its type, the process name and the two activities, and looks up the 30 closest passages of the process's documents, within the date window described above. It also looks up the closest glossary term.
3. It scores each passage as 0.4 times the similarity of the passage to the drift description plus 0.6 times the search similarity, keeps the best passage of each document and lets the model choose up to four documents, in order of relevance.
4. The model classifies each chosen passage against the process mining context taxonomy of Franzoi, Hartl et al. (2025).
5. For each chosen document the model reads its full text and writes one cause, with a prompt for the type of the drift, which follows the definitions of Bose et al. (2011) and Kraus and van der Aa (2025). When there are several causes, a second pass refines them and writes the summary. The confidence is added as described above.

The formal citation of the thesis is in the info popover next to the module name.
