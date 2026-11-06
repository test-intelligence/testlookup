# Fine-tuning (operator path)

[Documentation home](../README.md) · [Configuration reference](configuration.md) · [Feature flags](feature-flags.md) · [AI configuration](../../user-guide/ai-features.md)

TestLookup can fine-tune a language model on the failures your team has confirmed or corrected. It is **off by default** and an **operator-only** path: **Settings → AI** shows no control for it, and normal use needs nothing on this page. The learning loop your users see is the **ML classifier** card on **Settings → AI** (**Train now**): it trains on the same corrections, on the CPU, with no language model and no GPU ([How the ML classifier activates](../../user-guide/ai-features.md#how-the-ml-classifier-activates)).

## What it needs

- **A GPU or a hosted provider.** The pipeline never trains a model on the TestLookup servers (`backend/requirements.txt` carries no training library). What it does depends on the environment's `LLM_PROVIDER`, and it starts from the environment's `LLM_MODEL` (the provider, model and keys saved at **Settings → AI** are not read here):
  - `openai`: submits a fine-tuning job to OpenAI with the environment's `OPENAI_API_KEY` and polls it for up to 90 minutes. Refused while the environment sets `AI_OFFLINE_MODE=true`.
  - `ollama`: registers a model you trained yourself (for example with Unsloth or llama.cpp on a GPU machine). Upload the GGUF to the `FINETUNE_EXPORT_BUCKET` bucket as `<track>/<YYYY-MM-DD>.gguf`, for the UTC day the pipeline runs; the worker then runs `ollama create`, so the `ollama` command must be on the worker's `PATH`. Upload the file before the run: on an S3 store a missing file fails the run, which is retried once 30 minutes later (only the local storage backend ends it as `skipped`).
  - any other provider: the run logs the manual steps and ends as `manual_required`; train elsewhere and put the model in service with `POST /api/v1/training/promote` ([Manual endpoints](#manual-endpoints)).
- **Celery beat and a worker** on the `default` queue: every task below runs there.

## Turn it on or off

An administrator sets the switch through the API; it is the same request **Settings → AI** saves through.

```bash
# Turn fine-tuning on (ADMIN JWT). {"finetune_enabled": false} turns it off.
curl -X PUT "$TL_URL/api/v1/settings/ai" \
  -H "Authorization: Bearer $TL_TOKEN" -H 'Content-Type: application/json' \
  -d '{"finetune_enabled": true}'
```

The value is merged into the stored AI configuration, and the training tasks read it fresh every time they run. Like any save of that configuration, the request stores every other AI setting at its current effective value too: environment defaults such as `DEEP_INVESTIGATION_ENABLED` or `ANALYSIS_MODE` stop applying from then on (their `*_source` reads `ai_config`).

- **`FINETUNE_ENABLED` is the default only while nothing is stored.** A stored value wins over the environment in both directions. The **Settings → AI** form saves every field, so the first **Save Configuration** of any field stores the fine-tuning value in force at that moment; from then on, changing `FINETUNE_ENABLED` has no effect: use the request above.
- **A save on Settings → AI never changes it.** The page keeps the value it read and sends it back unchanged with every save.
- **Check the effective value** with `GET /api/v1/training/status` (any signed-in user): `finetune_enabled` is what the tasks obey, and `finetune_enabled_source` says where it comes from (`ai_config`: stored; `env_default`: `FINETUNE_ENABLED`). The same response has the feedback counts, the per-track thresholds and the models in service. `GET /api/v1/settings/ai` (QA lead and above) reports the same two fields.

## What runs

| Task (beat entry) | When (UTC) | Gated by the switch |
|---|---|---|
| `export_training_data` (`weekly-training-export`) | Sundays 02:00 | **No.** Exports the verified examples of each track to `FINETUNE_EXPORT_BUCKET` (`<track>/<YYYY-MM-DD>.jsonl`, plus a `holdout-<YYYY-MM-DD>.jsonl` with a `FINETUNE_EVAL_HOLDOUT` share of them). Then, only while the switch is on, queues fine-tuning for each track whose export reached its threshold (`FINETUNE_CLASSIFIER_MIN_EXAMPLES` 500, `FINETUNE_REASONING_MIN_EXAMPLES` 2000, `FINETUNE_EMBED_MIN_PAIRS` 1000). |
| `check_finetune_trigger` (`daily-finetune-trigger-check`) | daily 03:00 | **Yes.** Queues an export once `FINETUNE_INCREMENTAL_TRIGGER` (200) unexported feedback examples exist. |
| `run_finetune_pipeline(track)` | when queued | **Yes.** While off it returns `{"skipped": true, "reason": "finetune_disabled"}`. Otherwise it submits the job (above), evaluates the candidate on the holdout file, records a model version, and puts the candidate in service only when the evaluation approves it (for the classifier track: an accuracy gain of at least `FINETUNE_MIN_ACCURACY_GAIN`, 0.02). |

The nightly ML classifier training (`nightly-ml-classifier-training`, 03:10 UTC, gated by `ML_RETRAIN_ENABLED`) is a separate job and ignores this switch.

## Manual endpoints

| Request | Role | What it does |
|---|---|---|
| `POST /api/v1/training/export` | QA lead and above | Queues the export now. Not gated by the switch. |
| `POST /api/v1/training/finetune` with `{"track": "classifier"}` | QA lead and above | Queues the pipeline for one track (`classifier`, `reasoning` or `embedding`; anything else is a `400`). Skipped while the switch is off. |
| `POST /api/v1/training/promote` with `{"track": "classifier", "model_name": "<model>", "eval_accuracy": 0.91, "baseline_accuracy": 0.86}` | ADMIN | Puts a model you trained elsewhere in service for the track and records it as the active version. The two accuracies are optional. |
| `GET /api/v1/training/status` | any signed-in user | The effective switch and its source, feedback counts, thresholds and the models in service. |

A model put in service is picked up without a restart: language-model calls made for a track use that track's promoted model. The registry entry lives in Valkey with a **30-day expiry** that only a new promotion refreshes, and a Valkey flush loses it too; the track then falls back to the configured model (the one saved at **Settings → AI**, else `LLM_MODEL`) until you promote again.
