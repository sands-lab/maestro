"""Command-line CRAG benchmark translated from the langgraph_crag.ipynb notebook."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import logging
import operator
import os
import sys
import time
import warnings
from contextlib import nullcontext
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Dict, List, Optional, Sequence

from chromadb.config import Settings
from langchain_community.document_loaders import WebBaseLoader
from langchain_community.vectorstores import Chroma
from langchain_community.tools.tavily_search import TavilySearchResults
from langchain_core.documents import Document
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import RunnableConfig
from langchain_openai import ChatOpenAI, OpenAIEmbeddings
try:  # pragma: no cover - optional dependency
    from langchain_google_vertexai import ChatVertexAI, VertexAIEmbeddings
except Exception:  # pragma: no cover
    ChatVertexAI = None  # type: ignore
    VertexAIEmbeddings = None  # type: ignore

try:  # pragma: no cover - compatibility shim for LangChain 0.2+
    from langchain_text_splitters import RecursiveCharacterTextSplitter
except ImportError:  # pragma: no cover - older versions
    from langchain.text_splitter import RecursiveCharacterTextSplitter
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.errors import GraphRecursionError
from langgraph.graph import END, START, StateGraph
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.trace import Status, StatusCode
from pydantic import BaseModel, Field
from typing_extensions import TypedDict

from maestro.telemetry_helpers.langgraph_otel import (
    DEFAULT_ENVIRONMENT,
    AgentFailureCategory,
    AgentRetryTrigger,
    evaluate_answer,
    record_run_judgement,
    check_timeout,
    set_run_outcome,
    PsutilMetricsRecorder,
    invoke_agent_span,
    record_invoke_agent_output,
    run_llm_with_span,
    run_tool_with_span,
    set_agent_failure_attributes,
    set_agent_usefulness,
    span_id_hex,
    setup_jsonl_tracing,
)

warnings.filterwarnings(
    "ignore",
    message=r"The class `TavilySearchResults` was deprecated.*",
    category=DeprecationWarning,
)

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("crag-benchmark")

APP_NAME = "crag_benchmark"
BENCHMARK_ROOT = Path(__file__).resolve().parent
LOG_DIR = BENCHMARK_ROOT / "logs"
METRICS_DIR = BENCHMARK_ROOT / "metrics"
CACHE_DIR = BENCHMARK_ROOT / ".cache"
TRACE_SERVICE_NAME = "crag-benchmark"
TRACE_SERVICE_VERSION = "1.0.0"
METADATA_VERSION = 1
DEFAULT_OBJECTIVE = "What are the types of agent memory?"
DEFAULT_SEED_URLS = [
    "https://lilianweng.github.io/posts/2023-06-23-agent/",
    "https://lilianweng.github.io/posts/2023-03-15-prompt-engineering/",
    "https://lilianweng.github.io/posts/2023-10-25-adv-attack-llm/",
]
DEFAULT_CHUNK_SIZE = 250
DEFAULT_CHUNK_OVERLAP = 0
DEFAULT_RETRIEVAL_K = 4
DEFAULT_WEB_RESULTS = 3
DEFAULT_GENERATOR_MODEL = "gpt-4.1-mini"
DEFAULT_GRADER_MODEL = "gpt-4.1-mini"
DEFAULT_REWRITER_MODEL = "gpt-4o-mini"
DEFAULT_EMBEDDING_MODEL = "text-embedding-3-small"
DEFAULT_METRICS_INTERVAL = float(os.getenv("CRAG_METRICS_INTERVAL_SECONDS", "15") or 15.0)
DEFAULT_PROVIDER = "openai"


class GradeDocuments(BaseModel):
    """Binary score for relevance check on retrieved documents."""

    binary_score: str = Field(
        description="Documents are relevant to the question, 'yes' or 'no'."
    )


class CRAGState(TypedDict, total=False):
    """LangGraph state shared across CRAG nodes."""

    original_question: str
    question: str
    rewritten_question: str
    documents: List[Document]
    generation: str
    retrieved_count: int
    filtered_count: int
    web_search: str
    web_search_needed: bool
    web_search_used: bool
    tavily_results: int
    events: Annotated[List[str], operator.add]
    search_retry_attempts: int
    search_previous_span_id: str


@dataclass
class QuestionResult:
    index: int
    question: str
    rewritten_question: Optional[str]
    generation: Optional[str]
    retrieved_count: int
    filtered_count: int
    final_document_count: int
    used_web_search: bool
    tavily_results: int
    duration: float
    stream_events: List[str]
    node_events: List[str]
    doc_sources: List[str]
    judgement: Optional[str] = None
    judgement_reason: Optional[str] = None
    gold_answer: Optional[str] = None
    error: Optional[str] = None

    @property
    def status(self) -> str:
        return "error" if self.error else "ok"

    def to_metadata(self) -> Dict[str, object]:
        return {
            "index": self.index,
            "question": self.question,
            "rewritten_question": self.rewritten_question,
            "generation": self.generation,
            "retrieved_documents": self.retrieved_count,
            "filtered_documents": self.filtered_count,
            "final_document_count": self.final_document_count,
            "used_web_search": self.used_web_search,
            "tavily_results": self.tavily_results,
            "duration_seconds": self.duration,
            "stream_events": self.stream_events,
            "node_events": self.node_events,
            "document_sources": self.doc_sources,
            "judgement": self.judgement,
            "judgement_reason": self.judgement_reason,
            "gold_answer_present": bool(self.gold_answer),
            "error": self.error,
            "status": self.status,
        }


@dataclass
class QuestionRecord:
    prompt: str
    answer: Optional[str] = None
    metadata: Optional[Dict[str, str]] = None


def _truncate(text: str, limit: int = 100) -> str:
    snippet = text.strip().replace("\n", " ")
    if len(snippet) <= limit:
        return snippet
    return snippet[: limit - 3] + "..."


def _relative_path(path: Path) -> str:
    try:
        return os.path.relpath(path, start=BENCHMARK_ROOT)
    except ValueError:
        return str(path)


def load_questions_file(path: Path) -> List[QuestionRecord]:
    if not path.exists():
        raise FileNotFoundError(f"Questions file not found: {path}")
    suffix = path.suffix.lower()
    if suffix == ".txt":
        lines = [
            line.strip()
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        return [QuestionRecord(prompt=line) for line in lines]
    if suffix == ".csv":
        questions: List[QuestionRecord] = []
        with path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            if not reader.fieldnames:
                return []
            lower_to_actual = {col.lower(): col for col in reader.fieldnames}
            question_col = lower_to_actual.get("question")
            answer_col = lower_to_actual.get("answer")
            if not question_col:
                raise ValueError("CSV file must contain a 'question' column.")
            for row in reader:
                value = (row.get(question_col) or "").strip()
                if not value:
                    continue
                answer = (row.get(answer_col) or "").strip() if answer_col else None
                questions.append(
                    QuestionRecord(prompt=value, answer=answer, metadata=row)
                )
        return questions
    raise ValueError("Questions file must be .txt or .csv")


def _cache_path_for_url(url: str) -> Path:
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()
    return CACHE_DIR / f"{digest}.json"


def load_seed_documents(urls: Sequence[str]) -> List[Document]:
    documents: List[Document] = []
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    for url in urls:
        cache_path = _cache_path_for_url(url)
        if cache_path.exists():
            try:
                cached = json.loads(cache_path.read_text(encoding="utf-8"))
                docs = [
                    Document(
                        page_content=item.get("page_content", ""),
                        metadata=item.get("metadata", {}),
                    )
                    for item in cached
                ]
                documents.extend(docs)
                logger.info("Loaded %s cached documents from %s", len(docs), url)
                continue
            except Exception as exc:  # pragma: no cover - best effort
                logger.warning("Failed to load cache for %s: %s; refetching", url, exc)
        loader = WebBaseLoader(url)
        docs = loader.load()
        logger.info("Loaded %s documents from %s", len(docs), url)
        documents.extend(docs)
        try:
            cache_payload = [
                {"page_content": doc.page_content, "metadata": getattr(doc, "metadata", {})}
                for doc in docs
            ]
            cache_path.write_text(json.dumps(cache_payload), encoding="utf-8")
        except Exception as exc:  # pragma: no cover - cache best effort
            logger.warning("Failed to write cache for %s: %s", url, exc)
    if not documents:
        raise RuntimeError("No documents could be loaded from the supplied URLs.")
    return documents


def _make_chat_model(
    provider: str,
    model: str,
    temperature: float,
    *,
    vertex_project: Optional[str],
    vertex_location: Optional[str],
):
    if provider == "openai":
        return ChatOpenAI(model=model, temperature=temperature)
    if provider == "google-vertex":
        if ChatVertexAI is None:
            raise ImportError(
                "langchain-google-vertexai is required for provider=google-vertex. "
                "Install it via `pip install langchain-google-vertexai`."
            )
        project = vertex_project or os.getenv("GOOGLE_CLOUD_PROJECT")
        if not project:
            raise EnvironmentError(
                "Vertex AI provider requires --vertex-project or GOOGLE_CLOUD_PROJECT."
            )
        location = vertex_location or os.getenv("GOOGLE_CLOUD_REGION") or "us-central1"
        return ChatVertexAI(
            project=project,
            location=location,
            model_name=model,
            temperature=temperature,
        )
    raise ValueError(f"Unsupported provider: {provider}")


def _make_embeddings(
    provider: str,
    model: str,
    *,
    vertex_project: Optional[str],
    vertex_location: Optional[str],
):
    if provider == "openai":
        return OpenAIEmbeddings(model=model)
    if provider == "google-vertex":
        if VertexAIEmbeddings is None:
            raise ImportError(
                "langchain-google-vertexai is required for provider=google-vertex embeddings. "
                "Install it via `pip install langchain-google-vertexai`."
            )
        project = vertex_project or os.getenv("GOOGLE_CLOUD_PROJECT")
        if not project:
            raise EnvironmentError(
                "Vertex AI provider requires --vertex-project or GOOGLE_CLOUD_PROJECT."
            )
        location = vertex_location or os.getenv("GOOGLE_CLOUD_REGION") or "us-central1"
        return VertexAIEmbeddings(
            project=project,
            location=location,
            model_name=model,
        )
    raise ValueError(f"Unsupported provider: {provider}")


def _estimate_tokens(texts: Sequence[str]) -> int:
    """Crude token estimator when API usage is unavailable."""
    total = 0
    for t in texts:
        if not isinstance(t, str):
            continue
        total += max(1, len(t) // 4)
    return total


class TracedEmbeddings:
    """Wrap embeddings to emit OTEL spans and rough token usage."""

    def __init__(self, base_embedding, tracer, system: str, model: str):
        self._base = base_embedding
        self._tracer = tracer
        self._system = system
        self._model = model

    def embed_documents(self, texts: Sequence[str], **kwargs):
        estimated_tokens = _estimate_tokens(texts)
        span_cm = (
            self._tracer.start_as_current_span("crag.embed_documents")
            if self._tracer
            else nullcontext((None))
        )
        with span_cm as span:
            if span:
                span.set_attribute("gen_ai.system", self._system)
                span.set_attribute("gen_ai.request.model", self._model)
                span.set_attribute("gen_ai.operation.name", "embed_documents")
                span.set_attribute("gen_ai.usage.input_tokens", estimated_tokens)
                span.set_attribute("gen_ai.usage.total_tokens", estimated_tokens)
            try:
                result = self._base.embed_documents(texts, **kwargs)
            except Exception as exc:  # pragma: no cover
                if span:
                    span.record_exception(exc)
                    span.set_status(Status(StatusCode.ERROR, str(exc)))
                raise
        return result

    def embed_query(self, text: str, **kwargs):
        estimated_tokens = _estimate_tokens([text])
        span_cm = (
            self._tracer.start_as_current_span("crag.embed_query")
            if self._tracer
            else nullcontext((None))
        )
        with span_cm as span:
            if span:
                span.set_attribute("gen_ai.system", self._system)
                span.set_attribute("gen_ai.request.model", self._model)
                span.set_attribute("gen_ai.operation.name", "embed_query")
                span.set_attribute("gen_ai.usage.input_tokens", estimated_tokens)
                span.set_attribute("gen_ai.usage.total_tokens", estimated_tokens)
            try:
                result = self._base.embed_query(text, **kwargs)
            except Exception as exc:  # pragma: no cover
                if span:
                    span.record_exception(exc)
                    span.set_status(Status(StatusCode.ERROR, str(exc)))
                raise
        return result


def build_retriever(
    urls: Sequence[str],
    *,
    chunk_size: int,
    chunk_overlap: int,
    embedding_model: str,
    provider: str,
    vertex_project: Optional[str],
    vertex_location: Optional[str],
    tracer: Optional[trace.Tracer],
) -> object:
    documents = load_seed_documents(urls)
    text_splitter = RecursiveCharacterTextSplitter.from_tiktoken_encoder(
        chunk_size=chunk_size, chunk_overlap=chunk_overlap
    )
    doc_splits = text_splitter.split_documents(documents)
    logger.info(
        "Split %s documents into %s chunks (chunk_size=%s overlap=%s)",
        len(documents),
        len(doc_splits),
        chunk_size,
        chunk_overlap,
    )
    embeddings = _make_embeddings(
        provider,
        embedding_model,
        vertex_project=vertex_project,
        vertex_location=vertex_location,
    )
    gen_ai_system = "google_vertex" if provider == "google-vertex" else provider
    embeddings = TracedEmbeddings(embeddings, tracer, gen_ai_system, embedding_model)
    client_settings = Settings(anonymized_telemetry=False, is_persistent=False)
    collection_name = f"crag-{int(time.time() * 1000)}"
    vectorstore = Chroma.from_documents(
        documents=doc_splits,
        collection_name=collection_name,
        embedding=embeddings,
        client_settings=client_settings,
    )
    return vectorstore


def build_retrieval_grader(
    model: str,
    provider: str,
    vertex_project: Optional[str],
    vertex_location: Optional[str],
) -> object:
    llm = _make_chat_model(
        provider,
        model,
        0,
        vertex_project=vertex_project,
        vertex_location=vertex_location,
    )
    structured_llm_grader = llm.with_structured_output(GradeDocuments)
    system = (
        "You are a grader assessing relevance of a retrieved document to a user question.\n"
        "If the document contains keywords or semantic meaning related to the question, grade it as relevant.\n"
        "Give a binary 'yes' or 'no' score to indicate relevance."
    )
    grade_prompt = ChatPromptTemplate.from_messages(
        [
            ("system", system),
            ("human", "Retrieved document:\n\n{document}\n\nUser question: {question}"),
        ]
    )
    return grade_prompt | structured_llm_grader


def build_question_rewriter(
    model: str,
    provider: str,
    vertex_project: Optional[str],
    vertex_location: Optional[str],
) -> object:
    llm = _make_chat_model(
        provider,
        model,
        0,
        vertex_project=vertex_project,
        vertex_location=vertex_location,
    )
    system = (
        "You rewrite user questions so they are better optimized for web search.\n"
        "Look at the input and produce a more explicit query that captures the semantic intent."
    )
    prompt = ChatPromptTemplate.from_messages(
        [
            ("system", system),
            (
                "human",
                "Here is the initial question:\n\n{question}\n\nFormulate an improved question.",
            ),
        ]
    )
    return prompt | llm | StrOutputParser()


def build_rag_chain(
    model: str,
    provider: str,
    vertex_project: Optional[str],
    vertex_location: Optional[str],
) -> object:
    prompt = ChatPromptTemplate.from_messages(
        [
            (
                "system",
                "You are a helpful assistant that uses retrieved context to answer the question.\n"
                "Use only the provided context. Mention sources when possible.",
            ),
            ("human", "Context:\n{context}\n\nQuestion: {question}"),
        ]
    )
    llm = _make_chat_model(
        provider,
        model,
        0,
        vertex_project=vertex_project,
        vertex_location=vertex_location,
    )
    return prompt | llm | StrOutputParser()


def format_docs(docs: Sequence[Document]) -> str:
    return "\n\n".join(doc.page_content for doc in docs if doc.page_content)


def build_graph(
    *,
    retriever,
    retrieval_grader,
    rag_chain,
    question_rewriter,
    web_search_tool: Optional[TavilySearchResults],
    tracer: trace.Tracer | None,
    generator_model: str,
    grader_model: str,
    rewriter_model: str,
    enable_web_search: bool,
    max_search_results: int,
    provider: str,
) -> StateGraph:
    node_tracer = tracer or trace.get_tracer(APP_NAME)
    gen_ai_system = "google_vertex" if provider == "google-vertex" else provider

    def retrieve(state: CRAGState, *, config: Optional[RunnableConfig] = None) -> Dict[str, object]:
        question = state["question"]
        span_cm = (
            invoke_agent_span(
                node_tracer,
                "crag.node.retrieve",
                agent_name=f"{APP_NAME}.node.retrieve",
                payload={"question": question},
            )
            if node_tracer
            else nullcontext((None, 0))
        )
        with span_cm as (span, input_bytes):
            documents = retriever.invoke(question)
            doc_sources = [
                getattr(doc, "metadata", {}).get("source", "unknown") for doc in documents
            ]
            if span:
                span.set_attribute("crag.retrieved_documents", len(documents))
                span.set_attribute("crag.retrieve.sources", json.dumps(doc_sources))
                record_invoke_agent_output(span, doc_sources, input_bytes)
        event = f"Retrieved {len(documents)} documents for '{_truncate(question)}'"
        return {
            "documents": documents,
            "retrieved_count": len(documents),
            "web_search": "No",
            "events": [event],
            "search_retry_attempts": int(state.get("search_retry_attempts") or 0),
            "search_previous_span_id": state.get("search_previous_span_id"),
        }

    def grade_documents(state: CRAGState, *, config: Optional[RunnableConfig] = None) -> Dict[str, object]:
        question = state["question"]
        documents = list(state.get("documents") or [])
        filtered_docs: List[Document] = []
        web_search_flag = "No"
        for idx, doc in enumerate(documents):
            payload = {"question": question, "document": doc.page_content}

            def _invoke(updated_config: Optional[RunnableConfig]):
                return retrieval_grader.invoke(payload, config=updated_config)

            result = run_llm_with_span(
                node_tracer,
                "crag.call_llm.grade_documents",
                agent_name=f"{APP_NAME}.llm",
                phase="grade_documents",
                config=config,
                invoke_fn=_invoke,
                extra_attributes={
                    "crag.document_index": idx,
                    "gen_ai.system": gen_ai_system,
                    "gen_ai.request.model": grader_model,
                },
            )
            label = (result.binary_score or "").strip().lower()
            if label == "yes":
                filtered_docs.append(doc)
            else:
                web_search_flag = "Yes"
        summary = (
            f"Grader kept {len(filtered_docs)}/{len(documents)} docs "
            f"(web_search={web_search_flag})"
        )
        prior_attempts = int(state.get("search_retry_attempts") or 0)
        retry_attempts = prior_attempts + 1 if web_search_flag == "Yes" else 0
        return {
            "documents": filtered_docs,
            "filtered_count": len(filtered_docs),
            "web_search": web_search_flag,
            "web_search_needed": web_search_flag == "Yes",
            "events": [summary],
            "search_retry_attempts": retry_attempts,
            "search_previous_span_id": state.get("search_previous_span_id"),
        }

    def transform_query(state: CRAGState, *, config: Optional[RunnableConfig] = None) -> Dict[str, object]:
        question = state["question"]

        def _invoke(updated_config: Optional[RunnableConfig]):
            return question_rewriter.invoke({"question": question}, config=updated_config)

        prior_attempts = int(state.get("search_retry_attempts") or 0)
        previous_span_id = state.get("search_previous_span_id")
        retry_context = None
        if prior_attempts:
            retry_context = {
                "retry": {
                    "attempt_number": prior_attempts + 1,
                    "trigger": AgentRetryTrigger.RELEVANCE_GUARD,
                    "reason": "Rewriting query after relevance grading requested web search.",
                }
            }
            if previous_span_id:
                retry_context["retry"]["previous_span_id"] = previous_span_id

        def _annotate_transform(span, _result):
            span_hex = span_id_hex(span)
            if span_hex:
                state["search_previous_span_id"] = span_hex

        rewritten = run_llm_with_span(
            node_tracer,
            "crag.call_llm.transform_query",
            agent_name=f"{APP_NAME}.llm",
            phase="transform_query",
            config=config,
            invoke_fn=_invoke,
            extra_attributes={
                "gen_ai.system": gen_ai_system,
                "gen_ai.request.model": rewriter_model,
            },
            agent_context=retry_context,
            postprocess_fn=_annotate_transform,
        )
        event = f"Rewrote question to '{_truncate(rewritten)}'"
        return {
            "question": rewritten,
            "rewritten_question": rewritten,
            "events": [event],
            "search_retry_attempts": int(state.get("search_retry_attempts") or 0),
            "search_previous_span_id": state.get("search_previous_span_id"),
        }

    def web_search_node(
        state: CRAGState, *, config: Optional[RunnableConfig] = None
    ) -> Dict[str, object]:
        documents = list(state.get("documents") or [])
        question = state["question"]
        events: List[str] = []
        used_search = False
        appended = 0
        if enable_web_search and web_search_tool:
            payload = {"query": question}

            def _invoke_tool(invocation_payload: Dict[str, str], updated_config: Optional[RunnableConfig]):
                return web_search_tool.invoke(invocation_payload)

            retry_attempts = int(state.get("search_retry_attempts") or 0)
            previous_span_id = state.get("search_previous_span_id")
            retry_context = None
            if retry_attempts:
                retry_context = {
                    "retry": {
                        "attempt_number": retry_attempts + 1,
                        "trigger": AgentRetryTrigger.RELEVANCE_GUARD,
                        "reason": "Retrying retrieval via Tavily after document grading.",
                    }
                }
                if previous_span_id:
                    retry_context["retry"]["previous_span_id"] = previous_span_id

            def _count_appendable(result_obj: Any) -> int:
                iterable = result_obj if isinstance(result_obj, list) else [result_obj]
                count = 0
                for item in iterable:
                    if not isinstance(item, dict):
                        continue
                    content = item.get("content") or item.get("snippet") or ""
                    if content:
                        count += 1
                return count

            def _annotate_web_search(span, result_obj):
                span_hex = span_id_hex(span)
                if span_hex:
                    state["search_previous_span_id"] = span_hex
                appended_count = _count_appendable(result_obj)
                if appended_count == 0:
                    failures = int(state.get("search_retry_attempts") or 0)
                    set_agent_failure_attributes(
                        span,
                        category=AgentFailureCategory.QUALITY,
                        reason="Tavily search returned no usable content.",
                    )
                    set_agent_usefulness(
                        span,
                        is_useless=True,
                        reason="No Tavily results could be appended.",
                    )
                    state["search_retry_attempts"] = failures + 1
                else:
                    set_agent_usefulness(
                        span,
                        is_useless=False,
                        reason="Tavily returned usable snippets.",
                    )
                    state["search_retry_attempts"] = 0

            results = run_tool_with_span(
                node_tracer,
                "crag.execute_tool.tavily",
                agent_name=f"{APP_NAME}.tool.tavily",
                tool_name="tavily_search",
                payload=payload,
                invoke_fn=_invoke_tool,
                config=config,
                extra_attributes={
                    "gen_ai.tool.name": "tavily_search_results",
                    "gen_ai.system": "tavily",
                    "crag.web_results_requested": max_search_results,
                },
                agent_context=retry_context,
                postprocess_fn=_annotate_web_search,
            )
            iterable = results if isinstance(results, list) else [results]
            for item in iterable:
                if not isinstance(item, dict):
                    continue
                content = item.get("content") or item.get("snippet") or ""
                if not content:
                    continue
                metadata = {"source": item.get("url") or "tavily"}
                documents.append(Document(page_content=content, metadata=metadata))
                appended += 1
            used_search = appended > 0
            events.append(f"Web search appended {appended} Tavily results.")
        else:
            events.append("Web search disabled; skipping Tavily execution.")
        return {
            "documents": documents,
            "web_search": "Performed",
            "web_search_used": used_search,
            "tavily_results": appended,
            "events": events,
            "search_retry_attempts": int(state.get("search_retry_attempts") or 0),
            "search_previous_span_id": state.get("search_previous_span_id"),
        }

    def generate(state: CRAGState, *, config: Optional[RunnableConfig] = None) -> Dict[str, object]:
        question = state["question"]
        documents = list(state.get("documents") or [])
        context_text = format_docs(documents)

        def _invoke(updated_config: Optional[RunnableConfig]):
            return rag_chain.invoke(
                {"context": context_text, "question": question},
                config=updated_config,
            )

        retry_attempts = int(state.get("search_retry_attempts") or 0)
        previous_span_id = state.get("search_previous_span_id")
        retry_context = None
        if retry_attempts:
            retry_context = {
                "retry": {
                    "attempt_number": retry_attempts + 1,
                    "trigger": AgentRetryTrigger.RELEVANCE_GUARD,
                    "reason": "Generating answer after remedial retrieval attempts.",
                }
            }
            if previous_span_id:
                retry_context["retry"]["previous_span_id"] = previous_span_id

        def _annotate_generate(span, _result):
            span_hex = span_id_hex(span)
            if span_hex:
                state["search_previous_span_id"] = span_hex

        answer = run_llm_with_span(
            node_tracer,
            "crag.call_llm.generate",
            agent_name=f"{APP_NAME}.llm",
            phase="generate",
            config=config,
            invoke_fn=_invoke,
            extra_attributes={
                "gen_ai.system": gen_ai_system,
                "gen_ai.request.model": generator_model,
            },
            agent_context=retry_context,
            postprocess_fn=_annotate_generate,
        )
        event = f"Generated answer ({len(answer)} chars)."
        return {
            "generation": answer,
            "events": [event],
            "search_retry_attempts": int(state.get("search_retry_attempts") or 0),
            "search_previous_span_id": state.get("search_previous_span_id"),
        }

    def decide_to_generate(state: CRAGState) -> str:
        if state.get("web_search") == "Yes":
            return "transform_query"
        return "generate"

    workflow = StateGraph(CRAGState)
    workflow.add_node("retrieve", retrieve)
    workflow.add_node("grade_documents", grade_documents)
    workflow.add_node("transform_query", transform_query)
    workflow.add_node("web_search", web_search_node)
    workflow.add_node("generate", generate)
    workflow.add_edge(START, "retrieve")
    workflow.add_edge("retrieve", "grade_documents")
    workflow.add_conditional_edges(
        "grade_documents",
        decide_to_generate,
        {
            "transform_query": "transform_query",
            "generate": "generate",
        },
    )
    workflow.add_edge("transform_query", "web_search")
    workflow.add_edge("web_search", "generate")
    workflow.add_edge("generate", END)
    return workflow.compile(checkpointer=InMemorySaver())


def _summarize_event(event: Dict[str, object]) -> str:
    parts: List[str] = []
    for node, payload in event.items():
        if isinstance(payload, dict):
            keys = payload.get("keys") if isinstance(payload.get("keys"), dict) else payload
        else:
            keys = payload
        summary: Dict[str, object] = {}
        if isinstance(keys, dict):
            question = keys.get("question")
            generation = keys.get("generation")
            docs = keys.get("documents")
            web_search = keys.get("web_search")
            if question:
                summary["question"] = _truncate(str(question), 60)
            if generation:
                summary["generation"] = _truncate(str(generation), 60)
            if isinstance(docs, list):
                summary["documents"] = len(docs)
            if web_search:
                summary["web_search"] = web_search
        else:
            summary["value"] = str(keys)
        parts.append(f"{node}: {summary}")
    return " | ".join(parts)


def run_question(
    app,
    question: QuestionRecord,
    index: int,
    tracer: trace.Tracer | None,
    *,
    quiet: bool,
    evaluator: str,
    judge_llm=None,
    timeout_seconds: Optional[float] = None,
) -> QuestionResult:
    question_text = question.prompt
    thread_id = f"crag_{index}_{int(time.time() * 1000)}"
    events: List[str] = []
    span_cm = (
        invoke_agent_span(
            tracer,
            "crag.question",
            agent_name=f"{APP_NAME}.question",
            payload=question_text,
            extra_attributes={"crag.question_index": index, "crag.thread_id": thread_id},
        )
        if tracer
        else nullcontext((None, 0))
    )
    with span_cm as (span, question_bytes):
        start = time.perf_counter()
        try:
            for event in app.stream(
                {"question": question_text, "original_question": question_text},
                config={"configurable": {"thread_id": thread_id}},
            ):
                check_timeout(start, timeout_seconds)
                summary = _summarize_event(event)
                events.append(summary)
                if not quiet:
                    logger.info("  %s", summary)
                if span:
                    span.add_event("crag.graph_event", {"crag.summary": summary})
        except GraphRecursionError as exc:
            duration = time.perf_counter() - start
            if span:
                span.record_exception(exc)
                span.set_status(Status(StatusCode.ERROR, str(exc)))
                record_run_judgement(span, "unknown", "graph_recursion")
                set_run_outcome(span, success=False, reason="graph_recursion")
            return QuestionResult(
                index=index,
                question=question_text,
                rewritten_question=None,
                generation=None,
                retrieved_count=0,
                filtered_count=0,
                final_document_count=0,
                used_web_search=False,
                tavily_results=0,
                duration=duration,
                stream_events=events,
                node_events=[],
                doc_sources=[],
                judgement="unknown",
                judgement_reason="graph_recursion",
                gold_answer=question.answer,
                error=str(exc),
            )
        except TimeoutError as exc:
            duration = time.perf_counter() - start
            logger.error("Graph execution timed out: %s", exc)
            if span:
                span.record_exception(exc)
                span.set_status(Status(StatusCode.ERROR, str(exc)))
                record_run_judgement(span, "unknown", "timeout")
                set_run_outcome(span, success=False, reason="timeout")
            return QuestionResult(
                index=index,
                question=question_text,
                rewritten_question=None,
                generation=None,
                retrieved_count=0,
                filtered_count=0,
                final_document_count=0,
                used_web_search=False,
                tavily_results=0,
                duration=duration,
                stream_events=events,
                node_events=[],
                doc_sources=[],
                judgement="unknown",
                judgement_reason="timeout",
                gold_answer=question.answer,
                error="timeout",
            )
        except Exception as exc:  # pragma: no cover - best effort logging
            duration = time.perf_counter() - start
            logger.error("Graph execution failed: %s", exc)
            if span:
                span.record_exception(exc)
                span.set_status(Status(StatusCode.ERROR, str(exc)))
                record_run_judgement(span, "unknown", "graph_exception")
                set_run_outcome(span, success=False, reason="exception")
            return QuestionResult(
                index=index,
                question=question_text,
                rewritten_question=None,
                generation=None,
                retrieved_count=0,
                filtered_count=0,
                final_document_count=0,
                used_web_search=False,
                tavily_results=0,
                duration=duration,
                stream_events=events,
                node_events=[],
                doc_sources=[],
                judgement="unknown",
                judgement_reason="graph_exception",
                gold_answer=question.answer,
                error=str(exc),
            )
        snapshot = app.get_state({"configurable": {"thread_id": thread_id}})
        delete_state = getattr(app, "delete_state", None)
        if callable(delete_state):
            delete_state({"configurable": {"thread_id": thread_id}})
        duration = time.perf_counter() - start
        values = snapshot.values or {}
        documents: List[Document] = list(values.get("documents") or [])
        doc_sources = [
            getattr(doc, "metadata", {}).get("source", "unknown") for doc in documents
        ]
        node_events = list(values.get("events") or [])
        rewritten_question = values.get("rewritten_question")
        generation = values.get("generation")
        retrieved_count = int(values.get("retrieved_count") or len(documents))
        filtered_count = int(values.get("filtered_count") or len(documents))
        tavily_results = int(values.get("tavily_results") or 0)
        used_web_search = bool(values.get("web_search_used"))
        judgement, judgement_reason = evaluate_answer(
            mode=evaluator,
            pred=generation,
            gold=question.answer,
            question=question_text,
            llm=judge_llm,
        )
        if span:
            span.set_attribute("crag.retrieved_documents", retrieved_count)
            span.set_attribute("crag.filtered_documents", filtered_count)
            span.set_attribute("crag.final_documents", len(documents))
            span.set_attribute("crag.used_web_search", used_web_search)
            span.set_attribute("crag.tavily_results", tavily_results)
            span.set_attribute("crag.duration_seconds", duration)
            record_run_judgement(span, judgement, judgement_reason)
            if generation:
                record_invoke_agent_output(span, generation, question_bytes)
                span.set_status(Status(StatusCode.OK))
            set_run_outcome(span, success=generation is not None, reason="solved" if generation else "unsolved")
        return QuestionResult(
            index=index,
            question=question_text,
            rewritten_question=rewritten_question,
            generation=generation,
            retrieved_count=retrieved_count,
            filtered_count=filtered_count,
            final_document_count=len(documents),
            used_web_search=used_web_search,
            tavily_results=tavily_results,
            duration=duration,
            stream_events=events,
            node_events=node_events,
            doc_sources=doc_sources,
            judgement=judgement,
            judgement_reason=judgement_reason,
            gold_answer=question.answer,
        )


def write_run_artifacts(
    results: List[QuestionResult],
    *,
    run_id: str,
    args: argparse.Namespace,
    dataset_source: Optional[str],
    trace_log_path: Optional[Path],
    metrics_log_path: Optional[Path],
    status: str,
) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOG_DIR / f"run_{run_id}.log"
    metadata_path = LOG_DIR / f"run_{run_id}.metadata.json"
    with log_path.open("w", encoding="utf-8") as handle:
        for result in results:
            handle.write(
                f"[{result.index}] status={result.status} duration={result.duration:.2f}s "
                f"retrieved={result.retrieved_count} filtered={result.filtered_count} "
                f"final_docs={result.final_document_count} web_search={result.used_web_search}\n"
            )
            if result.rewritten_question:
                handle.write(f"  rewritten: {result.rewritten_question}\n")
            for event in result.stream_events:
                handle.write(f"  stream: {event}\n")
            for event in result.node_events:
                handle.write(f"  node: {event}\n")
            if result.generation:
                handle.write(f"  answer: {result.generation}\n")
            if result.judgement:
                handle.write(
                    f"  judgement: {result.judgement} ({result.judgement_reason})\n"
                )
            if result.error:
                handle.write(f"  error: {result.error}\n")
    metadata: Dict[str, object] = {
        "metadata_version": METADATA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "run_id": run_id,
        "app_name": APP_NAME,
        "python_version": sys.version,
        "cli_argv": sys.argv[1:],
        "status": status,
        "question_file": dataset_source,
        "questions": [result.to_metadata() for result in results],
        "config": {
            "seed_urls": args.seed_urls or DEFAULT_SEED_URLS,
            "chunk_size": args.chunk_size,
            "chunk_overlap": args.chunk_overlap,
            "retriever_k": args.retriever_k,
            "generator_model": args.generator_model,
            "grader_model": args.grader_model,
            "rewriter_model": args.rewriter_model,
            "embedding_model": args.embedding_model,
            "max_search_results": args.max_search_results,
            "web_search_enabled": not args.disable_web_search,
            "evaluator": args.evaluator,
            "judge_model": args.judge_model or args.generator_model,
            "judge_provider": args.judge_provider or args.provider,
            "provider": args.provider,
            "vertex_project": args.vertex_project or os.getenv("GOOGLE_CLOUD_PROJECT"),
            "vertex_location": args.vertex_location,
        },
    }
    if trace_log_path:
        metadata["trace_log"] = _relative_path(trace_log_path)
    if metrics_log_path:
        metadata["metrics_log"] = _relative_path(metrics_log_path)
    metadata_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    logger.info("Wrote %s and %s", log_path.name, metadata_path.name)


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="CRAG LangGraph benchmark translated from the notebook."
    )
    parser.add_argument(
        "--provider",
        choices=("openai", "google-vertex"),
        default=DEFAULT_PROVIDER,
        help="LLM provider for generator/grader/rewriter (default: openai).",
    )
    parser.add_argument(
        "--vertex-project",
        help="Google Cloud project for Vertex AI (defaults to GOOGLE_CLOUD_PROJECT).",
    )
    parser.add_argument(
        "--vertex-location",
        help="Vertex AI region (defaults to GOOGLE_CLOUD_REGION or us-central1).",
    )
    parser.add_argument(
        "--question",
        default=DEFAULT_OBJECTIVE,
        help="Single question to run if --questions-file is not provided.",
    )
    parser.add_argument(
        "--questions-file",
        help="Optional .txt or .csv file containing questions (one per line or question column).",
    )
    parser.add_argument(
        "--start-index",
        type=int,
        default=0,
        help="0-based index into the dataset when using --questions-file.",
    )
    parser.add_argument(
        "--num-questions",
        type=int,
        default=1,
        help="Number of questions to process from the dataset.",
    )
    parser.add_argument(
        "--seed-url",
        action="append",
        dest="seed_urls",
        help="Source URL to crawl for retrieval documents (can be provided multiple times).",
    )
    parser.add_argument(
        "--chunk-size",
        type=int,
        default=DEFAULT_CHUNK_SIZE,
        help="Chunk size for RecursiveCharacterTextSplitter.",
    )
    parser.add_argument(
        "--chunk-overlap",
        type=int,
        default=DEFAULT_CHUNK_OVERLAP,
        help="Chunk overlap for RecursiveCharacterTextSplitter.",
    )
    parser.add_argument(
        "--retriever-k",
        type=int,
        default=DEFAULT_RETRIEVAL_K,
        help="Number of documents to retrieve per query.",
    )
    parser.add_argument(
        "--generator-model",
        default=DEFAULT_GENERATOR_MODEL,
        help="OpenAI chat model used for final answer generation.",
    )
    parser.add_argument(
        "--grader-model",
        default=DEFAULT_GRADER_MODEL,
        help="OpenAI chat model used by the retrieval grader.",
    )
    parser.add_argument(
        "--rewriter-model",
        default=DEFAULT_REWRITER_MODEL,
        help="OpenAI chat model used by the question rewriter.",
    )
    parser.add_argument(
        "--embedding-model",
        default=DEFAULT_EMBEDDING_MODEL,
        help="OpenAI embedding model used for vector store creation.",
    )
    parser.add_argument(
        "--max-search-results",
        type=int,
        default=DEFAULT_WEB_RESULTS,
        help="Maximum number of Tavily results per search.",
    )
    parser.add_argument(
        "--disable-web-search",
        action="store_true",
        help="Skip Tavily web search even when documents were graded as irrelevant.",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress per-node streaming summaries.",
    )
    parser.add_argument(
        "--metrics-interval",
        type=float,
        default=DEFAULT_METRICS_INTERVAL,
        help="Seconds between psutil samples for system metrics.",
    )
    parser.add_argument(
        "--evaluator",
        choices=("f1", "llm"),
        default="f1",
        help="Correctness evaluator to use for run.judgement (default: f1).",
    )
    parser.add_argument(
        "--judge-model",
        default=None,
        help="Model for LLM-as-a-judge when --evaluator llm is selected (defaults to --generator-model).",
    )
    parser.add_argument(
        "--judge-provider",
        choices=("openai", "google-vertex"),
        default=None,
        help="Provider for the LLM judge (default: same as --provider).",
    )
    parser.add_argument(
        "--run-timeout-seconds",
        type=float,
        default=None,
        help="Optional wall-clock timeout per question; exits early with failure if exceeded.",
    )
    args = parser.parse_args(argv)
    if args.num_questions <= 0:
        parser.error("--num-questions must be >= 1.")
    if args.start_index < 0:
        parser.error("--start-index must be non-negative.")
    if args.retriever_k <= 0:
        parser.error("--retriever-k must be >= 1.")
    if args.chunk_size <= 0:
        parser.error("--chunk-size must be positive.")
    if args.chunk_overlap < 0:
        parser.error("--chunk-overlap must be >= 0.")
    if args.max_search_results <= 0:
        parser.error("--max-search-results must be positive.")
    if args.provider == "google-vertex" and not (
        args.vertex_project or os.getenv("GOOGLE_CLOUD_PROJECT")
    ):
        parser.error(
            "provider=google-vertex requires --vertex-project or GOOGLE_CLOUD_PROJECT."
        )
    return args


def _validate_env(args: argparse.Namespace) -> None:
    if (
        (args.provider == "openai")
        or (args.evaluator == "llm" and (args.judge_provider or args.provider) == "openai")
    ) and not os.getenv("OPENAI_API_KEY"):
        raise EnvironmentError("OPENAI_API_KEY environment variable is required for OpenAI provider or judge.")
    if args.provider == "google-vertex" and not (
        args.vertex_project or os.getenv("GOOGLE_CLOUD_PROJECT")
    ):
        raise EnvironmentError("GOOGLE_CLOUD_PROJECT or --vertex-project is required for provider=google-vertex.")
    if not args.disable_web_search and not os.getenv("TAVILY_API_KEY"):
        raise EnvironmentError(
            "TAVILY_API_KEY must be set when web search is enabled. "
            "Use --disable-web-search to skip Tavily."
        )


def main(argv: Optional[List[str]] = None) -> None:
    args = parse_args(argv)
    _validate_env(args)

    if args.questions_file:
        dataset_path = Path(args.questions_file)
        dataset = load_questions_file(dataset_path)
        if not dataset:
            raise SystemExit(f"No questions found in {dataset_path}")
        if args.start_index >= len(dataset):
            raise SystemExit("--start-index exceeds dataset size.")
        end = min(len(dataset), args.start_index + args.num_questions)
        slice_questions = dataset[args.start_index:end]
        question_records = [
            (args.start_index + idx, question)
            for idx, question in enumerate(slice_questions)
        ]
        dataset_source = str(dataset_path)
    else:
        question_records = [(0, QuestionRecord(prompt=args.question))]
        dataset_source = None
    seed_urls = args.seed_urls or DEFAULT_SEED_URLS

    run_id = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    tracer: Optional[trace.Tracer] = None
    trace_log_path: Optional[Path] = None
    provider: Optional[TracerProvider] = None
    metrics_recorder: Optional[PsutilMetricsRecorder] = None
    metrics_log_path: Optional[Path] = None
    try:
        tracer, trace_log_path, provider = setup_jsonl_tracing(
            app_name=APP_NAME,
            service_name=TRACE_SERVICE_NAME,
            service_version=TRACE_SERVICE_VERSION,
            log_dir=LOG_DIR,
            run_id=run_id,
            environment=DEFAULT_ENVIRONMENT,
        )
        logger.info("OpenTelemetry trace log: %s", trace_log_path)
    except Exception as exc:  # pragma: no cover - optional tracing
        logger.warning("Unable to initialize OpenTelemetry tracing: %s", exc)
        tracer = None
        trace_log_path = None
        provider = None

    try:
        metrics_recorder = PsutilMetricsRecorder(
            service_name=TRACE_SERVICE_NAME,
            service_version=TRACE_SERVICE_VERSION,
            run_id=run_id,
            output_dir=METRICS_DIR,
            environment=DEFAULT_ENVIRONMENT,
            scope=f"{APP_NAME}.system-metrics",
            interval_seconds=max(1.0, args.metrics_interval),
            logger=logger,
        )
        metrics_recorder.start()
        metrics_log_path = metrics_recorder.output_path
        logger.info("System metrics log: %s", metrics_log_path)
    except Exception as exc:  # pragma: no cover - optional metrics
        logger.warning("Unable to initialize system metrics recorder: %s", exc)
        metrics_recorder = None
        metrics_log_path = None

    judge_llm = None
    if args.evaluator == "llm":
        model_for_judge = args.judge_model or args.generator_model
        judge_provider = args.judge_provider or args.provider
        try:
            judge_llm = _make_chat_model(
                judge_provider,
                model_for_judge,
                0,
                vertex_project=args.vertex_project or os.getenv("GOOGLE_CLOUD_PROJECT"),
                vertex_location=args.vertex_location,
            )
        except Exception as exc:  # pragma: no cover - optional judge
            logger.warning("LLM judge unavailable, falling back to unknown judgements: %s", exc)
            judge_llm = None

    try:
        vectorstore = build_retriever(
            seed_urls,
            chunk_size=args.chunk_size,
            chunk_overlap=args.chunk_overlap,
            embedding_model=args.embedding_model,
            provider=args.provider,
            vertex_project=args.vertex_project or os.getenv("GOOGLE_CLOUD_PROJECT"),
            vertex_location=args.vertex_location,
            tracer=tracer,
        )
        retriever = vectorstore.as_retriever(search_kwargs={"k": args.retriever_k})
        retrieval_grader = build_retrieval_grader(
            args.grader_model,
            args.provider,
            args.vertex_project or os.getenv("GOOGLE_CLOUD_PROJECT"),
            args.vertex_location,
        )
        question_rewriter = build_question_rewriter(
            args.rewriter_model,
            args.provider,
            args.vertex_project or os.getenv("GOOGLE_CLOUD_PROJECT"),
            args.vertex_location,
        )
        rag_chain = build_rag_chain(
            args.generator_model,
            args.provider,
            args.vertex_project or os.getenv("GOOGLE_CLOUD_PROJECT"),
            args.vertex_location,
        )
        web_search_tool = (
            TavilySearchResults(k=args.max_search_results)
            if not args.disable_web_search
            else None
        )
        app = build_graph(
            retriever=retriever,
            retrieval_grader=retrieval_grader,
            rag_chain=rag_chain,
            question_rewriter=question_rewriter,
            web_search_tool=web_search_tool,
            tracer=tracer,
            generator_model=args.generator_model,
            grader_model=args.grader_model,
            rewriter_model=args.rewriter_model,
            enable_web_search=not args.disable_web_search,
            max_search_results=args.max_search_results,
            provider=args.provider,
        )
    except Exception:
        if metrics_recorder:
            metrics_recorder.stop()
        if provider:
            provider.shutdown()
        raise

    results: List[QuestionResult] = []
    run_status = "unknown"
    run_span_cm = (
        invoke_agent_span(
            tracer,
            "crag.run",
            agent_name=f"{APP_NAME}.run",
            payload={"questions": len(question_records), "dataset": dataset_source},
            extra_attributes={
                "crag.total_questions": len(question_records),
                "crag.dataset": dataset_source or "inline",
            },
        )
        if tracer
        else nullcontext((None, 0))
    )
    with run_span_cm as (run_span, run_input_bytes):
        try:
            for index, question in question_records:
                logger.info("Running question %s: %s", index, question.prompt)
                result = run_question(
                    app,
                    question,
                    index,
                    tracer,
                    quiet=args.quiet,
                    evaluator=args.evaluator,
                    judge_llm=judge_llm,
                    timeout_seconds=args.run_timeout_seconds,
                )
                results.append(result)
                if run_span:
                    run_span.add_event(
                        "crag.question_complete",
                        {
                            "crag.question_index": index,
                            "crag.status": result.status,
                            "crag.duration_seconds": result.duration,
                        },
                    )
            run_status = "ok" if all(result.error is None for result in results) else "error"
            if run_span:
                run_span.set_attribute("crag.status", run_status)
                run_span.set_attribute("crag.completed_questions", len(results))
                record_invoke_agent_output(run_span, run_status, run_input_bytes)
                if run_status == "ok":
                    run_span.set_status(Status(StatusCode.OK))
                else:
                    run_span.set_status(Status(StatusCode.ERROR, run_status))
        finally:
            if metrics_recorder:
                metrics_recorder.stop()
            if provider:
                provider.shutdown()

    write_run_artifacts(
        results,
        run_id=run_id,
        args=args,
        dataset_source=dataset_source,
        trace_log_path=trace_log_path,
        metrics_log_path=metrics_log_path,
        status=run_status,
    )

if __name__ == "__main__":  # pragma: no cover
    main()
