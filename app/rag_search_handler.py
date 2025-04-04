import os
from typing import Any
from dotenv import load_dotenv, find_dotenv
from azure.core.credentials import AzureKeyCredential
from azure.identity import DefaultAzureCredential
from azure.search.documents.aio import SearchClient
from azure.search.documents.models import VectorizableTextQuery

load_dotenv(find_dotenv())

search_key = os.environ.get("AZURE_SEARCH_API_KEY")
search_endpoint = os.environ.get("AZURE_SEARCH_ENDPOINT")
identifier_field = os.environ.get("AZURE_OPENAI_IDENTIFIERFIELD") or "chunk_id"
content_field = os.environ.get("AZURE_OPENAI_CONTENTFIELD") or "chunk"
embedding_field = os.environ.get("AZURE_OPENAI_EMBEDDINGFIELD") or "text_vector"
title_field = os.environ.get("AZURE_OPENAI_TITLEFIELD") or "title"
user_vector_query = (os.environ.get("AZURE_OPENAI_USERVECTORQUERY") == "true") or True


async def _search_tool(search_client: SearchClient, args: Any) -> Any:
    print(f"Searching for '{args['query']}' in the knowledge base.")
    # Focus on retrieving minimal yet accurate information.
    vector_queries = []
    if user_vector_query:
        # Reduce the number of neighbors to limit the impact of less relevant results.
        vector_queries.append(
            VectorizableTextQuery(
                text=args["query"], k_nearest_neighbors=10, fields=embedding_field
            )
        )

    semantic_configuration = args.get("search_config").get(
        "SEARCH_SEMANTIC_CONFIGURATION"
    )
    search_results = await search_client.search(
        search_text=args["query"],
        query_type="semantic",
        semantic_configuration_name=semantic_configuration,
        top=3,  # Retrieve only the top result.
        vector_queries=vector_queries,
        select=", ".join([identifier_field, content_field]),
    )
    result = ""
    async for r in search_results:
        result += f"[{r[identifier_field]}]: {r[content_field]}"
    return result


_cached_search_client = None


async def get_cached_search_client(args: Any) -> SearchClient:
    global _cached_search_client
    if _cached_search_client is None:
        credentials = (
            AzureKeyCredential(search_key) if search_key else DefaultAzureCredential()
        )
        if not isinstance(credentials, AzureKeyCredential):
            credentials.get_token("https://search.azure.com/.default")
        search_index = args.get("search_config").get("SEARCH_INDEX")
        _cached_search_client = SearchClient(search_endpoint, search_index, credentials)
    return _cached_search_client


async def get_search_response(args: Any) -> str:
    try:
        if not args.get("search_config"):
            raise ValueError("Search configuration is missing for this request.")

        search_client = await get_cached_search_client(args)
        response = await _search_tool(search_client, args)
        return response
    except Exception as e:
        raise Exception(f"Error while getting search response: {e}") from e
