

import os
import re

_client = None
_client_init_failed = False


def get_client():
    """Lazily create the Tavily client. Returns None (never raises) when
    the API key is unset or the package/init fails -- callers treat that
    as "web search unavailable right now"."""
    global _client, _client_init_failed
    if _client is not None:
        return _client
    if _client_init_failed:
        return None
    api_key = os.environ.get("TAVILY_API_KEY", "").strip()
    if not api_key:
        return None
    try:
        from tavily import TavilyClient
        _client = TavilyClient(api_key=api_key)
        return _client
    except Exception as exc:
        print(f"[web_search] Tavily client init failed: {exc!r}", flush=True)
        _client_init_failed = True
        return None


def search_medical_web(query: str, max_results: int = 3):
    """Search the web for medical information not in the local KB.

    Returns a summarized answer string, or None when Tavily is
    unconfigured, returns nothing usable, or the request fails -- callers
    treat None exactly like any other failed tier and fall through to the
    next one.
    """
    client = get_client()
    if not client:
        return None
    try:
        # "medical health information" steers Tavily toward health sources
        # (WebMD, Mayo Clinic, WHO, NHS) rather than generic web results.
        medical_query = f"{query} medical health information"
        result = client.search(
            query=medical_query,
            search_depth="basic",
            max_results=max_results,
            include_answer=True,
        )
        answer = (result or {}).get("answer")
        if answer and len(answer) > 50:
            return (
                f"{answer}\n\n"
                f"(Source: web search — please verify with a doctor)"
            )
        return None
    except Exception as exc:
        print(f"[web_search] Tavily search failed: {exc!r}", flush=True)
        return None


# Safety net only -- guardrails.check_guardrails() already blocks
# off-topic messages in app.py before chatbot_pipeline.process_query() is
# ever called (see the "cricket match score" guardrail eval case), so this
# tier structurally can't be reached by an off-topic query in the first
# place. This exists in case a future caller invokes search_medical_web()
# from somewhere that DIDN'T already pass guardrails.
_MEDICAL_TERMS = re.compile(
    r"\b(disease|symptom|treatment|medicine|health|medical|doctor|"
    r"hospital|blood|heart|brain|cancer|diabetes|fever|pain|"
    r"infection|virus|bacteria|organ|body|nerve|muscle|bone|"
    r"surgery|therapy|diagnosis|syndrome|disorder|condition)\b",
    re.IGNORECASE,
)


def is_medical_query(text: str) -> bool:
    return bool(_MEDICAL_TERMS.search(str(text or "")))
