from dotenv import load_dotenv
import os

load_dotenv()
from typing import Any, Dict, List

from langchain import hub
from langchain.chains.combine_documents import create_stuff_documents_chain
from langchain.chains.history_aware_retriever import create_history_aware_retriever
from langchain.chains.retrieval import create_retrieval_chain
from langchain_chroma import Chroma
from langchain_core.output_parsers import StrOutputParser
from langchain_core.runnables import RunnablePassthrough
from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from langchain_pinecone import PineconeEmbeddings
from langchain_pinecone import PineconeVectorStore


# embeddings = OpenAIEmbeddings(model="text-embedding-3-small")
# chroma = Chroma(persist_directory="chroma_db", embedding_function=embeddings)

embeddings = PineconeEmbeddings(model="llama-text-embed-v2")
docsearch = PineconeVectorStore(
    index_name=os.environ["INDEX_NAME"], embedding=embeddings
)


def run_llm(query: str, chat_history: List[Dict[str, Any]] = []):
    chat = ChatOpenAI(model="gpt-4o-mini", verbose=True, temperature=0)

    rephrase_prompt = hub.pull("langchain-ai/chat-langchain-rephrase")

    retrieval_qa_chat_prompt = hub.pull("langchain-ai/retrieval-qa-chat")
    stuff_documents_chain = create_stuff_documents_chain(chat, retrieval_qa_chat_prompt)

    history_aware_retriever = create_history_aware_retriever(
        llm=chat, retriever=docsearch.as_retriever(), prompt=rephrase_prompt
    )
    qa = create_retrieval_chain(
        retriever=history_aware_retriever, combine_docs_chain=stuff_documents_chain
    )

    print("🔄 Retrieving relevant documents...")
    result = qa.invoke(input={"input": query, "chat_history": chat_history})

    # Print the retrieved documents
    print(f"\n📚 RETRIEVED DOCUMENTS ({len(result.get('context', []))} found):")
    print("-" * 60)

    for i, doc in enumerate(result.get("context", []), 1):
        print(f"\n📄 Document {i}:")
        print(f"   🔗 Source: {doc.metadata.get('source', 'Unknown source')}")
        print(f"   📝 Title: {doc.metadata.get('title', 'No title')}")
        print(f"   🔍 Content preview: {doc.page_content[:200]}...")
        print(f"   📏 Content length: {len(doc.page_content)} characters")
        print(f"   📏 ID: {doc.id}")

        # Show more metadata if available
        other_metadata = {
            k: v for k, v in doc.metadata.items() if k not in ["source", "title"]
        }
        if other_metadata:
            print(f"   ℹ️  Other metadata: {other_metadata}")

    print(f"\n🤖 GENERATING ANSWER...")
    print("-" * 60)

    return result


"""
Quick test script - minimal example
"""


def quick_test():
    # Test query
    query = "What is Chroma DB that Langchain uses?"

    print(f"🤔 Question: {query}")
    print("🔄 Processing...")

    try:
        # Run the RAG system
        result = run_llm(query=query, chat_history=[])

        # Print results
        print("\n✅ Answer:")
        print(result["answer"])

        print(f"\n📚 Sources ({len(result['context'])} docs):")
        for i, doc in enumerate(result["context"], 1):
            source = doc.metadata.get("source", "Unknown")
            print(f"{i}. {source}")

    except Exception as e:
        print(f"❌ Error: {e}")


if __name__ == "__main__":
    quick_test()


def format_docs(docs):
    return "\n\n".join(doc.page_content for doc in docs)


# def run_llm2(query: str, chat_history: List[Dict[str, Any]] = []):
#     embeddings = OpenAIEmbeddings(model="text-embedding-3-small")
#     docsearch = Chroma(persist_directory="chroma_db", embedding_function=embeddings)
#     chat = ChatOpenAI(model="gpt-4o-mini", verbose=True, temperature=0)

#     rephrase_prompt = hub.pull("langchain-ai/chat-langchain-rephrase")

#     retrieval_qa_chat_prompt = hub.pull("langchain-ai/retrieval-qa-chat")

#     rag_chain = (
#         {
#             "context": docsearch.as_retriever() | format_docs,
#             "input": RunnablePassthrough(),
#         }
#         | retrieval_qa_chat_prompt
#         | chat
#         | StrOutputParser()
#     )

#     retrieve_docs_chain = (lambda x: x["input"]) | docsearch.as_retriever()

#     chain = RunnablePassthrough.assign(context=retrieve_docs_chain).assign(
#         answer=rag_chain
#     )

#     result = chain.invoke({"input": query, "chat_history": chat_history})
#     return result
