import asyncio
import io
import json
import unittest
from pathlib import Path
from fastapi import HTTPException, UploadFile
from starlette.datastructures import Headers

import main
from agent.data import (
    DATASET_PATH,
    USUARIOS,
    autenticar_credenciales,
    cargar_dataset,
    hashear_password,
    usuario_publico,
    verificar_password,
)
from agent.tools.tools import (
    actualizar_estado,
    adjuntar_archivo,
    agregar_comentario,
    asignar_responsable,
    buscar_tickets,
    cambiar_prioridad,
    consultar_historial,
    consultar_ticket,
    crear_ticket,
    fijar_usuario_sesion,
    generar_resumen,
    leer_archivo,
    limpiar_usuario_sesion,
    listar_archivos,
)


class TestTechSupportFuncionalYSeguridad(unittest.TestCase):
    def setUp(self):
        cargar_dataset()
        main._tokens_activos.clear()

    def _login(self, identificador: str, password: str) -> tuple[str, dict]:
        res = main.iniciar_sesion(identificador=identificador, password=password)
        self.assertTrue(res["ok"])
        return res["token"], res["usuario"]

    # 1. PRUEBAS DE DATASET Y HASHING DE CONTRASENAS
    def test_01_dataset_sin_passwords_en_texto_plano_y_equipo_soporte(self):
        raw = json.loads(Path(DATASET_PATH).read_text(encoding="utf-8"))
        for uid, u in raw["usuarios"].items():
            self.assertNotIn("password", u)
            self.assertNotIn("credencial", u)
            self.assertIn("password_hash", u)
            self.assertTrue(
                u["password_hash"].startswith("pbkdf2_sha256$100000$"),
                f"Hash inseguro en {uid}",
            )

        self.assertEqual(USUARIOS["s001"]["nombre"], "Luana Ramirez")
        self.assertEqual(USUARIOS["s002"]["nombre"], "Viviana Gonzales")
        self.assertEqual(USUARIOS["s003"]["nombre"], "Stella Bernard")
        self.assertEqual(USUARIOS["s001"]["rol"], "soporte")
        self.assertEqual(USUARIOS["s002"]["rol"], "soporte")
        self.assertEqual(USUARIOS["s003"]["rol"], "soporte")

        pub = usuario_publico("s001")
        self.assertNotIn("password_hash", pub)

    def test_02_verificacion_pbkdf2_y_login_endpoints(self):
        h = hashear_password("ClaveSegura2026!", salt="sal_prueba")
        self.assertTrue(verificar_password("ClaveSegura2026!", h))
        self.assertFalse(verificar_password("OtraClave", h))

        self.assertIsNotNone(autenticar_credenciales("s001", "Luana2026!"))
        self.assertIsNotNone(
            autenticar_credenciales("viviana.gonzales@techsupport.com", "Viviana2026!")
        )
        self.assertIsNotNone(autenticar_credenciales("s003", "Stella2026!"))
        self.assertIsNone(autenticar_credenciales("s001", "incorrecta"))

        with self.assertRaises(HTTPException) as ctx:
            main.iniciar_sesion(identificador="s001", password="mal")
        self.assertEqual(ctx.exception.status_code, 401)

    # 2. PRUEBAS FUNCIONALES DE LAS TOOLS (F01 a F12)
    def test_03_ciclo_vida_ticket_estados_prioridad_asignacion_historial(self):
        # Crear ticket como u005 (Camila Vega - Comercial)
        res_crear = crear_ticket(
            usuario_id="u005",
            titulo="Error al abrir modulo de clientes en CRM",
            descripcion="Al hacer clic en la ficha de clientes aparece pantalla en blanco.",
            categoria="Sistemas y aplicaciones",
            prioridad="Media",
            confirmar_duplicado=True,
        )
        self.assertTrue(res_crear["ok"])
        tid = res_crear["ticket"]["id"]
        self.assertEqual(res_crear["ticket"]["area"], "Comercial y Atención al Cliente")
        self.assertEqual(res_crear["ticket"]["estado"], "Abierto")

        # F06: Soporte (Luana s001) revisa y sube la prioridad a Alta
        res_prio = cambiar_prioridad(tid, "s001", "Alta")
        self.assertTrue(res_prio["ok"])
        self.assertEqual(res_prio["ticket"]["prioridad"], "Alta")

        # Asignar a Viviana (s002) y luego reasignar a Stella (s003)
        res_asig1 = asignar_responsable(tid, "s001", "s002")
        self.assertTrue(res_asig1["ok"])
        res_asig2 = asignar_responsable(tid, "s002", "s003")
        self.assertTrue(res_asig2["ok"])
        self.assertEqual(res_asig2["ticket"]["responsable"], "s003")

        # F08: Transición inválida Abierto -> Resuelto debe fallar
        res_inv = actualizar_estado(tid, "s003", "Resuelto")
        self.assertFalse(res_inv["ok"])

        # Transiciones válidas: Abierto -> En revision -> En progreso -> Resuelto -> Cerrado
        for sig in ("En revision", "En progreso", "Resuelto", "Cerrado"):
            r_est = actualizar_estado(tid, "s003", sig)
            self.assertTrue(r_est["ok"], f"Fallo al pasar a {sig}: {r_est}")

        # Desde Cerrado no se puede volver a abrir
        res_cerrado = actualizar_estado(tid, "s003", "En progreso")
        self.assertFalse(res_cerrado["ok"])

        # F10 y F11: Comentario, adjunto e historial completo
        self.assertTrue(agregar_comentario(tid, "u005", "Confirmo que ya funciona.").get("ok"))
        self.assertTrue(adjuntar_archivo(tid, "u005", "reporte_red.txt").get("ok"))

        hist = consultar_historial(tid, "u005")
        self.assertTrue(hist["ok"])
        tipos_eventos = [e["tipo"] for e in hist["historial"]]
        self.assertIn("Creacion", tipos_eventos)
        self.assertIn("Cambio de prioridad", tipos_eventos)
        self.assertIn("Asignacion", tipos_eventos)
        self.assertIn("Reasignacion", tipos_eventos)
        self.assertIn("Cambio de estado", tipos_eventos)
        self.assertIn("Comentario", tipos_eventos)
        self.assertIn("Archivo", tipos_eventos)

    def test_04_lectura_archivos_multimodales_y_resumen_f12(self):
        lista = listar_archivos()
        self.assertTrue(lista["ok"])
        self.assertIn("error_facturacion.log", lista["archivos"])
        self.assertIn("reporte_red.txt", lista["archivos"])

        log_res = leer_archivo("error_facturacion.log")
        self.assertTrue(log_res["ok"])
        self.assertIn("SQLTransientConnectionException", log_res["contenido"])

        img_res = leer_archivo("captura_error_vpn.png")
        self.assertFalse(img_res["ok"])
        self.assertIn("Limitacion", img_res["error"])

        resumen = generar_resumen("s001")
        self.assertTrue(resumen["ok"])
        self.assertEqual(resumen["total"], 5)
        self.assertIn("Finanzas y Facturación", resumen["por_area"])

        b_area = buscar_tickets(usuario_id="s001", area="Finanzas")
        self.assertTrue(b_area["ok"])
        self.assertEqual(b_area["cantidad"], 2)

    # 3. AUDITORIA DE SEGURIDAD Y PERMISOS
    def test_05_control_acceso_por_rol_y_aislamiento_entre_usuarios(self):
        # u001 (Laura) no puede consultar ni comentar el ticket 1051 de u002 (Martin)
        self.assertFalse(consultar_ticket(1051, "u001")["ok"])
        self.assertFalse(agregar_comentario(1051, "u001", "Intento espiar")["ok"])

        # u001 no puede cambiar estado, prioridad ni asignar responsables
        self.assertFalse(actualizar_estado(1050, "u001", "Resuelto")["ok"])
        self.assertFalse(cambiar_prioridad(1050, "u001", "Critica")["ok"])
        self.assertFalse(asignar_responsable(1050, "u001", "s001")["ok"])

        # En la API REST, sin token ni usuario da 401 (ya no usa s001 por defecto)
        with self.assertRaises(HTTPException) as ctx_anon:
            main.listar_tickets_visibles(usuario_id=None, authorization=None)
        self.assertEqual(ctx_anon.exception.status_code, 401)

        # Con token de u001 solo ve sus propios tickets (1050 y 1052)
        tok_u001, _ = self._login("u001", "Laura2026!")
        r_tkts = main.listar_tickets_visibles(authorization=f"Bearer {tok_u001}")
        ids_visibles = {t["id"] for t in r_tkts["tickets"]}
        self.assertEqual(ids_visibles, {1050, 1052})

        # Si u001 intenta pedir /tickets/1051 con su token, recibe 403
        with self.assertRaises(HTTPException) as ctx_proh:
            main.detalle_ticket(ticket_id=1051, authorization=f"Bearer {tok_u001}")
        self.assertEqual(ctx_proh.exception.status_code, 403)

        # Si u001 intenta enviar usuario_id="s001" con su token de u001, recibe 403
        with self.assertRaises(HTTPException) as ctx_spoof:
            main.listar_tickets_visibles(
                usuario_id="s001", authorization=f"Bearer {tok_u001}"
            )
        self.assertEqual(ctx_spoof.exception.status_code, 403)

    def test_06_bloqueo_suplantacion_usuario_por_prompt_injection(self):
        # Simulamos que la sesión activa en /chat es de u001, pero el LLM intenta llamar tools como s001
        token_ctx = fijar_usuario_sesion("u001")
        try:
            res_suplantado = consultar_ticket(1051, "s001")
            self.assertFalse(res_suplantado["ok"])
            self.assertIn("bloqueada por seguridad", res_suplantado["error"])

            res_estado = actualizar_estado(1050, "s001", "Resuelto")
            self.assertFalse(res_estado["ok"])
        finally:
            limpiar_usuario_sesion(token_ctx)

    def test_07_bloqueo_sqli_path_traversal_y_archivos_maliciosos(self):
        # SQL Injection bloqueado
        res_sqli = agregar_comentario(
            1050, "u001", "Hola ' OR '1'='1'; DROP TABLE tickets; --"
        )
        self.assertFalse(res_sqli["ok"])
        self.assertIn("inyeccion SQL", res_sqli["error"])

        # Path traversal en leer_archivo y adjuntar_archivo
        self.assertFalse(leer_archivo("../../main.py")["ok"])
        self.assertFalse(leer_archivo("..\\dataset\\datos_iniciales.json")["ok"])
        self.assertFalse(adjuntar_archivo(1050, "u001", "../../evil.sh")["ok"])
        self.assertFalse(adjuntar_archivo(1050, "u001", "malware.exe")["ok"])

        tok_u001, _ = self._login("u001", "Laura2026!")

        # Subida de archivo con extensión peligrosa (.exe) en /chat
        up_exe = UploadFile(
            filename="script.exe",
            file=io.BytesIO(b"MZfake"),
            headers=Headers({"content-type": "application/octet-stream"}),
        )
        res_exe = asyncio.run(
            main.chat_con_agente(
                mensaje="Subo ejecutable",
                archivo=up_exe,
                authorization=f"Bearer {tok_u001}",
            )
        )
        self.assertFalse(res_exe["ok"])
        self.assertIn("no soportado", res_exe["respuesta"])

        # Imagen .png falsificada (sin magic bytes PNG reales)
        up_fake_png = UploadFile(
            filename="falso.png",
            file=io.BytesIO(b"<script>alert(1)</script>"),
            headers=Headers({"content-type": "image/png"}),
        )
        res_fake_png = asyncio.run(
            main.chat_con_agente(
                mensaje="Subo png falso",
                archivo=up_fake_png,
                authorization=f"Bearer {tok_u001}",
            )
        )
        self.assertFalse(res_fake_png["ok"])
        self.assertIn("imagen valida", res_fake_png["respuesta"])

    def test_08_ausencia_xss_y_vistas_en_frontend(self):
        html = Path(main.FRONTEND_FILE).read_text(encoding="utf-8")
        self.assertNotIn("innerHTML = `", html)
        self.assertIn("VISTA CONSOLA SOPORTE TI", html)
        self.assertIn("VISTA PORTAL USUARIO", html)
        self.assertIn("Luana2026!", html)
        self.assertIn("Viviana2026!", html)
        self.assertIn("Stella2026!", html)


if __name__ == "__main__":
    unittest.main(verbosity=2)
