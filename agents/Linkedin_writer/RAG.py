import os
from pathlib import Path

from langchain_chroma import Chroma
from langchain_core.documents import Document
from langchain_huggingface import HuggingFaceEmbeddings

PERSIST_DIR = "chroma_store/linkedin_voice"
POSTS_DIR = Path("data/past_linkedin_posts")  # one .txt file per past post

embeddings = HuggingFaceEmbeddings(model_name="sentence-transformers/all-MiniLM-L6-v2")

def _load_or_build_vectorstore() -> Chroma:
    if os.path.exists(PERSIST_DIR) and os.listdir(PERSIST_DIR):
        return Chroma(persist_directory=PERSIST_DIR, embedding_function=embeddings)

    docs = [
        Document(page_content=path.read_text(encoding="utf-8"), metadata={"source": path.name})
        for path in POSTS_DIR.glob("*.txt")
    ]
    if not docs:
        raise ValueError(
            f"No past posts found in {POSTS_DIR}. Add some .txt files "
            "(one past LinkedIn post per file) before running this node."
        )
    return Chroma.from_documents(docs, embedding=embeddings, persist_directory=PERSIST_DIR)



