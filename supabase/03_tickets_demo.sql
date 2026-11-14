insert into public.tickets (id, creado, solicitante, organizacion_id, area, titulo, descripcion, categoria, prioridad, estado, responsable, adjuntos) values (1050, '2026-10-20 09:15-03'::timestamptz, 'u001', 'org_central', 'Finanzas y Facturación', 'No puedo conectarme a la VPN', 'Desde ayer la VPN corporativa muestra error de autenticacion 691 al intentar conectarme desde mi notebook.', 'Redes y conectividad', 'Alta', 'En progreso', 's001', '{}'::text[]) on conflict (id) do nothing;
insert into public.ticket_historial (ticket_id, fecha, usuario, tipo, detalle) values (1050, '2026-10-20 09:15-03'::timestamptz, 'u001', 'Creacion', 'Ticket creado');
insert into public.ticket_historial (ticket_id, fecha, usuario, tipo, detalle) values (1050, '2026-10-20 09:40-03'::timestamptz, 's001', 'Cambio de estado', 'Abierto -> En revision');
insert into public.ticket_historial (ticket_id, fecha, usuario, tipo, detalle) values (1050, '2026-10-20 10:05-03'::timestamptz, 's001', 'Cambio de estado', 'En revision -> En progreso');

insert into public.tickets (id, creado, solicitante, organizacion_id, area, titulo, descripcion, categoria, prioridad, estado, responsable, adjuntos) values (1051, '2026-10-21 11:30-03'::timestamptz, 'u002', 'org_central', 'Administración y Compras', 'La impresora del piso 2 no imprime', 'La impresora enciende pero queda con luz roja y no toma trabajos de impresion del sector.', 'Hardware', 'Baja', 'Abierto', null, '{}'::text[]) on conflict (id) do nothing;
insert into public.ticket_historial (ticket_id, fecha, usuario, tipo, detalle) values (1051, '2026-10-21 11:30-03'::timestamptz, 'u002', 'Creacion', 'Ticket creado');

insert into public.tickets (id, creado, solicitante, organizacion_id, area, titulo, descripcion, categoria, prioridad, estado, responsable, adjuntos) values (1052, '2026-10-22 08:50-03'::timestamptz, 'u001', 'org_central', 'Finanzas y Facturación', 'Sistema de facturacion caido', 'El sistema de facturacion devuelve error 500 y nadie puede emitir facturas en el area.', 'Sistemas y aplicaciones', 'Critica', 'Pendiente', 's002', array['error_facturacion.log']::text[]) on conflict (id) do nothing;
insert into public.ticket_historial (ticket_id, fecha, usuario, tipo, detalle) values (1052, '2026-10-22 08:50-03'::timestamptz, 'u001', 'Creacion', 'Ticket creado');
insert into public.ticket_historial (ticket_id, fecha, usuario, tipo, detalle) values (1052, '2026-10-22 08:52-03'::timestamptz, 'u001', 'Archivo', 'Adjunto error_facturacion.log');
insert into public.ticket_historial (ticket_id, fecha, usuario, tipo, detalle) values (1052, '2026-10-22 09:00-03'::timestamptz, 's002', 'Cambio de estado', 'Abierto -> En revision');
insert into public.ticket_historial (ticket_id, fecha, usuario, tipo, detalle) values (1052, '2026-10-22 09:20-03'::timestamptz, 's002', 'Cambio de estado', 'En revision -> Pendiente');
insert into public.ticket_historial (ticket_id, fecha, usuario, tipo, detalle) values (1052, '2026-10-22 09:21-03'::timestamptz, 's002', 'Comentario', 'Esperando respuesta del proveedor de la base de datos.');

insert into public.tickets (id, creado, solicitante, organizacion_id, area, titulo, descripcion, categoria, prioridad, estado, responsable, adjuntos) values (1053, '2026-10-23 14:05-03'::timestamptz, 'u003', 'org_central', 'Recursos Humanos (RRHH)', 'Olvide mi contrasena del correo', 'No puedo ingresar al correo corporativo y necesito restablecer la clave.', 'Accesos y permisos', 'Media', 'Resuelto', 's001', '{}'::text[]) on conflict (id) do nothing;
insert into public.ticket_historial (ticket_id, fecha, usuario, tipo, detalle) values (1053, '2026-10-23 14:05-03'::timestamptz, 'u003', 'Creacion', 'Ticket creado');
insert into public.ticket_historial (ticket_id, fecha, usuario, tipo, detalle) values (1053, '2026-10-23 14:20-03'::timestamptz, 's001', 'Cambio de estado', 'Abierto -> En revision');
insert into public.ticket_historial (ticket_id, fecha, usuario, tipo, detalle) values (1053, '2026-10-23 14:30-03'::timestamptz, 's001', 'Cambio de estado', 'En revision -> En progreso');
insert into public.ticket_historial (ticket_id, fecha, usuario, tipo, detalle) values (1053, '2026-10-23 14:45-03'::timestamptz, 's001', 'Cambio de estado', 'En progreso -> Resuelto');

insert into public.tickets (id, creado, solicitante, organizacion_id, area, titulo, descripcion, categoria, prioridad, estado, responsable, adjuntos) values (1054, '2026-10-24 16:40-03'::timestamptz, 'u004', 'org_central', 'Operaciones y Logística', 'Servidor de archivos sin espacio', 'El servidor de archivos alcanzo el 98% de uso de disco en la carpeta de logistica.', 'Infraestructura', 'Alta', 'Cerrado', 's003', '{}'::text[]) on conflict (id) do nothing;
insert into public.ticket_historial (ticket_id, fecha, usuario, tipo, detalle) values (1054, '2026-10-24 16:40-03'::timestamptz, 'u004', 'Creacion', 'Ticket creado');
insert into public.ticket_historial (ticket_id, fecha, usuario, tipo, detalle) values (1054, '2026-10-24 17:00-03'::timestamptz, 's003', 'Cambio de estado', 'Abierto -> En revision');
insert into public.ticket_historial (ticket_id, fecha, usuario, tipo, detalle) values (1054, '2026-10-24 17:30-03'::timestamptz, 's003', 'Cambio de estado', 'En revision -> En progreso');
insert into public.ticket_historial (ticket_id, fecha, usuario, tipo, detalle) values (1054, '2026-10-25 09:00-03'::timestamptz, 's003', 'Cambio de estado', 'En progreso -> Resuelto');
insert into public.ticket_historial (ticket_id, fecha, usuario, tipo, detalle) values (1054, '2026-10-26 09:00-03'::timestamptz, 's003', 'Cambio de estado', 'Resuelto -> Cerrado');

select setval(pg_get_serial_sequence('public.tickets','id'), greatest((select max(id) from public.tickets), 1054));
