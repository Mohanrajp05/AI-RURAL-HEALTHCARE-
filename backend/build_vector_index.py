"""
Medical RAG Vector Index Builder
================================

Loads all disease knowledge-base `.txt` files from `backend/knowledge_base/`,
chunks them, embeds them with a free local sentence-transformer model, and
stores the result in a FAISS index under `backend/faiss_disease_index/`.

Pipeline:
    DirectoryLoader (txt) -> RecursiveCharacterTextSplitter (500/100)
    -> HuggingFaceEmbeddings (all-MiniLM-L6-v2, local, no API key)
    -> FAISS vector store -> saved to disk

Run (from backend/):
    venv\\Scripts\\python.exe build_vector_index.py
"""

import os
from pathlib import Path

from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_community.document_loaders import DirectoryLoader, TextLoader
from langchain_community.vectorstores import FAISS
from langchain_huggingface import HuggingFaceEmbeddings

BASE_DIR = Path(__file__).resolve().parent

KNOWLEDGE_BASE_DIR = BASE_DIR / "knowledge_base"
INDEX_OUTPUT_DIR = BASE_DIR / "faiss_disease_index"

EMBEDDING_MODEL = "sentence-transformers/all-MiniLM-L6-v2"

CHUNK_SIZE = 500
CHUNK_OVERLAP = 100


def load_documents() -> list:
    """Load every .txt knowledge-base file as a LangChain Document."""
    loader = DirectoryLoader(
        str(KNOWLEDGE_BASE_DIR),
        glob="**/*.txt",
        loader_cls=TextLoader,
        loader_kwargs={"encoding": "utf-8"},
        show_progress=False,
    )
    return loader.load()


def main() -> None:
    if not KNOWLEDGE_BASE_DIR.is_dir():
        raise SystemExit(
            f"knowledge_base/ folder not found at {KNOWLEDGE_BASE_DIR}. "
            "Run scrape_medical_kb.py first."
        )

    print(f"Loading documents from: {KNOWLEDGE_BASE_DIR}")
    documents = load_documents()

    if not documents:
        raise SystemExit("No .txt files found in knowledge_base/. Nothing to index.")

    print(f"Loaded {len(documents)} document(s).")

    # 1. Split each document into 500-char chunks with 100-char overlap.
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=CHUNK_SIZE,
        chunk_overlap=CHUNK_OVERLAP,
        separators=["\n\n", "\n", " ", ""],
    )
    chunks = splitter.split_documents(documents)
    print(
        f"Created {len(chunks)} chunks from {len(documents)} documents "
        f"(chunk_size={CHUNK_SIZE}, overlap={CHUNK_OVERLAP})."
    )

    # 2. Embed locally with HuggingFace sentence-transformers (no API key).
    print(f"Loading embedding model: {EMBEDDING_MODEL}")
    embeddings = HuggingFaceEmbeddings(
        model_name=EMBEDDING_MODEL,
        model_kwargs={"device": "cpu"},
        encode_kwargs={"normalize_embeddings": True},
    )

    # 3. Build and persist the FAISS index.
    print("Building FAISS index...")
    vector_store = FAISS.from_documents(chunks, embeddings)

    INDEX_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    vector_store.save_local(str(INDEX_OUTPUT_DIR))
    print(f"Saved FAISS index to: {INDEX_OUTPUT_DIR}")
    print("FAISS index built and saved successfully")


if __name__ == "__main__":
    main()
