-- Evita que dos personas de soporte se pisen al editar el mismo ticket.
--
-- La funcion recibe ademas los valores que el ticket tenia cuando se lo leyo (p_esperado).
-- Bloquea la fila, compara con lo que hay ahora y, si otra persona lo cambio mientras tanto,
-- rechaza el cambio con el error 'conflicto_concurrencia'. Ejecutar una sola vez, despues de 01.

drop function if exists public.aplicar_cambio_ticket(bigint, jsonb, text, text, text);

create or replace function public.aplicar_cambio_ticket(
  p_ticket_id bigint, p_campos jsonb, p_usuario text, p_tipo text, p_detalle text,
  p_esperado jsonb default '{}'::jsonb)
returns jsonb
language plpgsql
set search_path = ''
as $$
declare
  v_actual public.tickets;
  v_evento public.ticket_historial;
begin
  select * into v_actual from public.tickets where id = p_ticket_id for update;
  if not found then
    raise exception 'ticket_inexistente';
  end if;

  if (p_esperado ? 'estado'      and (p_esperado ->> 'estado')      is distinct from v_actual.estado)
  or (p_esperado ? 'prioridad'   and (p_esperado ->> 'prioridad')   is distinct from v_actual.prioridad)
  or (p_esperado ? 'responsable' and (p_esperado ->> 'responsable') is distinct from v_actual.responsable)
  or (p_esperado ? 'adjuntos'
      and array(select jsonb_array_elements_text(p_esperado -> 'adjuntos')) is distinct from v_actual.adjuntos)
  then
    raise exception 'conflicto_concurrencia';
  end if;

  update public.tickets t set
    estado      = coalesce(p_campos ->> 'estado', t.estado),
    prioridad   = coalesce(p_campos ->> 'prioridad', t.prioridad),
    responsable = case when p_campos ? 'responsable' then p_campos ->> 'responsable' else t.responsable end,
    adjuntos    = case when p_campos ? 'adjuntos'
                       then array(select jsonb_array_elements_text(p_campos -> 'adjuntos'))
                       else t.adjuntos end
  where t.id = p_ticket_id;

  insert into public.ticket_historial (ticket_id, usuario, tipo, detalle)
  values (p_ticket_id, p_usuario, p_tipo, p_detalle)
  returning * into v_evento;

  return to_jsonb(v_evento);
end;
$$;

revoke all on function public.aplicar_cambio_ticket(bigint, jsonb, text, text, text, jsonb)
  from public, anon, authenticated;
grant execute on function public.aplicar_cambio_ticket(bigint, jsonb, text, text, text, jsonb)
  to service_role;
