"""Chunking + lightweight retrieval for file-attached chat questions.

Single-document Q&A does not need a vector database. Extracted file text is
split into word-overlapping chunks once per upload; each question then pulls
only the top-k most relevant chunks (TF-IDF cosine similarity) so the LLM
prompt stays small and fast.

Fallback: if scikit-learn is unavailable, a keyword-overlap scorer is used
so retrieval never crashes the chat.
"""

import re

DEFAULT_CHUNK_SIZE = 500  # words
DEFAULT_OVERLAP = 50      # words


def chunk_text(text, chunk_size=DEFAULT_CHUNK_SIZE, overlap=DEFAULT_OVERLAP):
    """Split text into ~chunk_size-word chunks with `overlap` words of
    overlap so context isn't cut mid-thought.

    Args:
        text: Extracted file text.
        chunk_size: Words per chunk.
        overlap: Words shared between neighbouring chunks.

    Returns:
        list[str] of chunks (empty when the input is empty).
    """
    words = str(text or "").split()
    if not words:
        return []
    chunks = []
    i = 0
    while i < len(words):
        chunks.append(" ".join(words[i:i + chunk_size]))
        i += chunk_size - overlap
    return chunks


def _tfidf_retrieve(question, chunks, top_k):
    """Hybrid retrieval: TF-IDF cosine similarity + a token-overlap bonus.

    Pure TF-IDF with short questions can rank a doc-header chunk above the
    chunk that actually contains the answer (e.g. "how many patients were
    referred for diabetes follow-up" vs a screening section). Adding a
    modest overlap bonus for shared significant tokens fixes that without
    any extra dependency.
    """
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.metrics.pairwise import cosine_similarity

    vectorizer = TfidfVectorizer().fit(chunks + [question])
    chunk_vectors = vectorizer.transform(chunks)
    question_vector = vectorizer.transform([question])
    scores = cosine_similarity(question_vector, chunk_vectors)[0].copy()

    q_tokens = set(re.findall(r"[a-z0-9']+", str(question or "").lower()))
    significant = {tok for tok in q_tokens if len(tok) > 3}
    if significant:
        for i, chunk in enumerate(chunks):
            c_tokens = set(re.findall(r"[a-z0-9']+", str(chunk or "").lower()))
            shared = significant & c_tokens
            overlap_ratio = len(shared) / len(significant)
            scores[i] += 0.5 * overlap_ratio

    top_indices = scores.argsort()[-top_k:][::-1]
    return [chunks[i] for i in sorted(top_indices)]


def _keyword_retrieve(question, chunks, top_k):
    """Dependency-free fallback: score chunks by shared significant tokens."""
    q_tokens = set(re.findall(r"[a-z0-9']+", str(question or "").lower()))
    if not q_tokens:
        return chunks[:top_k]
    scored = []
    for chunk in chunks:
        c_tokens = set(re.findall(r"[a-z0-9']+", str(chunk or "").lower()))
        shared = q_tokens & c_tokens
        score = sum(len(tok) for tok in shared if len(tok) > 3)
        scored.append((score, chunk))
    scored.sort(key=lambda item: item[0], reverse=True)
    return [chunk for _score, chunk in scored[:top_k]]


def retrieve_relevant_chunks(question, chunks, top_k=3):
    """Return the top_k chunks most relevant to `question`.

    Short documents (<= top_k chunks) are returned in full -- no retrieval
    needed. Raises nothing: any retrieval failure falls back to keyword
    overlap scoring.
    """
    if not chunks:
        return []
    if len(chunks) <= top_k:
        return list(chunks)
    try:
        return _tfidf_retrieve(question, chunks, top_k)
    except Exception as exc:
        print(f"[doc_chunker] TF-IDF retrieval failed ({exc}), using keyword overlap")
        return _keyword_retrieve(question, chunks, top_k)