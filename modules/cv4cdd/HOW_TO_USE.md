# How to use CV4CDD - Concept Drift Detection

CV4CDD finds out when your process changed. It turns the log into an image of how alike the process was in different periods and lets a trained image model mark the changes. You get a list of drifts with their type, time and confidence, and a map that shows where the model saw them.

## When to use it

- You suspect that the process was changed, or changed by itself, and you want to know when, and whether it was a single switch, a gradual rollout, a series of small changes or a pattern that comes back.
- You want the periods before and after a change to compare. Use the detected times as the basis for a comparison in Process Comparison, or look at Performance Over Time, which can shade the detected drift periods behind its trend.
- You want to know why the process changed. CV4CDD says when and what kind, not why; Concept Drift Explainer takes the detections further.
- You want recurring calendar patterns, such as busy weeks that look alike, or the trend of one metric. Use Temporal Mapper or Performance Over Time.

## Before you start

- The module needs a case-centric event log with `case_id`, `activity` and `timestamp` columns, at least 200 events and at least 20 cases. Only those three columns are used. Otherwise the module's card in the process's module list shows an **Unavailable** badge and cannot be opened; hover the card to read the reason.
- A detection model has to be installed. MATE ships none: an administrator can pin one for everyone under Admin → Controls, or anyone can upload one on the module's settings page (see Settings). Without a model the first run fails with "No CV4CDD detection model is available." and the page offers **Retry**.
- There is no run button. The first time you open the module for a process, and again when the log's data has changed since the last run, MATE starts the detection. It shows "Computing CV4CDD - Concept Drift Detection…" with a progress bar and a **Cancel** button before the panel appears. Models are large, about 0.5 GB each, so a run can take a while.
- This page has no filter bar. A filter applied on the log's **Events** tab narrows the events the detection reads, and applying or clearing it makes a module you have already opened compute again.
- Everything runs on your MATE server, so the module is safe for confidential logs.

## Settings

The panel has no controls. The settings sit on the module's page: open **Module Settings** in the sidebar and choose this module. The **Detection model** card stands above the **Configuration** card.

### Detection model card

- **Upload model** adds a pretrained model to the platform. It accepts a `.tar.zst` archive that contains a TensorFlow SavedModel, and the model is shared with every account. The name comes from the file name, and a name that already exists is refused until you delete the old model. If your account has no model selected yet, the one you upload is selected automatically.
- The list below shows every installed model with its size. Choose one to use it with your account. The choice is saved at once, together with any values in the Configuration card that you changed but did not save yet. The model in use is marked **in use**. Without a choice, MATE picks an installed model itself.
- The trash icon deletes a model from disk for every account after you confirm.
- When an administrator pins a model, the card shows **Admin-controlled**: the choice and deletion are read-only, and uploads stay possible.
- The authors publish models and the training dataset on [Hugging Face](https://huggingface.co/datasets/pm-science/cv4cdd_4d).

### Configuration card

Save the values with **Save configuration**. An administrator can lock the card, which makes it read-only. Saving does not start a run and does not change detections that already exist: new values apply the next time detection runs, for example after the log's data has changed.

| Setting | Default | What it changes |
| --- | --- | --- |
| Number of windows | 200 | How many windows the log is cut into (50 to 500). The model was trained at 200, so change it only if your log has very few cases. |
| Detection confidence threshold | 0.5 | The lowest confidence a detection needs to be reported (0.1 to 0.95 in steps of 0.05). A detection counts when its confidence is above the threshold. Lower values report more drifts, including more false alarms. |

## Reading the results

If no stored detections exist, the page reads "No drifts detected yet". Otherwise the panel shows two cards: **Detected drifts** on the left and **Drift map** on the right.

### Detected drifts

The card header repeats the confidence threshold and the number of windows of the run, for example `confidence ≥ 50% · 200 windows`. Below it, one badge per drift type counts the detections, for example "Sudden · 2". If nothing passed the threshold, the card reads "The model didn't find any drifts above the confidence threshold."

The table lists the detections in chronological order, and for the same start by confidence:

- **Type**: Sudden, Gradual, Incremental or Recurring. Hover the label for a short explanation. A sudden drift is a single point where the process abruptly changes. A gradual drift is one where old and new variants co-exist for a while before the switch completes. An incremental drift is a series of small adjustments over time. A recurring drift is an earlier process version that returns and replaces the current one.
- **Start** and **End**: the start times of the first case in the first and in the last window of the detection. A sudden drift is a single point, so both are equal. Because a window holds many cases, the real change can lie anywhere inside it.
- **Confidence**: how sure the model is, with one decimal. Treat low values as uncertain and check them against the drift map.

### Drift map

A picture of how alike the process was in every pair of windows. The windows are equal groups of cases in the order the cases started, and both axes run through them, with the oldest at the top left. A bright pixel means the two windows look alike, a dark one means they differ. The diagonal is always bright, because a window is compared with itself. The scale is relative to the most different pair in this log, so a dark patch can still be a small difference.

Blocks of bright pixels along the diagonal are stretches of similar behaviour, and a boundary between two blocks is a place where it changed. Each detection is a box with a label such as "gradual: 91%". White boxes are sudden drifts, blue gradual, magenta incremental and aqua recurring. If the image is not there yet, the card reads "Image not yet available".

### Dashboard cards

Cards follow the filters of their dashboard. Each has an info icon with a short explanation and an arrow (**Open in module**) that opens this page.

- **CV drift detections**: the number of detections across the run's windows, and one row per detection with its type, its window numbers and its confidence. **Min confidence** (0 to 1, default 0) hides detections below that value on the card only. A card without results asks you to run detection in the module.

## Tips and limits

- Start with the defaults. Raise the confidence threshold if you get too many doubtful drifts, and lower it if you expect one the model misses. Both need a new run to take effect.
- Windows hold an equal number of cases, not an equal span of time, so busy periods get many windows and quiet ones few. The last window takes the remainder. With fewer cases than windows, each window gets one case and the remaining windows stay empty, so lower **Number of windows** for small logs.
- The model was fine-tuned on synthetic logs with known drifts. A detection is a hint to look at that period, not proof of a change. Compare the periods in Process Comparison before you act on it.
- Only the latest run is kept for a log, and it replaces the previous one. Detections only mark when behaviour shifted. Use Concept Drift Explainer for possible causes.

## Method

CV4CDD encodes the log as a similarity matrix and treats drift detection as object detection on that image. The cases are put in order of their first event and cut into the chosen number of windows. For each window it counts how often each activity is directly followed by each other activity. It then computes the cosine distance between the counts of every two windows and draws the matrix with the viridis colour scale.

A fine-tuned object detector of the RetinaNet family looks at the image and returns boxes with a drift type and a confidence. Boxes above the threshold are mapped back from pixels to windows and then to the start time of the first case in each window. The method is the one of Kraus and van der Aa (2024), whose CV4CDD models and training data are published on Hugging Face. The formal citations are in the info popover next to the module name.
