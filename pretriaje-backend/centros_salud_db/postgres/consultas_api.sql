-- ============================================================================
--  Llamadas que la API le hace a la base `centros_salud`.
--
--  Todas usan parámetros con nombre (:param), que es el formato que espera
--  SQLAlchemy con text(). Todos los filtros son opcionales: si el parámetro
--  llega en NULL, ese filtro no se aplica.
--
--  Uso desde SQLAlchemy:
--
--      from sqlalchemy import text
--      filas = db.session.execute(text(QUERY), {
--          'ciudad': None, 'especialidad': 'Odontología',
--          'lat': -31.7333, 'lon': -60.5238,
--          'hora': None, 'barrio': None, 'tipo': None,
--          'radio_km': None, 'limite': 50,
--      }).mappings().all()
--
--  Hay que pasar SIEMPRE todas las claves del diccionario, aunque vayan en
--  None: SQLAlchemy falla si falta una, no la asume NULL.
--
--  DOS COSAS QUE NO HAY QUE "SIMPLIFICAR" ------------------------------------
--
--  1) Los CAST(:param AS tipo) no son decorativos. Sin ellos psycopg2 manda un
--     NULL sin tipo y PostgreSQL corta con
--     "could not determine data type of parameter".
--
--  2) Va CAST(:param AS tipo) y NO la forma corta :param::tipo, que es la que
--     uno escribiría en psql. SQLAlchemy busca los parámetros con una regex
--     que descarta los ':' seguidos de otro ':' (para no romper los casts de
--     Postgres), así que en :ciudad::text el parámetro NUNCA se sustituye y
--     la consulta explota con "syntax error at or near :".
--     Esto sólo aparece al ejecutarla desde SQLAlchemy: en psql anda.
-- ============================================================================


-- ----------------------------------------------------------------------------
--  ANTES DE USAR: estado real de las columnas
-- ----------------------------------------------------------------------------
--  latitud/longitud -> 0 de 86 filas cargadas.
--      El orden por cercanía está escrito y es correcto, pero hasta que no se
--      geocodifiquen las direcciones `distancia_km` va a dar NULL en todas las
--      filas y el resultado va a salir ordenado por el criterio de respaldo
--      (ciudad, nombre). No devuelve error ni filas de menos.
--
--  horario          -> 6 de 86 filas cargadas (los CAPS de Paraná).
--      Por eso el filtro :hora NO descarta los centros con horario NULL: si
--      los descartara, una búsqueda con hora dejaría afuera 80 de 86 centros.
--      Los sin horario se devuelven con `horario_informado = false` para que
--      el front pueda aclararlo.
--
--  barrio           -> 50 de 86 filas cargadas.
-- ----------------------------------------------------------------------------

CREATE EXTENSION IF NOT EXISTS unaccent;   -- comparar texto ignorando tildes


-- ============================================================================
--  Q1 — BÚSQUEDA PRINCIPAL
--  Filtros: ciudad, especialidad, ubicación (lat/lon + radio), horario, barrio,
--           tipo. Todos aceptan NULL.
--  Orden:   por cercanía al punto recibido; si no hay punto o no hay
--           coordenadas, por ciudad y nombre.
-- ============================================================================

SELECT
    c.id,
    c.nombre,
    c.tipo,
    c.dependencia,
    c.ciudad,
    c.provincia,
    c.direccion,
    c.barrio,
    c.telefono,
    c.horario,
    (c.horario IS NOT NULL)                          AS horario_informado,
    c.latitud,
    c.longitud,

    -- distancia en km (Haversine). NULL si falta el punto de referencia
    -- o si el centro todavía no está geocodificado.
    CASE
        WHEN CAST(:lat AS double precision) IS NULL
          OR CAST(:lon AS double precision) IS NULL
          OR c.latitud  IS NULL
          OR c.longitud IS NULL
        THEN NULL
        ELSE round((6371 * acos(LEAST(1, GREATEST(-1,
                 cos(radians(CAST(:lat AS double precision))) * cos(radians(c.latitud))
               * cos(radians(c.longitud) - radians(CAST(:lon AS double precision)))
               + sin(radians(CAST(:lat AS double precision))) * sin(radians(c.latitud))
             ))))::numeric, 3)
    END                                              AS distancia_km,

    -- todas las especialidades del centro, aunque se haya filtrado por una
    COALESCE((
        SELECT array_agg(e.nombre ORDER BY e.nombre)
        FROM centro_especialidad ce
        JOIN especialidades e ON e.id = ce.especialidad_id
        WHERE ce.centro_id = c.id
    ), ARRAY[]::text[])                              AS especialidades

FROM centros c
WHERE
    -- ciudad ------------------------------------------------------------
    (CAST(:ciudad AS text) IS NULL
     OR unaccent(lower(c.ciudad)) = unaccent(lower(CAST(:ciudad AS text))))

    -- especialidad ------------------------------------------------------
    -- EXISTS y no JOIN: así el filtro no recorta la lista de especialidades
    -- que se devuelve por centro, y no duplica filas.
AND (CAST(:especialidad AS text) IS NULL
     OR EXISTS (
         SELECT 1
         FROM centro_especialidad ce
         JOIN especialidades e ON e.id = ce.especialidad_id
         WHERE ce.centro_id = c.id
           AND unaccent(lower(e.nombre)) LIKE
               '%' || unaccent(lower(CAST(:especialidad AS text))) || '%'
     ))

    -- tipo (hospital / CAPS / centro de salud / SAMCO / CIC) ------------
AND (CAST(:tipo AS text) IS NULL
     OR unaccent(lower(c.tipo)) = unaccent(lower(CAST(:tipo AS text))))

    -- barrio o distrito -------------------------------------------------
AND (CAST(:barrio AS text) IS NULL
     OR unaccent(lower(coalesce(c.barrio, ''))) LIKE
        '%' || unaccent(lower(CAST(:barrio AS text))) || '%')

    -- horario: ¿está abierto a esa hora? --------------------------------
    -- el campo es texto libre ("7:00 a 17:00"), se parsea con regex.
    -- Los centros sin horario informado NO se descartan (ver nota arriba).
AND (CAST(:hora AS time) IS NULL
     OR c.horario IS NULL
     OR (
         substring(c.horario from '^\s*(\d{1,2}:\d{2})')::time <= CAST(:hora AS time)
         AND substring(c.horario from 'a\s*(\d{1,2}:\d{2})')::time >= CAST(:hora AS time)
     ))

    -- radio: sólo centros dentro de X km del punto ----------------------
AND (CAST(:radio_km AS double precision) IS NULL
     OR CAST(:lat AS double precision) IS NULL
     OR CAST(:lon AS double precision) IS NULL
     OR (c.latitud IS NOT NULL AND c.longitud IS NOT NULL
         AND 6371 * acos(LEAST(1, GREATEST(-1,
                 cos(radians(CAST(:lat AS double precision))) * cos(radians(c.latitud))
               * cos(radians(c.longitud) - radians(CAST(:lon AS double precision)))
               + sin(radians(CAST(:lat AS double precision))) * sin(radians(c.latitud))
             ))) <= CAST(:radio_km AS double precision)))

ORDER BY
    distancia_km ASC NULLS LAST,   -- primero los más cercanos
    c.ciudad,                      -- respaldo cuando no hay coordenadas
    c.nombre
LIMIT COALESCE(CAST(:limite AS int), 100);


-- ============================================================================
--  Q2 — DETALLE DE UN CENTRO
-- ============================================================================

SELECT
    c.id, c.nombre, c.tipo, c.dependencia, c.ciudad, c.provincia,
    c.direccion, c.barrio, c.telefono, c.horario, c.latitud, c.longitud,
    c.fuente,
    COALESCE((
        SELECT array_agg(e.nombre ORDER BY e.nombre)
        FROM centro_especialidad ce
        JOIN especialidades e ON e.id = ce.especialidad_id
        WHERE ce.centro_id = c.id
    ), ARRAY[]::text[]) AS especialidades
FROM centros c
WHERE c.id = CAST(:id AS int);


-- ============================================================================
--  Q3 — CATÁLOGO: ciudades
--  Para poblar el desplegable del front.
-- ============================================================================

SELECT ciudad, provincia, count(*) AS centros
FROM centros
GROUP BY ciudad, provincia
ORDER BY centros DESC;


-- ============================================================================
--  Q4 — CATÁLOGO: especialidades
--  Opcionalmente acotado a una ciudad, para no ofrecer especialidades que
--  en esa ciudad no existen.
-- ============================================================================

SELECT e.nombre AS especialidad, count(DISTINCT c.id) AS centros
FROM especialidades e
JOIN centro_especialidad ce ON ce.especialidad_id = e.id
JOIN centros c              ON c.id = ce.centro_id
WHERE (CAST(:ciudad AS text) IS NULL
       OR unaccent(lower(c.ciudad)) = unaccent(lower(CAST(:ciudad AS text))))
GROUP BY e.nombre
ORDER BY centros DESC, e.nombre;


-- ============================================================================
--  Q5 — CATÁLOGO: tipos de efector
-- ============================================================================

SELECT tipo, count(*) AS centros
FROM centros
WHERE (CAST(:ciudad AS text) IS NULL
       OR unaccent(lower(ciudad)) = unaccent(lower(CAST(:ciudad AS text))))
GROUP BY tipo
ORDER BY centros DESC;


-- ============================================================================
--  Q6 — CATÁLOGO: barrios / distritos
-- ============================================================================

SELECT barrio, ciudad, count(*) AS centros
FROM centros
WHERE barrio IS NOT NULL
  AND (CAST(:ciudad AS text) IS NULL
       OR unaccent(lower(ciudad)) = unaccent(lower(CAST(:ciudad AS text))))
GROUP BY barrio, ciudad
ORDER BY ciudad, barrio;


-- ============================================================================
--  Q7 — EL MÁS CERCANO QUE ATIENDA UNA ESPECIALIDAD
--  Atajo de Q1 para "¿dónde me atiendo YA?".
-- ============================================================================

SELECT
    c.id, c.nombre, c.tipo, c.ciudad, c.direccion, c.telefono, c.horario,
    round((6371 * acos(LEAST(1, GREATEST(-1,
             cos(radians(CAST(:lat AS double precision))) * cos(radians(c.latitud))
           * cos(radians(c.longitud) - radians(CAST(:lon AS double precision)))
           + sin(radians(CAST(:lat AS double precision))) * sin(radians(c.latitud))
         ))))::numeric, 3) AS distancia_km
FROM centros c
WHERE c.latitud IS NOT NULL
  AND c.longitud IS NOT NULL
  AND (CAST(:especialidad AS text) IS NULL
       OR EXISTS (
           SELECT 1
           FROM centro_especialidad ce
           JOIN especialidades e ON e.id = ce.especialidad_id
           WHERE ce.centro_id = c.id
             AND unaccent(lower(e.nombre)) = unaccent(lower(CAST(:especialidad AS text)))
       ))
ORDER BY distancia_km ASC
LIMIT COALESCE(CAST(:limite AS int), 1);


-- ============================================================================
--  Q8 — CONTEO PARA PAGINAR
--  Mismos filtros que Q1, sin orden ni límite.
-- ============================================================================

SELECT count(*) AS total
FROM centros c
WHERE (CAST(:ciudad AS text) IS NULL
       OR unaccent(lower(c.ciudad)) = unaccent(lower(CAST(:ciudad AS text))))
  AND (CAST(:especialidad AS text) IS NULL
       OR EXISTS (
           SELECT 1
           FROM centro_especialidad ce
           JOIN especialidades e ON e.id = ce.especialidad_id
           WHERE ce.centro_id = c.id
             AND unaccent(lower(e.nombre)) LIKE
                 '%' || unaccent(lower(CAST(:especialidad AS text))) || '%'
       ))
  AND (CAST(:tipo AS text) IS NULL
       OR unaccent(lower(c.tipo)) = unaccent(lower(CAST(:tipo AS text))))
  AND (CAST(:barrio AS text) IS NULL
       OR unaccent(lower(coalesce(c.barrio, ''))) LIKE
          '%' || unaccent(lower(CAST(:barrio AS text))) || '%')
  AND (CAST(:hora AS time) IS NULL
       OR c.horario IS NULL
       OR (substring(c.horario from '^\s*(\d{1,2}:\d{2})')::time <= CAST(:hora AS time)
           AND substring(c.horario from 'a\s*(\d{1,2}:\d{2})')::time >= CAST(:hora AS time)));


-- ============================================================================
--  PENDIENTE PARA QUE LA CERCANÍA FUNCIONE
--
--  Hay que cargar latitud/longitud (hoy 0 de 86). Una vez geocodificadas,
--  estas consultas empiezan a ordenar por distancia sin cambiarles una coma.
--
--  Con 86 filas el cálculo Haversine a mano es instantáneo y no necesita nada
--  más. Si la tabla creciera a miles de filas, conviene:
--
--      CREATE EXTENSION cube;
--      CREATE EXTENSION earthdistance;
--      CREATE INDEX idx_centros_geo ON centros
--          USING gist (ll_to_earth(latitud, longitud));
--
--  y reemplazar el Haversine por earth_distance(ll_to_earth(...), ...),
--  que sí usa el índice.
-- ============================================================================
