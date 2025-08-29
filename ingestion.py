import asyncio
import os
import ssl
import time
from typing import Any, Dict, List

import certifi
from dotenv import load_dotenv
from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain_chroma import Chroma
from langchain_core.documents import Document
from langchain_openai import OpenAIEmbeddings
from langchain_pinecone import PineconeVectorStore
from langchain_tavily import TavilyCrawl
from langchain_pinecone import PineconeEmbeddings

from logger import Colors, log_error, log_header, log_info, log_success, log_warning


load_dotenv()

# Configure SSL context to use certifi certificates
ssl_context = ssl.create_default_context(cafile=certifi.where())
os.environ["SSL_CERT_FILE"] = certifi.where()
os.environ["REQUESTS_CA_BUNDLE"] = certifi.where()


# Option 1: OpenAI Embeddings with Chroma (Higher rate limits)
# embeddings = OpenAIEmbeddings(
#     model="text-embedding-3-small",
#     show_progress_bar=True,
#     chunk_size=1000,
#     max_retries=3,
# )
# vectorstore = Chroma(persist_directory="chroma_db", embedding_function=embeddings)

# Option 2: Pinecone Embeddings (Lower rate limits - requires careful rate limiting)
embeddings = PineconeEmbeddings(model="llama-text-embed-v2")
vectorstore = PineconeVectorStore(
    index_name=os.environ["INDEX_NAME"], embedding=embeddings
)


# TavilyCrawl handles both mapping and extraction in one step
tavily_crawl = TavilyCrawl()


async def index_documents_async(documents: List[Document], batch_size: int = 3):
    """Process documents in batches asynchronously."""
    log_header("VECTOR STORAGE PHASE")
    log_info(
        f"📚 VectorStore Indexing: Preparing to add {len(documents)} documents to Pinecone vector store",
        Colors.DARKCYAN,
    )
    log_info(
        "🔧 Using conservative rate limiting for Pinecone (3 docs/batch, 3s delays)",
        Colors.YELLOW,
    )

    # Create batches
    batches = [
        documents[i : i + batch_size] for i in range(0, len(documents), batch_size)
    ]

    log_info(
        f"📦 VectorStore Indexing: Split into {len(batches)} batches of {batch_size} documents each"
    )
    log_info(
        f"⏱️  Estimated processing time: ~{len(batches) * 3 + (len(batches) * 2)}s with delays",
        Colors.YELLOW,
    )

    # Process batches with rate limiting and retry logic
    async def add_batch_with_retry(
        batch: List[Document], batch_num: int, max_retries: int = 3
    ):
        for attempt in range(max_retries + 1):
            try:
                # Add longer delay between batches for Pinecone rate limits
                if batch_num > 1:
                    delay = 3  # 3 seconds between batches for Pinecone
                    log_info(
                        f"⏳ Waiting {delay}s before processing batch {batch_num}..."
                    )
                    await asyncio.sleep(delay)

                await vectorstore.aadd_documents(batch)
                log_success(
                    f"VectorStore Indexing: Successfully added batch {batch_num}/{len(batches)} ({len(batch)} documents)"
                )
                return True

            except Exception as e:
                error_msg = str(e)
                if (
                    "429" in error_msg
                    or "Too Many Requests" in error_msg
                    or "RESOURCE_EXHAUSTED" in error_msg
                ):
                    # Rate limit error - wait longer before retry (Pinecone needs more time)
                    wait_time = (2**attempt) * 20  # Exponential backoff: 20s, 40s, 80s
                    if attempt < max_retries:
                        log_warning(
                            f"⚠️  Rate limit hit for batch {batch_num}, attempt {attempt + 1}/{max_retries + 1}. "
                            f"Waiting {wait_time}s before retry..."
                        )
                        await asyncio.sleep(wait_time)
                        continue
                    else:
                        log_error(
                            f"❌ VectorStore Indexing: Rate limit exceeded for batch {batch_num} after {max_retries} retries"
                        )
                        return False
                else:
                    # Other error - log and fail
                    log_error(
                        f"❌ VectorStore Indexing: Failed to add batch {batch_num} - {e}"
                    )
                    return False
        return False

    # Process batches sequentially to respect rate limits (not concurrently)
    results = []
    for i, batch in enumerate(batches):
        result = await add_batch_with_retry(batch, i + 1)
        results.append(result)

    # Count successful batches
    successful = sum(1 for result in results if result is True)

    if successful == len(batches):
        log_success(
            f"VectorStore Indexing: All batches processed successfully! ({successful}/{len(batches)})"
        )
    else:
        log_warning(
            f"VectorStore Indexing: Processed {successful}/{len(batches)} batches successfully"
        )


async def main():
    """Main async function to orchestrate the entire process."""
    log_header("DOCUMENTATION INGESTION PIPELINE")

    log_info(
        "🗺️  TavilyCrawl: Starting to crawl the documentation site",
        Colors.PURPLE,
    )
    # Crawl the documentation site

    res = tavily_crawl.invoke(
        {
            "url": "https://python.langchain.com/",
            "max_depth": 2,
            "extract_depth": "advanced",
        }
    )
    all_docs = res["results"]

    # Debug: Check what TavilyCrawl returns
    log_info(f"🔍 Debug: TavilyCrawl returned {len(all_docs)} items")
    if all_docs and len(all_docs) > 0:
        log_info(f"🔍 Debug: First item type: {type(all_docs[0])}")
        log_info(
            f"🔍 Debug: First item keys: {list(all_docs[0].keys()) if isinstance(all_docs[0], dict) else 'Not a dict'}"
        )

    # Convert dictionaries to Document objects
    log_info("🔄 Converting crawled data to Document objects", Colors.YELLOW)
    documents = []
    for doc_data in all_docs:
        if isinstance(doc_data, dict):
            # Extract content and metadata from the dictionary
            content = doc_data.get("raw_content", doc_data.get("content", ""))
            url = doc_data.get("url", "")
            title = doc_data.get("title", "")

            # Create Document object
            doc = Document(
                page_content=content,
                metadata={
                    "url": url,
                    "title": title,
                    "source": url,  # LangChain commonly uses 'source' in metadata
                },
            )
            documents.append(doc)
        else:
            # If it's already a Document object, keep it as is
            documents.append(doc_data)

    log_success(f"✅ Converted {len(documents)} items to Document objects")

    # Split documents into chunks
    log_header("DOCUMENT CHUNKING PHASE")
    log_info(
        f"✂️  Text Splitter: Processing {len(documents)} documents with 4000 chunk size and 200 overlap",
        Colors.YELLOW,
    )
    text_splitter = RecursiveCharacterTextSplitter(chunk_size=4000, chunk_overlap=200)
    splitted_docs = text_splitter.split_documents(documents)
    # Debug: Show first chunk example
    log_info(
        f"📄 Sample chunk preview: {splitted_docs[0].page_content[:200]}..."
        if splitted_docs
        else "No chunks created"
    )
    log_success(
        f"Text Splitter: Created {len(splitted_docs)} chunks from {len(documents)} documents"
    )

    # Process documents asynchronously
    await index_documents_async(splitted_docs)

    log_header("PIPELINE COMPLETE")
    log_success("🎉 Documentation ingestion pipeline finished successfully!")
    log_info("📊 Summary:", Colors.BOLD)
    log_info(f"   • Documents extracted: {len(documents)}")
    log_info(f"   • Chunks created: {len(splitted_docs)}")


if __name__ == "__main__":
    asyncio.run(main())
