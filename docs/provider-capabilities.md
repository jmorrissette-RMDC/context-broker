# Provider Capabilities Matrix

**Date:** 2026-03-26
**Purpose:** Reference for which inference providers support which capabilities, and what protocols they use. Informs configuration and testing.

## Capability Matrix

| Provider | LLM | Embeddings | Reranking | Notes |
|----------|-----|------------|-----------|-------|
| **OpenAI** | Yes | Yes (`/v1/embeddings`) | No | Relies on third parties for reranking |
| **Anthropic** | Yes | No | No | Partners with Voyage AI for embeddings/reranking |
| **Google Gemini** | Yes | Yes | No | Native: `/v1beta/models/...:embedContent` with task types. OpenAI compat layer available but loses task type support |
| **xAI (Grok)** | Yes | No | No | Focused on LLM/Vision |
| **Together AI** | Yes | Yes (`/v1/embeddings`) | Yes (`/v1/rerank`) | Full stack |
| **Ollama** | Yes | Yes (`/v1/embeddings`) | No | Local LLM inference. Requires `tiktoken_enabled: false` for embeddings |
| **Infinity** | No | Yes (`/v1/embeddings`) | Yes (`/v1/rerank`) | Local embeddings and reranking. CPU or GPU. Serves any HuggingFace model |
| **Cohere** | Yes | Yes (own format) | Yes (`/v2/rerank`) | Gold standard for reranking |
| **Jina** | No | Yes | Yes (`/v1/rerank`) | 8K token context for embeddings/reranking — best for long chunks |
| **Voyage AI** | No | Yes (`/v1/embeddings`) | Yes | Anthropic's recommended partner. Competitive rerank models |

## Protocol Standards

### LLM (Chat Completions)
- **Standard:** OpenAI `/v1/chat/completions`
- **Adopted by:** All 6 LLM providers (OpenAI, Anthropic via adapter, Google, xAI, Together, Ollama)
- **LangChain class:** `ChatOpenAI` (OpenAI-compatible), `ChatAnthropic` (native)

### Embeddings
- **Standard:** OpenAI `/v1/embeddings` with `{"model": "...", "input": "text"}`
- **Adopted by:** OpenAI, Together, Ollama, Voyage AI
- **Exception:** Google Gemini has native format with task types (`RETRIEVAL_QUERY` vs `RETRIEVAL_DOCUMENT`). OpenAI compat layer available but loses task type support, impacting search quality.
- **Exception:** Cohere uses own format
- **LangChain class:** `OpenAIEmbeddings` (set `tiktoken_enabled: false` for non-OpenAI providers)
- **Note:** Anthropic and xAI do not offer embeddings. Use a separate provider.

### Reranking
- **Converging standard:** `POST /v1/rerank` with `{"model": "...", "query": "...", "documents": [...], "top_n": N}`
- **Request format:** Identical across Together, Jina, Voyage. Cohere uses `/v2/rerank` but same body.
- **Response format:** Results with `index` and `relevance_score`. Key name varies: `results[]` (Cohere), `choices[]` (Together), `data[]` (others).
- **Document format variation:** Simple (`documents: ["text1", "text2"]`) vs complex (`documents: [{"text": "...", "metadata": "..."}]`). Jina supports both.
- **Providers:** Infinity (local), Together, Cohere, Jina, Voyage AI
- **Local option:** Infinity container serving any HuggingFace reranker model (e.g., `BAAI/bge-reranker-v2-m3`) via `/v1/rerank`
- **Not available from:** OpenAI, Anthropic, Google, xAI, Ollama

## Configuration Implications

### For Context Broker deployment:

1. **LLM:** Any of the 6 providers works. Set `base_url` and `model` in config. API key via standard env var.

2. **Embeddings:** Choose from OpenAI, Together, Ollama (local), Google, or Voyage. Anthropic and xAI users need a separate embedding provider. Config specifies `base_url`, `model`, and `tiktoken_enabled`.

3. **Reranking:** Two options:
   - `provider: api` — hits any `/v1/rerank` endpoint. Default: local Infinity container. Also works with Together, Jina, Voyage, Cohere.
   - `provider: none` — skip reranking, use raw RRF scores

4. **Minimum viable deployment:** Ollama (LLM) + Infinity (embeddings + reranking). Free, local, no API keys.

5. **Full-featured deployment:** Cloud LLM (OpenAI/Anthropic) + cloud embeddings (OpenAI/Voyage) + cloud reranking (Together/Cohere/Jina). Or keep Infinity local for embeddings/reranking to avoid per-call costs.

## Known Limitations

### Gemini JSON Extraction (EXT-04)
Google Gemini's OpenAI-compatible endpoint does not enforce `response_format: json_object`. When used for extraction (via Mem0), the LLM may return non-JSON responses that fail parsing. The native Gemini API supports JSON enforcement, but Mem0 0.1.29 only supports the OpenAI-compatible endpoint. **Workaround:** Use GPT-4o-mini, xAI, or Ollama for extraction. Gemini works fine for the Imperator and summarization.

### Anthropic Extraction
Anthropic's OpenAI-compatible endpoint does not support `response_format` at all. Not usable for extraction. Works for Imperator conversation.

### Local Out-of-Box Verification
Verified working without any API keys:
- **Imperator:** Ollama qwen2.5:7b (conversation + tool calling)
- **Summarization:** Ollama qwen2.5:7b
- **Extraction:** Ollama qwen2.5:7b (valid JSON via `response_format: json_object`)
- **Embeddings:** Infinity nomic-ai/nomic-embed-text-v1.5 (768 dims)
- **Reranking:** Infinity mixedbread-ai/mxbai-rerank-xsmall-v1

### Inference Models Catalog
A curated model catalog is shipped at `/config/inference-models.yml`. The `change_inference` Imperator tool reads this catalog to list available models per slot and switch between them. The catalog includes flagship and cost-effective options per provider, with extraction restricted to providers with verified JSON mode enforcement.

## Testing Requirements

Cross-provider tests must verify:
- LLM: One cheap model per provider (6 tests)
- Embeddings: One model per embedding-capable provider (5 tests: OpenAI, Google, Together, Ollama, Voyage)
- Reranking: One model per reranking-capable provider (4 tests: Infinity, Together, Cohere, Jina)
