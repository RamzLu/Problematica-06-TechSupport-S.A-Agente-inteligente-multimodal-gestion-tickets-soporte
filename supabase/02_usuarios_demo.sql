update public.profiles set codigo = 'tmp_' || replace(id::text, '-', '')
where email in (
  'luana.ramirez@techsupport.com','viviana.gonzales@techsupport.com','stella.bernard@techsupport.com',
  'laura.gomez@empresa.com','martin.perez@empresa.com','sofia.diaz@empresa.com',
  'lucas.torres@empresa.com','camila.vega@empresa.com');

update public.profiles p set
  codigo = v.codigo, nombre = v.nombre, rol = v.rol, area = v.area, cargo = v.cargo
from (values
  ('luana.ramirez@techsupport.com',   's001', 'Luana Ramirez',   'soporte', 'Soporte TI - Redes y Ciberseguridad (N2)',     'Especialista en Redes y Conectividad'),
  ('viviana.gonzales@techsupport.com','s002', 'Viviana Gonzales','soporte', 'Soporte TI - Sistemas y Bases de Datos (N3)', 'Especialista en Sistemas y Aplicaciones'),
  ('stella.bernard@techsupport.com',  's003', 'Stella Bernard',  'soporte', 'Soporte TI - Infraestructura y Hardware (N2)','Especialista en Infraestructura y Equipos'),
  ('laura.gomez@empresa.com',         'u001', 'Laura Gomez',     'usuario', 'Finanzas y Facturación',                      'Analista de Facturación'),
  ('martin.perez@empresa.com',        'u002', 'Martin Perez',    'usuario', 'Administración y Compras',                    'Coordinador Administrativo'),
  ('sofia.diaz@empresa.com',          'u003', 'Sofia Diaz',      'usuario', 'Recursos Humanos (RRHH)',                     'Analista de RRHH'),
  ('lucas.torres@empresa.com',        'u004', 'Lucas Torres',    'usuario', 'Operaciones y Logística',                     'Supervisor de Depósito'),
  ('camila.vega@empresa.com',         'u005', 'Camila Vega',     'usuario', 'Comercial y Atención al Cliente',             'Ejecutiva de Cuentas')
) as v(email, codigo, nombre, rol, area, cargo)
where p.email = v.email;

select codigo, nombre, rol, area from public.profiles order by codigo;
