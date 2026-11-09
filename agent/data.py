CATEGORIAS = [
    "Hardware",
    "Software",
    "Redes y conectividad",
    "Sistemas y aplicaciones",
    "Accesos y permisos",
    "Infraestructura",
    "Otros",
]

PRIORIDADES = ["Baja", "Media", "Alta", "Critica"]

ESTADOS = ["Abierto", "En revision", "En progreso", "Pendiente", "Resuelto", "Cerrado"]

TRANSICIONES_PERMITIDAS = {
    "Abierto": ["En revision"],
    "En revision": ["En progreso", "Pendiente"],
    "En progreso": ["Pendiente", "Resuelto"],
    "Pendiente": ["En revision", "En progreso"],
    "Resuelto": ["Cerrado", "En progreso"],
    "Cerrado": [],
}

USUARIOS = {
    "u001": {"nombre": "Laura Gomez", "rol": "usuario", "email": "laura.gomez@cliente.test"},
    "u002": {"nombre": "Martin Perez", "rol": "usuario", "email": "martin.perez@cliente.test"},
    "u003": {"nombre": "Sofia Diaz", "rol": "usuario", "email": "sofia.diaz@cliente.test"},
    "s001": {"nombre": "Carlos Ruiz", "rol": "soporte", "email": "carlos.ruiz@techsupport.test"},
    "s002": {"nombre": "Ana Torres", "rol": "soporte", "email": "ana.torres@techsupport.test"},
}

PERMISOS_POR_ROL = {
    "usuario": {"consultar_propios", "comentar_propios", "adjuntar_propios", "crear"},
    "soporte": {
        "consultar_todos", "comentar_todos", "adjuntar_todos", "crear",
        "cambiar_estado", "cambiar_prioridad", "asignar",
    },
}


def _evento(fecha, usuario, tipo, detalle):
    return {"fecha": fecha, "usuario": usuario, "tipo": tipo, "detalle": detalle}


TICKETS = {
    1050: {
        "id": 1050,
        "creado": "2026-10-20 09:15",
        "solicitante": "u001",
        "titulo": "No puedo conectarme a la VPN",
        "descripcion": "Desde ayer la VPN corporativa muestra error de autenticacion.",
        "categoria": "Redes y conectividad",
        "prioridad": "Alta",
        "adjuntos": [],
        "estado": "En progreso",
        "responsable": "s001",
        "historial": [
            _evento("2026-10-20 09:15", "u001", "Creacion", "Ticket creado"),
            _evento("2026-10-20 09:40", "s001", "Cambio de estado", "Abierto -> En revision"),
            _evento("2026-10-20 10:05", "s001", "Cambio de estado", "En revision -> En progreso"),
        ],
    },
    1051: {
        "id": 1051,
        "creado": "2026-10-21 11:30",
        "solicitante": "u002",
        "titulo": "La impresora del piso 2 no imprime",
        "descripcion": "La impresora enciende pero queda con luz roja y no toma trabajos.",
        "categoria": "Hardware",
        "prioridad": "Baja",
        "adjuntos": [],
        "estado": "Abierto",
        "responsable": None,
        "historial": [
            _evento("2026-10-21 11:30", "u002", "Creacion", "Ticket creado"),
        ],
    },
    1052: {
        "id": 1052,
        "creado": "2026-10-22 08:50",
        "solicitante": "u001",
        "titulo": "Sistema de facturacion caido",
        "descripcion": "El sistema de facturacion devuelve error 500 y nadie puede emitir facturas.",
        "categoria": "Sistemas y aplicaciones",
        "prioridad": "Critica",
        "adjuntos": ["error_facturacion.log"],
        "estado": "Pendiente",
        "responsable": "s002",
        "historial": [
            _evento("2026-10-22 08:50", "u001", "Creacion", "Ticket creado"),
            _evento("2026-10-22 08:52", "u001", "Archivo", "Adjunto error_facturacion.log"),
            _evento("2026-10-22 09:00", "s002", "Cambio de estado", "Abierto -> En revision"),
            _evento("2026-10-22 09:20", "s002", "Cambio de estado", "En revision -> Pendiente"),
            _evento("2026-10-22 09:21", "s002", "Comentario", "Esperando respuesta del proveedor de la base de datos."),
        ],
    },
    1053: {
        "id": 1053,
        "creado": "2026-10-23 14:05",
        "solicitante": "u003",
        "titulo": "Olvide mi contrasena del correo",
        "descripcion": "No puedo ingresar al correo corporativo y necesito restablecer la clave.",
        "categoria": "Accesos y permisos",
        "prioridad": "Media",
        "adjuntos": [],
        "estado": "Resuelto",
        "responsable": "s001",
        "historial": [
            _evento("2026-10-23 14:05", "u003", "Creacion", "Ticket creado"),
            _evento("2026-10-23 14:20", "s001", "Cambio de estado", "Abierto -> En revision"),
            _evento("2026-10-23 14:30", "s001", "Cambio de estado", "En revision -> En progreso"),
            _evento("2026-10-23 14:45", "s001", "Cambio de estado", "En progreso -> Resuelto"),
        ],
    },
    1054: {
        "id": 1054,
        "creado": "2026-10-24 16:40",
        "solicitante": "u002",
        "titulo": "Servidor de archivos sin espacio",
        "descripcion": "El servidor de archivos alcanzo el 98% de uso de disco.",
        "categoria": "Infraestructura",
        "prioridad": "Alta",
        "adjuntos": [],
        "estado": "Cerrado",
        "responsable": "s002",
        "historial": [
            _evento("2026-10-24 16:40", "u002", "Creacion", "Ticket creado"),
            _evento("2026-10-24 17:00", "s002", "Cambio de estado", "Abierto -> En revision"),
            _evento("2026-10-24 17:30", "s002", "Cambio de estado", "En revision -> En progreso"),
            _evento("2026-10-25 09:00", "s002", "Cambio de estado", "En progreso -> Resuelto"),
            _evento("2026-10-26 09:00", "s002", "Cambio de estado", "Resuelto -> Cerrado"),
        ],
    },
}
