"""
rag_core - retrieval, chunking, and grounded answering used by the production API.

Modules:

    loaders.py         Chapter 5  - text extraction, PDF routing and OCR
    cleaning.py        Chapter 5  - cleaning extracted text
    chunking.py        Chapter 6  - seven chunking strategies
    embeddings.py      Chapter 7  - embedding models behind one interface
    vectorstore.py     Chapter 8  - exact vector search with metadata filters
    bm25.py            Chapter 8  - keyword search (BM25) written from scratch
    hybrid.py          Chapter 8  - hybrid retrieval, min-max fusion and RRF
    rerank.py          Chapter 8  - cross-encoder reranking
    llm.py, prompts.py Chapter 9  - talking to an LLM, grounded prompts
    generate.py        Chapter 9  - context building, citations, abstaining
    evaluation.py      Chapter 10 - retrieval and answer metrics
    security.py        Chapter 11 - PII redaction, access control, prompt injection
    cache.py           Chapter 12 - semantic cache
    observability.py   Chapter 12 - tracing, latency and cost
    pipeline.py        Everything wired together
"""

__version__ = "1.0.0"
