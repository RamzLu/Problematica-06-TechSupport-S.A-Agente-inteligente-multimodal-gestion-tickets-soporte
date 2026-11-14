import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.repo import get_repo  # noqa: E402
from agent.tools import tools as T  # noqa: E402


def main() -> None:
    texto = " ".join(sys.argv[1:]).strip() or (
        "tengo un problema, me sale una pantalla azul que dice Se produjo un error en el software del sistema"
    )
    print(f"Texto: {texto}\n")
    tickets = get_repo().tickets_todos()
    print(f"Tickets en Supabase: {len(tickets)}")

    try:
        from langchain_ollama import OllamaEmbeddings

        dim = len(OllamaEmbeddings(model="nomic-embed-text").embed_query("prueba"))
        print(f"Ollama (nomic-embed-text): OK, vector de {dim} dimensiones")
    except Exception as exc:
        print(f"Ollama (nomic-embed-text): FALLA -> {exc}")
        print("Solucion: abrir Ollama y correr 'ollama pull nomic-embed-text'.")
        return

    try:
        vs = T._obtener_vector_store()
        print(f"Tickets indexados: {len(T._ids_indexados)}")
        res = vs.similarity_search_with_relevance_scores(texto, k=5)
    except Exception as exc:
        print(f"Error al indexar o buscar: {exc}")
        return

    print(f"\nUmbral de duplicado: {T.UMBRAL_DUPLICADO}")
    for doc, score in res:
        marca = "DUPLICADO" if score >= T.UMBRAL_DUPLICADO else "debajo del umbral"
        print(f"  ticket {doc.metadata['ticket_id']}: similitud {score:.3f} -> {marca}")
    if not res:
        print("  (sin resultados)")


if __name__ == "__main__":
    main()
