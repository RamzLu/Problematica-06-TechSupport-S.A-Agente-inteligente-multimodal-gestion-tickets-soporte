import mimetypes
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from agent.repo import CARPETA_COMPARTIDA, get_repo  # noqa: E402

EXTENSIONES = {".png", ".jpg", ".jpeg", ".webp", ".txt", ".log"}


def main() -> None:
    repo = get_repo()
    subidos = 0
    for ruta in sorted((RAIZ / "archivos").iterdir()):
        if not ruta.is_file() or ruta.suffix.lower() not in EXTENSIONES:
            continue
        mime = "text/plain" if ruta.suffix.lower() in {".txt", ".log"} else (
            mimetypes.guess_type(ruta.name)[0] or "application/octet-stream"
        )
        repo.archivo_guardar(CARPETA_COMPARTIDA, ruta.name, ruta.read_bytes(), mime)
        print(f"Subido: {CARPETA_COMPARTIDA}/{ruta.name}")
        subidos += 1
    print(f"Listo: {subidos} archivo(s).")


if __name__ == "__main__":
    main()
