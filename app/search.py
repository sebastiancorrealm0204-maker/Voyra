"""Búsqueda web real para el News watcher y el Destination Scanner.

Usa Tavily (https://tavily.com) — 1000 búsquedas/mes gratis, sin tarjeta.
Sin TAVILY_API_KEY, corre en modo mock (devuelve lista vacía) para que el
resto del pipeline funcione igual en dev sin romper nada.

Esto es lo que en producción correría como cron POR DESTINO (no por usuario),
cada 24h, alimentando un `destination_feed` compartido — ver README. El
endpoint /trips/{tid}/scan del MVP lo dispara manualmente por viaje para
poder probarlo de punta a punta.
"""
import logging
import os

from . import net

log = logging.getLogger("companion.search")

TAVILY_API_KEY = os.environ.get("TAVILY_API_KEY", "")
MODE = "real" if TAVILY_API_KEY else "mock"

# Caché TTL por (query, topic, max_results): el Destination Scanner y el modo
# aeropuerto repiten las MISMAS queries para todos los viajeros de una ciudad
# en el mismo día. 30 min de frescura sobra para noticias/recomendaciones y
# ahorra cuota de Tavily + varios segundos por búsqueda.
_cache = net.TTLCache(ttl_s=float(os.environ.get("SEARCH_CACHE_TTL_S", "1800")), maxsize=512)


def search(query: str, max_results: int = 3, topic: str = "general", days: int | None = None) -> list[dict]:
    """Devuelve [{title, content, url}, ...]. Lista vacía en modo mock o si falla."""
    if MODE == "mock":
        return []
    ck = (query.strip().lower(), topic, int(max_results), days)
    cacheado = _cache.get(ck)
    if cacheado is not None:
        return [dict(r) for r in cacheado]
    try:
        cuerpo = {
            "api_key": TAVILY_API_KEY,
            "query": query,
            "topic": topic,  # "news" o "general"
            "search_depth": "basic",
            "max_results": max_results,
            "include_answer": False,
            "include_raw_content": False,
        }
        if days and topic == "news":
            cuerpo["days"] = int(days)  # solo noticias de los últimos N días
        resp = net.request("POST", "https://api.tavily.com/search", retries=1, json=cuerpo, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        out = [
            {"title": r.get("title", ""), "content": r.get("content", ""), "url": r.get("url", ""),
             "fecha": r.get("published_date") or ""}
            for r in data.get("results", [])
        ]
        _cache.set(ck, [dict(r) for r in out])
        return out
    except Exception as e:
        # Una búsqueda fallida nunca debe romper el pipeline del Companion.
        log.warning("tavily_error error=%s", type(e).__name__)
        return []
