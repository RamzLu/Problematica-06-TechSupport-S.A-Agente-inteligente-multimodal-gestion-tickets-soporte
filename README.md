# Problemática 06 — TechSupport S.A.
## Agente Inteligente Multimodal para la Gestión de Tickets de Soporte Técnico

Trabajo práctico de la materia **Seminario de Actividad Complementaria — Modelos y Aplicaciones de la Inteligencia Artificial**.

**TechSupport S.A.** necesita un asistente inteligente que reciba solicitudes de soporte en lenguaje natural y en varios formatos (texto, imágenes/capturas, `.txt` y `.log`), comprenda la incidencia, pida información cuando sea ambigua, la clasifique con reglas de negocio, proponga su prioridad, evite tickets duplicados (búsqueda semántica) y gestione todo su ciclo de vida respetando roles, permisos, transiciones de estado e historial de trazabilidad, **sin exponer datos personales (PII) al modelo de lenguaje**.

---

## 1. Arquitectura

| Capa | Tecnología |
|---|---|
| Agente | Google ADK + LiteLLM → Ollama (`qwen3.5:4b`) |
| Duplicados | ChromaDB + embeddings `nomic-embed-text` (Ollama) |
| API / Frontend | FastAPI + un único `frontend/index.html` |
| **Login** | **Supabase Auth** (email + contraseña, JWT) |
| **Datos** | **Supabase Postgres** (usuarios/perfiles, tickets, historial) |

### Login real con Supabase Auth
- El usuario ingresa su **código (`s001`, `u001`…) o correo** y su contraseña. El backend valida contra Supabase Auth y devuelve el JWT de Supabase y un token de renovación.
- **Cada petición se identifica únicamente por el JWT** (`Authorization: Bearer …`), que se verifica contra Supabase. Ya no hay tokens propios en memoria ni se acepta un `usuario_id` suelto como identidad.
- La sesión se **renueva sola** (el frontend usa el token de renovación cuando el JWT vence), y el cierre de sesión revoca el token.
- Las contraseñas **nunca** se guardan en este proyecto: las gestiona Supabase Auth.
- Si Supabase no responde, la API contesta **503** (no cierra la sesión por un corte de red).

### Datos en Supabase
- `public.profiles`: un perfil por usuario de Supabase Auth (código, nombre, rol `usuario`/`soporte`, área, cargo, `activo`).
- `public.tickets` y `public.ticket_historial`: tickets y trazabilidad. Cada cambio y su evento de historial se guardan **en una sola transacción** (funciones `crear_ticket_con_historial` y `aplicar_cambio_ticket`).
- `public.organizaciones`.
- Las tablas tienen **RLS activado sin políticas** y sin permisos para `anon`/`authenticated`: solo el backend (clave `service_role`) accede.

### Privacidad (Zero-PII hacia el LLM)
- El agente nunca recibe nombres ni correos: `preprocesar_contexto_seguro` autentica la sesión, la fija en un `ContextVar` y enmascara nombres/correos antes de llamar al modelo.
- Los permisos por rol y el catálogo de categorías, prioridades, estados, transiciones y reglas de clasificación están en `dataset/datos_iniciales.json` (sin datos personales).

---

## 2. Puesta en marcha

### 2.1 Base de datos (Supabase)
En el **SQL Editor** de tu proyecto, ejecutar en orden:
1. `supabase/01_esquema.sql` — tablas, trigger de perfiles, RLS y funciones. *(Si el proyecto ya se configuró con el asistente, este paso ya está hecho.)*
2. Crear los usuarios (ver 2.2).
3. `supabase/02_usuarios_demo.sql` — completa código, rol, área y cargo de los 8 usuarios de ejemplo.
4. `supabase/03_tickets_demo.sql` — carga los 5 tickets históricos (ejecutar una sola vez).
5. `supabase/04_storage.sql` — crea el bucket privado `ticket-archivos` (imágenes, `.txt` y `.log`, máx. 5 MB). Luego, una sola vez y con el `.env` completo, subir los archivos de ejemplo: `env\Scripts\python.exe scripts/subir_archivos_demo.py`.
6. `supabase/05_concurrencia.sql` — reemplaza `aplicar_cambio_ticket` por una versión que detecta ediciones simultáneas del mismo ticket. Es seguro ejecutarla varias veces. Si todavía no se ejecutó, el sistema sigue funcionando pero avisa en el log.

### 2.2 Crear usuarios (desde Supabase)
1. Panel de Supabase → **Authentication → Users → Add user → Create new user**.
2. Ingresar correo y contraseña y **tildar "Auto Confirm User"**.
3. Al crearse, un trigger genera su perfil automáticamente (`rol = usuario`, código `u001`, `u002`…).
4. Para cambiar rol, nombre, área o código, editar la fila en **Table Editor → profiles** (por ejemplo `rol = soporte`, `codigo = s004`).
5. Para dar de baja a alguien sin borrarlo: `activo = false` (no podrá iniciar sesión).

Usuarios de ejemplo (correos para `02_usuarios_demo.sql`; cada uno con su propia contraseña):

| Código | Rol | Correo |
|---|---|---|
| s001 | soporte | luana.ramirez@techsupport.com |
| s002 | soporte | viviana.gonzales@techsupport.com |
| s003 | soporte | stella.bernard@techsupport.com |
| u001 | usuario | laura.gomez@empresa.com |
| u002 | usuario | martin.perez@empresa.com |
| u003 | usuario | sofia.diaz@empresa.com |
| u004 | usuario | lucas.torres@empresa.com |
| u005 | usuario | camila.vega@empresa.com |

### 2.3 Variables de entorno
Copiar `.env.example` a `.env` y completar (Supabase → **Project Settings → API**):

```
SUPABASE_URL=https://xxxxxxxx.supabase.co
SUPABASE_PUBLISHABLE_KEY=sb_publishable_...
SUPABASE_SERVICE_ROLE_KEY=...        # clave SECRETA, solo en el servidor
```
`.env` está en `.gitignore`. **La clave `service_role` nunca debe subirse a git ni compartirse.**

Variables opcionales (todas tienen un valor por defecto):

| Variable | Para qué sirve | Por defecto |
|---|---|---|
| `TS_SESIONES` | `sqlite` guarda el contexto del modelo en `agent/.adk/sesiones_chat.db` y el historial visible en `agent/.adk/conversaciones.db`; `memoria` los pierde al reiniciar | `sqlite` |
| `TS_MAX_TURNOS` | Mensajes que el modelo recuerda de una conversación antes de seguir con contexto nuevo (la conversación visible no se corta) | `10` |
| `TS_MAX_CHAT_MIN` | Mensajes de chat por minuto y por usuario | `20` |
| `TS_EMBED_TIMEOUT` | Segundos de espera al generar embeddings con Ollama | `10` |

### 2.4 Dependencias y modelos
```powershell
env\Scripts\pip.exe install -r requirements.txt
ollama pull qwen3.5:4b
ollama pull nomic-embed-text
```

### 2.5 Ejecutar
```powershell
env\Scripts\uvicorn.exe main:app --host 127.0.0.1 --port 8001
```
Abrir **http://127.0.0.1:8001** e iniciar sesión con un usuario creado en Supabase. Si falta algún valor del `.env`, el servidor lo indica al arrancar.

### 2.6 Tests
```powershell
env\Scripts\python.exe -m unittest discover -s tests -p "test_*.py" -v
```
Los tests **no usan Supabase real**: corren con un repositorio en memoria (`TS_BACKEND=memoria`, datos ficticios de `dataset/usuarios_demo_memoria.json` y `dataset/tickets_iniciales.csv`) y con un cliente de Supabase simulado para probar el login, la verificación de tokens y el manejo de errores.

---

## 3. Estructura del proyecto

```text
├── agent/
│   ├── agent.py            # LiteLlm (qwen3.5:4b) y root_agent (sin PII en el prompt)
│   ├── data.py             # Catálogo/reglas/permisos + directorio de usuarios (desde la BD)
│   ├── repo.py             # Capa de datos: RepoSupabase (Auth + Postgres) y fábrica
│   ├── repo_memoria.py     # Repositorio en memoria (solo tests)
│   ├── limitador.py        # Límite de intentos (login y chat)
│   ├── conversaciones.py   # Historial de conversaciones por usuario (SQLite)
│   └── tools/tools.py      # 14 herramientas, preprocesar_contexto_seguro, clasificación y ChromaDB
├── archivos/               # Archivos de ejemplo (.log, .txt, .png); se suben a Storage con scripts/subir_archivos_demo.py
├── scripts/subir_archivos_demo.py
├── dataset/
│   ├── datos_iniciales.json         # Catálogo, reglas de clasificación, estados y permisos
│   ├── tickets_iniciales.csv        # Tickets de ejemplo (para tests)
│   └── usuarios_demo_memoria.json   # Usuarios FICTICIOS solo para tests
├── supabase/
│   ├── 01_esquema.sql      # Tablas, trigger de perfiles, RLS y funciones
│   ├── 02_usuarios_demo.sql
│   ├── 03_tickets_demo.sql
│   ├── 04_storage.sql      # Bucket privado de archivos
│   └── 05_concurrencia.sql # Detección de ediciones simultáneas
├── frontend/index.html     # Login + vistas Usuario y Soporte
├── tests/test_funcional_y_seguridad.py
├── main.py                 # FastAPI: autenticación por JWT de Supabase
├── .env.example
└── requirements.txt
```

---

## 4. Controles de seguridad

1. **Autenticación real**: JWT de Supabase Auth verificado en cada petición; sin JWT válido no hay acceso (ni con `usuario_id` suelto).
2. **Aislamiento de datos sensibles (Zero-PII en el agente)** y función previa `preprocesar_contexto_seguro`.
3. **Permisos por rol** verificados en cada herramienta (usuario: solo lo propio; soporte: todo + cambios de estado, prioridad y asignación) y **transiciones de estado** controladas.
4. **Tablas cerradas** en Supabase (RLS sin políticas, sin acceso para `anon`/`authenticated`).
5. **Clasificación basada en herramienta** (`consultar_reglas_clasificacion`) en lugar de reglas cableadas en el prompt.
6. **Defensas de entrada**: filtros anti SQLi y prompt injection, validación de archivos (extensión, tamaño, *magic bytes*, UTF-8), prevención de path traversal, cabeceras de seguridad y construcción segura del DOM en el frontend.
7. **Archivos en bucket privado** (Supabase Storage): cada usuario guarda y lee solo en su carpeta (`<codigo>/`), más los ejemplos de solo lectura en `compartidos/`; sin políticas para `anon`/`authenticated`, solo el backend accede.
8. **Trazabilidad atómica**: cada modificación y su evento de historial se guardan juntos.
9. **Zero-PII también en lo que vuelve al modelo**: títulos, descripciones, comentarios e historial se enmascaran antes de entregárselos al agente, vengan del chat o de la API. La interfaz sigue mostrando el texto original.
10. **Límite de intentos**: 5 contraseñas erróneas por usuario y dirección IP en 5 minutos (y 20 por usuario en 15 minutos), 30 pedidos de login por minuto por IP y 20 mensajes de chat por minuto por usuario. Responde `429` con `Retry-After`. Los contadores viven en memoria: se reinician con el servidor y no se comparten entre varios procesos.
11. **Filtros según el tipo de campo**: en identificadores, títulos y filtros se bloquea cualquier patrón SQL; en texto libre (mensajes, descripciones, comentarios y archivos) solo las formas típicas de ataque, para que un técnico pueda pegar un `SELECT` o un `/* */` en un reporte. Los datos viajan siempre como parámetros, nunca como SQL.
12. **Edición simultánea**: cada cambio indica qué valor tenía el ticket al leerlo y la base lo rechaza (`409`) si otra persona lo modificó antes.
13. **Conversaciones persistentes y aisladas**: cada usuario puede tener varias; se guardan en SQLite, sobreviven a recargar la página y reiniciar el servidor, y cada consulta filtra por usuario. El texto del usuario se guarda enmascarado (Zero-PII). El contexto del modelo se acota a `TS_MAX_TURNOS` mensajes.
14. **CSP con nonce**: los scripts y bloques de estilo solo se ejecutan con el nonce de cada respuesta. Solo se permite `style` en atributos (`style="…"`) del HTML.
15. **Catálogos sincronizados**: un test compara las categorías, prioridades y estados del JSON con los `CHECK` de `01_esquema.sql` para detectar desfases.
