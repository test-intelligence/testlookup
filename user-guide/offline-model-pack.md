# Offline model pack

TestLookup's AI triage runs on **local models** — an Ollama daemon you host, never a cloud API. On a connected host that is one `ollama pull` away. On an air-gapped host there is no registry to pull from, so the models have to arrive the same way the rest of your software does: as a file you carry in.

This guide is the **model pack**: which models TestLookup needs, what each one costs in disk and latency, how to build a transferable archive on a connected machine, how to restore it into an air-gapped install, and how to verify it actually landed.

> **Model files are not in the code bundle.** The release artifacts ship code and container images, not model weights — the default pair alone is ~5 GB, and the licences are the model publishers', not ours. Side-loading is the supported path, and it is a deliberate one-time step per environment.

## What TestLookup needs

| Purpose | Setting | Default | Why it exists |
|---|---|---|---|
| Triage LLM | `LLM_MODEL` | `qwen2.5:7b` | The ReAct agent behind Deep Investigation, run narratives, and Ask AI |
| Embeddings | `EMBEDDING_MODEL` | `nomic-embed-text` | Semantic search, the semantic analysis cache, and knowledge-base RAG |
| Fast classifier (optional) | `CLASSIFIER_MODEL` | unset — reuses `LLM_MODEL` | Single-call classification ahead of the full agent |

Both defaults are **served by Ollama**, including the embedding model — TestLookup does not use ChromaDB's bundled ONNX embedder, so ChromaDB alone is not enough to make semantic features work. If your pack contains the LLM but not `nomic-embed-text`, triage works and semantic search does not.

## Size and quality tradeoffs

Disk figures are the Ollama download size at the default quantisation (Q4); RAM is the rough working-set while generating. Latency is relative, on CPU-only hardware — a GPU changes the absolute numbers, not the ordering.

| Model | Disk | RAM | Quality / latency tradeoff | Status |
|---|---|---|---|---|
| `qwen2.5:7b` | ~4.7 GB | ~8 GB | The baseline everything is tuned and evaluated against. Follows the agent's tool-calling format reliably. | **Supported default** |
| `nomic-embed-text` | ~275 MB | ~1 GB | 768-dim embeddings, fast enough to embed every failure inline. | **Supported default** |
| `qwen2.5:3b` | ~2 GB | ~4 GB | ~2× faster and fits a small VM, at a visible cost in root-cause specificity; more likely to break the ReAct format, which surfaces as a fallback to rules. Reasonable as `CLASSIFIER_MODEL` while `LLM_MODEL` stays at 7b. | Known-workable alternate |
| `llama3.1:8b` | ~4.7 GB | ~8 GB | Comparable cost and quality to the 7b default; sometimes more verbose summaries. Useful if your organisation has already vetted this family. | Known-workable alternate |
| `qwen2.5:14b` | ~9 GB | ~16 GB | Noticeably better multi-step reasoning on tangled failures; roughly 2× the latency of 7b. Worth it if triage runs asynchronously and you have the RAM. | Known-workable alternate |
| `qwen2.5:32b` | ~20 GB | ~32 GB+ | Best reasoning on offer, but per-failure latency makes inline triage impractical on CPU. Only sensible with a GPU. | Known-workable alternate |
| `mxbai-embed-large` | ~670 MB | ~2 GB | 1024-dim embeddings, modestly better retrieval than nomic. **Changing the embedding model invalidates existing vectors** — see the warning below. | Known-workable alternate |

*Supported default* means it is what the defaults, the prompt evaluation gate, and our testing use. *Known-workable alternate* means the integration works and the behaviour is reasonable, but the prompts are not tuned for it — validate on your own failures before rolling it out.

> **Changing the embedding model is not a drop-in swap.** Vectors written with one embedding model are not comparable to vectors from another. If you switch, existing collections must be re-embedded or dropped, or semantic search silently returns nonsense neighbours. Changing `LLM_MODEL` has no such effect.

Sizing the host as a whole (CPU, RAM, disk growth) is covered in [Sizing & capacity](sizing.md).

## Building the pack (on a connected host)

Ollama has **no `save`/`load` command**. What it does have is a flat, content-addressed store on disk — a `blobs/` directory of layers plus a `manifests/` tree naming them — and that store is portable between hosts of the same Ollama major version. So the pack is simply an archive of that directory.

Do this on any internet-connected machine with Ollama installed. It does not need to be a TestLookup host.

```bash
# 1. Pull exactly the models your target will use.
ollama pull qwen2.5:7b
ollama pull nomic-embed-text

# 2. Confirm what you have (these names must match LLM_MODEL / EMBEDDING_MODEL).
ollama list

# 3. Archive the model store.
#    Linux (package install):  /usr/share/ollama/.ollama/models
#    Linux/macOS (user install): ~/.ollama/models
#    Docker container:          /root/.ollama/models
MODELS_DIR=~/.ollama/models
tar -C "$MODELS_DIR" -czf testlookup-model-pack.tar.gz blobs manifests

# 4. Checksum it — this is what makes the transfer auditable.
sha256sum testlookup-model-pack.tar.gz > testlookup-model-pack.tar.gz.sha256

# Optional but recommended: record per-model provenance alongside the pack.
ollama list > testlookup-model-pack.manifest.txt
sha256sum testlookup-model-pack.manifest.txt >> testlookup-model-pack.tar.gz.sha256
```

If the connected machine runs Ollama in Docker, pull inside the container and archive from there. `-T` matters — without it Docker allocates a TTY and corrupts the binary stream:

```bash
docker exec -i <container> ollama pull qwen2.5:7b
docker exec -i <container> ollama pull nomic-embed-text
docker exec -i <container> tar -C /root/.ollama/models -czf - blobs manifests > testlookup-model-pack.tar.gz
sha256sum testlookup-model-pack.tar.gz > testlookup-model-pack.tar.gz.sha256
```

Carry **both** files (archive + `.sha256`) across the air gap on your approved transfer medium.

## Verifying the transfer

Before restoring anything, on the target host:

```bash
sha256sum -c testlookup-model-pack.tar.gz.sha256
# testlookup-model-pack.tar.gz: OK
```

On Windows: `certutil -hashfile testlookup-model-pack.tar.gz SHA256` and compare by eye.

A failed checksum means a truncated or altered transfer — do not restore it. A half-written blob does not produce a clean error at run time; it produces a model that loads and then fails mid-generation, which reads like an application bug.

## Restoring into the target

The archive expands into the Ollama data volume. The path depends on how Ollama is deployed.

### Docker Compose

The compose stack mounts the named volume `ollama_models` at **`/root/.ollama`** inside the `ollama` service, so the model store is `/root/.ollama/models`. The service sits behind the `local-llm` profile — bring it up with `make dev-llm` (or `docker compose --profile local-llm up -d ollama`) before restoring.

```bash
# Copy the pack in and expand it into the model store.
docker compose cp testlookup-model-pack.tar.gz ollama:/tmp/pack.tar.gz
docker compose exec -T ollama tar -C /root/.ollama/models -xzf /tmp/pack.tar.gz
docker compose exec -T ollama rm /tmp/pack.tar.gz

# Ollama re-reads the store on start; restart to be certain it is picked up.
docker compose restart ollama
docker compose exec -T ollama ollama list
```

Because the data lives in the `ollama_models` volume, it survives `docker compose down` — but **not** `docker compose down -v`, which deletes the volume and the pack with it.

### Kubernetes (K3s / OpenShift)

The `testlookup-ollama` deployment mounts the PVC **`ollama-models-pvc`** at `/root/.ollama`, so the model store lives at **`/root/.ollama/models`** inside the pod.

```bash
NS=testlookup
POD=$(kubectl -n $NS get pod -l app=testlookup-ollama -o name | head -1)

kubectl -n $NS cp testlookup-model-pack.tar.gz ${POD#pod/}:/tmp/pack.tar.gz
kubectl -n $NS exec $POD -- tar -C /root/.ollama/models -xzf /tmp/pack.tar.gz
kubectl -n $NS exec $POD -- rm /tmp/pack.tar.gz
kubectl -n $NS exec $POD -- ollama list
```

Because the PVC survives pod restarts, this is a **one-time step per environment**, not per deploy. If the PVC is ever recreated, the pack must be restored again.

Notes that save time:

- Expand **into** `models/`, not over it. The archive contains `blobs/` and `manifests/`; extracting at the wrong level produces `models/models/blobs`, and Ollama then reports no models with no error.
- Ownership matters on package installs: if Ollama runs as the `ollama` user, `chown -R ollama:ollama /usr/share/ollama/.ollama/models` after extracting.
- Restoring is additive. Models already present are untouched; blobs are content-addressed, so re-restoring the same pack is a no-op.

## Verifying it worked, from TestLookup

`ollama list` proves the daemon has the files. It does not prove TestLookup agrees — the configured model name has to match, and the backend has to be able to reach the daemon. Check the application's own view:

**In the UI:** **Settings → AI Configuration → Model Availability & Fallback Chain**. It shows, live:

- whether Ollama is **reachable**, and at which URL;
- each required model — LLM, embedding, and classifier when configured — as **Installed** or **Missing**, with the exact remedy command for your runtime when one is missing;
- the **fallback chain** (ML → LLM → Rules) with each tier marked available or unavailable *and the reason*, plus which tier is currently **Active**.

**Via the API** (QA Lead or higher):

```bash
curl -s -H "Authorization: Bearer $TOKEN" \
  http://localhost:8000/api/v1/settings/ai/model-status | jq
```

Read the two failure states as distinct problems — the page keeps them apart on purpose:

| What you see | What it means | What to do |
|---|---|---|
| `ollama_reachable: false` | Connectivity. The backend cannot reach `OLLAMA_BASE_URL` at all. Says nothing about your models. | Check the Ollama service/pod is up and the URL is right. Pulling or restoring won't help yet. |
| `ollama_reachable: true`, a required model `present: false` | The daemon is fine; the pack didn't land, or the name doesn't match. | Compare `ollama list` against `LLM_MODEL` / `EMBEDDING_MODEL` — exact tag, including `:7b`. Re-extract if a blob is missing. |
| LLM tier unavailable, rules available | Triage still runs, on the rules engine. Results are shallower, not absent. | Fix the above; no data is lost in the meantime. |

Model presence is matched on the **exact** Ollama name, treating a missing tag as `:latest`. `qwen2.5:14b` does **not** satisfy `LLM_MODEL=qwen2.5:7b` — a same-family, different-size tag is reported as missing, because at run time the agent asks for the configured name and gets nothing.

## How this relates to `AI_OFFLINE_MODE`

`AI_OFFLINE_MODE` defaults to **true** and is the switch that keeps TestLookup from talking to any cloud AI provider. Local models are exactly what makes that safe: with offline mode on, the model pack is the *only* thing standing between you and no AI features at all.

- **Offline mode on + models present** → full AI triage, entirely inside your network.
- **Offline mode on + models missing** → cloud providers stay blocked (as intended) and analysis degrades to the rules engine. The settings page reports this rather than failing silently.
- **Offline mode off** → cloud provider keys become usable. Nothing here requires it, and air-gapped installs should leave offline mode on.

One related quirk worth knowing: `/health/details` only reports on Ollama when `AI_OFFLINE_MODE` is true, because offline mode means "local models only". A deployment running Ollama with offline mode *off* sees `"ollama": {"status": "skipped"}` there even though Ollama is in use. The **model-status endpoint above keys off the configured provider instead**, so it is the accurate source for "is my model backend healthy".

## Automating it

Deployment scripts pull the default models when the host has egress — `homelabsetup/deploy-homelab.sh` (skippable with `--skip-models`) and `openshiftsetup/deploy-openshift-artifactory.sh`, which warns `Could not pull <model> (air-gapped Ollama?)` and carries on. **That warning is the signal to run this procedure.** The deploy is not broken; it simply couldn't fetch weights, and the install will run on rules until a pack is restored.

If you image environments repeatedly, keep the pack and its `.sha256` in your artifact repository next to the release bundle, and make restoring it a step in the environment build — the same way you'd stage base container images.

## Related

- [AI features](ai-features.md) — what each analysis tier actually does
- [Administration & settings](administration.md) — the AI Configuration page in context
- [Sizing & capacity](sizing.md) — RAM/CPU/disk planning per profile
- [Troubleshooting & FAQ](troubleshooting.md) — symptom-first index
