-- Consultas de ejemplo. Correr con:
--   psql -h 127.0.0.1 -p 5438 -U postgres -d centros_salud -f postgres/consultas.sql
--
-- Este archivo está en UTF-8 y psql lo lee bien. Conviene consultar desde
-- archivo en lugar de pasar -c "..." con tildes: PowerShell 5.1 codifica los
-- argumentos en ANSI y el servidor los rechaza (ver README).

\echo '== centros por ciudad =='
SELECT ciudad, provincia, count(*) AS centros
FROM centros
GROUP BY ciudad, provincia
ORDER BY centros DESC;

\echo ''
\echo '== especialidades mas ofrecidas =='
SELECT e.nombre AS especialidad, count(ce.centro_id) AS centros
FROM especialidades e
LEFT JOIN centro_especialidad ce ON ce.especialidad_id = e.id
GROUP BY e.nombre
ORDER BY centros DESC, e.nombre
LIMIT 15;

\echo ''
\echo '== donde atienden odontologia en Parana =='
SELECT centro, direccion, telefono
FROM vw_centros_especialidades
WHERE especialidad = 'Odontología'
  AND ciudad = 'Paraná'
ORDER BY centro;

\echo ''
\echo '== hospitales de Santa Fe con sus especialidades =='
SELECT centro, direccion, string_agg(especialidad, ', ' ORDER BY especialidad) AS especialidades
FROM vw_centros_especialidades
WHERE ciudad = 'Santa Fe' AND tipo = 'hospital'
GROUP BY centro, direccion
ORDER BY centro;

\echo ''
\echo '== centros sin especialidades cargadas (pendiente de completar) =='
SELECT c.nombre, c.ciudad, c.direccion
FROM centros c
LEFT JOIN centro_especialidad ce ON ce.centro_id = c.id
WHERE ce.centro_id IS NULL
ORDER BY c.ciudad, c.nombre;

\echo ''
\echo '== busqueda por texto, sin distinguir tildes ni mayusculas =='
-- 'pediatr' encuentra 'Pediatría'; para ignorar tildes se compara la forma
-- normalizada con translate()
SELECT DISTINCT centro, ciudad, direccion
FROM vw_centros_especialidades
WHERE translate(lower(especialidad), 'áéíóúñ', 'aeioun') LIKE '%pediatr%'
  AND translate(lower(ciudad),       'áéíóúñ', 'aeioun') = 'parana'
ORDER BY ciudad, centro;
