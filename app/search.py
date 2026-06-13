"""Búsqueda web real para el News watcher y el Destination Scanner.

Usa Tavily (https://tavily.com) — 1000 búsquedas/mes gratis, sin tarjeta.
Sin TAVILY_API_KEY, corre en modo mock (devuelve lista vacía) para que el
resto del pipeline funcione igual en dev sin romper nada.

Esto es lo que en producción correría como cron POR DESTINO (no por usuario),
cada 24h, alimentando un `destination_feed` compartido — ver README. El
endpoint /trips/{tid}/scan del MVP lo dispara manualmente por viaje para
poder probarlo de punta a punta.
"""
import os

import httpx

TAVILY_API_KEY = os.environ.get("TAVILY_API_KEY", "")
MODE = "real" if TAVILY_API_KEY else "mock"


def search(query: str, max_results: int = 3, topic: str = "general") -> list[dict]:
    """Devuelve [{title, content, url}, ...]. Lista vacía en modo mock o si falla."""
    if MODE == "mock":
        return []
    try:
        resp = httpx.post(
            "https://api.tavily.com/search",
            json={
                "api_key": TAVILY_API_KEY,
                "query": query,
                "topic": topic,  # "news" o "general"
                "search_depth": "basic",
                "max_results": max_results,
                "include_answer": False,
                "include_raw_content": False,
            },
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        return [
            {"title": r.get("title", ""), "content": r.get("content", ""), "url": r.get("url", "")}
            for r in data.get("results", [])
        ]
    except Exception:
        # Una búsqueda fallida nunca debe romper el pipeline del Companion.
        return []
