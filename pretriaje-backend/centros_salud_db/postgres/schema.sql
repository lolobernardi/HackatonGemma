-- Esquema PostgreSQL: centros de salud de Paraná (Entre Ríos)
-- y Santa Fe capital (Santa Fe).

-- para comparar texto ignorando tildes (unaccent('Paraná') = 'Parana')
CREATE EXTENSION IF NOT EXISTS unaccent;

DROP VIEW  IF EXISTS vw_centros_especialidades;
DROP TABLE IF EXISTS centro_especialidad;
DROP TABLE IF EXISTS especialidades;
DROP TABLE IF EXISTS centros;

CREATE TABLE centros (
    id          serial PRIMARY KEY,
    nombre      text NOT NULL,
    tipo        text NOT NULL,   -- centro de salud | CAPS | hospital | CIC | SAMCO | ...
    dependencia text NOT NULL,   -- provincial | municipal
    ciudad      text NOT NULL,
    provincia   text NOT NULL,
    direccion   text NOT NULL,
    barrio      text,            -- barrio o distrito municipal
    telefono    text,
    horario     text,
    latitud     double precision,   -- sin geocodificar todavía
    longitud    double precision,
    fuente      text NOT NULL,
    UNIQUE (nombre, ciudad)
);

CREATE TABLE especialidades (
    id     serial PRIMARY KEY,
    nombre text NOT NULL UNIQUE
);

CREATE TABLE centro_especialidad (
    centro_id       integer NOT NULL REFERENCES centros(id)        ON DELETE CASCADE,
    especialidad_id integer NOT NULL REFERENCES especialidades(id) ON DELETE CASCADE,
    PRIMARY KEY (centro_id, especialidad_id)
);

CREATE INDEX idx_centros_ciudad  ON centros (ciudad);
CREATE INDEX idx_centros_tipo    ON centros (tipo);
CREATE INDEX idx_ce_especialidad ON centro_especialidad (especialidad_id);

-- Búsqueda por nombre sin distinguir tildes ni mayúsculas.
-- unaccent() necesita la extensión; si no está disponible, este índice se omite.
CREATE INDEX idx_centros_nombre_lower ON centros (lower(nombre));

-- Una fila por par centro/especialidad: es la vista que conviene consultar.
CREATE VIEW vw_centros_especialidades AS
SELECT c.id     AS centro_id,
       c.nombre AS centro,
       c.tipo,
       c.dependencia,
       c.ciudad,
       c.provincia,
       c.direccion,
       c.barrio,
       c.telefono,
       c.horario,
       e.nombre AS especialidad
FROM centros c
LEFT JOIN centro_especialidad ce ON ce.centro_id = c.id
LEFT JOIN especialidades e       ON e.id = ce.especialidad_id;
