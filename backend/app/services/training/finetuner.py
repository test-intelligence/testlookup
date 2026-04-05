"""
FineTuningPipeline — provider-specific fine-tuning job submission.

Supports:
  openai   — OpenAI fine-tuning API (gpt-4o-mini, gpt-3.5-turbo)
  ollama   — Local GGUF model creation via `ollama create` from a Modelfile
             (requires Unsloth/llama.cpp fine-tuned adapter pre-built separately)
  generic  — Logs instructions for manual fine-tuning with other providers

Workflow:
  1. Upload JSONL to provider (or use MinIO path for Ollama)
  2. Submit fine-tuning job
  3. Poll job status until complete
  4. Return provider_job_id + final model name for ModelEvaluator
"""
import asyncio
import logging
from datetime import timezone
from typing import Optional

from app.core.config import settings

logger = logging.getLogger("training.finetuner")


class FineTuningPipeline:
    """Submits fine-tuning jobs to the configured LLM provider."""

    @classmethod
    async def submit(
        cls,
        track: str,
        training_path: str,
        base_model: Optional[str] = None,
    ) -> dict:
        """
        Submit a fine-tuning job for `track` using the JSONL at `training_path` (MinIO key).
        Returns: {provider_job_id, model_name, status}
        """
        _model = base_model or settings.LLM_MODEL
        provider = settings.LLM_PROVIDER

        logger.info(
            "Submitting fine-tune job: track=%s provider=%s base_model=%s path=%s",
            track, provider, _model, training_path,
        )

        if provider == "openai":
            return await cls._submit_openai(track, training_path, _model)
        elif provider == "ollama":
            return await cls._submit_ollama(track, training_path, _model)
        else:
            return cls._log_manual_instructions(track, training_path, _model, provider)

    @classmethod
    async def poll_status(cls, provider_job_id: str) -> dict:
        """
        Poll the fine-tuning job for completion.
        Returns: {status: "running"|"succeeded"|"failed", fine_tuned_model: str|None}
        """
        provider = settings.LLM_PROVIDER
        if provider == "openai":
            return await cls._poll_openai(provider_job_id)
        # Ollama jobs complete synchronously in _submit_ollama
        return {"status": "succeeded", "fine_tuned_model": provider_job_id}

    # ── OpenAI ────────────────────────────────────────────────────────────────

    @classmethod
    async def _submit_openai(cls, track: str, training_path: str, base_model: str) -> dict:
        if settings.AI_OFFLINE_MODE:
            raise RuntimeError("AI_OFFLINE_MODE=true — cannot call OpenAI fine-tuning API")
        if not settings.OPENAI_API_KEY:
            raise RuntimeError("OPENAI_API_KEY not set")

        try:
            from openai import AsyncOpenAI
            client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)

            # Download JSONL from MinIO and upload to OpenAI Files API
            from app.db.storage import get_storage_provider
            storage = get_storage_provider()
            content = await storage.get_object_content(
                training_path, bucket=settings.FINETUNE_EXPORT_BUCKET
            )

            file_obj = await client.files.create(
                file=(f"{track}-training.jsonl", content, "application/jsonl"),
                purpose="fine-tune",
            )
            logger.info("Uploaded training file to OpenAI: file_id=%s", file_obj.id)

            job = await client.fine_tuning.jobs.create(
                training_file=file_obj.id,
                model=base_model,
                suffix=f"{settings.FINETUNE_OPENAI_SUFFIX}-{track}",
                hyperparameters={"n_epochs": 3},
            )
            logger.info("OpenAI fine-tune job created: job_id=%s", job.id)
            return {
                "provider_job_id": job.id,
                "model_name": f"pending:{job.id}",
                "status": "running",
            }
        except Exception as exc:
            logger.error("OpenAI fine-tune submission failed: %s", exc)
            raise

    @classmethod
    async def _poll_openai(cls, job_id: str) -> dict:
        if not settings.OPENAI_API_KEY:
            return {"status": "failed", "fine_tuned_model": None}
        try:
            from openai import AsyncOpenAI
            client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
            job = await client.fine_tuning.jobs.retrieve(job_id)
            return {
                "status": job.status,  # "running"|"succeeded"|"failed"|"cancelled"
                "fine_tuned_model": job.fine_tuned_model,
            }
        except Exception as exc:
            logger.error("OpenAI poll failed job_id=%s: %s", job_id, exc)
            return {"status": "failed", "fine_tuned_model": None}

    # ── Ollama (local) ────────────────────────────────────────────────────────

    @classmethod
    async def _submit_ollama(cls, track: str, training_path: str, base_model: str) -> dict:
        """
        For local Ollama deployment: expects a pre-built GGUF adapter to already exist
        at a known MinIO path. Creates a new Ollama model from a Modelfile.

        Pre-requisite (run outside this app):
          1. Use Unsloth/llama.cpp to fine-tune and export GGUF
          2. Upload to MinIO: training-data/{track}/adapter.gguf
          3. This method downloads it, writes a Modelfile, calls `ollama create`
        """
        from datetime import datetime as _dt
        version_tag = _dt.now(timezone.utc).strftime("%Y%m%d")
        new_model_name = f"{base_model}-testlookup-{track}-{version_tag}"
        gguf_path = training_path.replace(".jsonl", ".gguf")

        try:
            from app.db.storage import get_storage_provider
            import tempfile
            import pathlib
            storage = get_storage_provider()
            gguf_bytes = await storage.get_object_content(
                gguf_path, bucket=settings.FINETUNE_EXPORT_BUCKET
            )

            with tempfile.TemporaryDirectory() as tmpdir:
                gguf_file = pathlib.Path(tmpdir) / "model.gguf"
                gguf_file.write_bytes(gguf_bytes)

                modelfile_content = (
                    f"FROM {gguf_file}\n"
                    f"PARAMETER temperature {settings.LLM_TEMPERATURE}\n"
                    f'SYSTEM "You are a QA test failure analysis expert."\n'
                )
                modelfile_path = pathlib.Path(tmpdir) / "Modelfile"
                modelfile_path.write_text(modelfile_content)

                proc = await asyncio.create_subprocess_exec(
                    "ollama", "create", new_model_name, "-f", str(modelfile_path),
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=300)

                if proc.returncode != 0:
                    raise RuntimeError(f"ollama create failed: {stderr.decode()}")

            logger.info("Created Ollama model: %s", new_model_name)
            return {
                "provider_job_id": new_model_name,
                "model_name": new_model_name,
                "status": "succeeded",
            }

        except FileNotFoundError:
            logger.warning(
                "GGUF adapter not found at %s — skipping Ollama fine-tune. "
                "Run Unsloth training separately and upload adapter.gguf to MinIO.",
                gguf_path,
            )
            return {"provider_job_id": None, "model_name": base_model, "status": "skipped"}
        except Exception as exc:
            logger.error("Ollama fine-tune failed: %s", exc)
            raise

    @staticmethod
    def _log_manual_instructions(track: str, training_path: str, model: str, provider: str) -> dict:
        """For unsupported providers, log clear instructions."""
        logger.warning(
            "Auto fine-tuning not supported for provider=%s. "
            "Manual steps:\n"
            "  1. Download training JSONL: MinIO → %s/%s\n"
            "  2. Fine-tune %s using Unsloth / your provider's API\n"
            "  3. Upload the result and call POST /api/v1/training/promote\n"
            "     with {\"track\": \"%s\", \"model_name\": \"<your-model>\"}",
            provider, settings.FINETUNE_EXPORT_BUCKET, training_path, model, track,
        )
        return {
            "provider_job_id": None,
            "model_name": model,
            "status": "manual_required",
        }
